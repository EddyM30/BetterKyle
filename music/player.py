"""Wavelink integration for one player, one queue, and two playback modes."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
import logging
from urllib.parse import urlparse

import aiohttp
import discord
import wavelink

from config import (
    LAVALINK_CONNECT_RETRIES,
    LAVALINK_PASSWORD,
    LAVALINK_URI,
    MUSIC_IDLE_TIMEOUT_SECONDS,
    SPOTIFY_RESOLVE_CONCURRENCY,
)
from music.helpers import is_spotify_url, track_label
from music.radio_stations import RadioStation
from music.state import MusicState


LOGGER = logging.getLogger(__name__)


class MusicError(RuntimeError):
    """Base class for clean user-facing music errors."""


class MusicUnavailableError(MusicError):
    """Raised when Lavalink is not configured or connected."""


class VoiceChannelRequiredError(MusicError):
    """Raised when a requester is not in voice."""


class VoiceChannelConflictError(MusicError):
    """Raised rather than unexpectedly moving an existing player."""


class NoTracksFoundError(MusicError):
    """Raised when Lavalink cannot resolve a query to playable audio."""


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    """Summary returned to ``/play`` after resolution and queue insertion."""

    added: int
    failed: int
    started: bool
    collection_name: str | None
    first_track_label: str
    spotify: bool


class MusicController:
    """Own BetterKyle's single Wavelink node/player state."""

    def __init__(self, client: discord.Client) -> None:
        self.client = client
        self.state = MusicState()
        self.node: wavelink.Node | None = None
        self._node_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._node_task: asyncio.Task[bool] | None = None
        self._node_session: aiohttp.ClientSession | None = None
        self._playback_token_counter = 0
        self._reported_music_failures: deque[str] = deque(maxlen=256)

    @property
    def node_connected(self) -> bool:
        """Return whether the configured Lavalink node is currently usable."""

        return (
            self.node is not None and self.node.status is wavelink.NodeStatus.CONNECTED
        )

    def start_node_connection(self) -> None:
        """Connect in the background so League startup never waits on Lavalink."""

        if self._node_task is None or self._node_task.done():
            self._node_task = asyncio.create_task(
                self.connect_lavalink(),
                name="betterkyle-lavalink-connect",
            )

    async def connect_lavalink(self) -> bool:
        """Make a bounded node connection attempt and log a clean failure."""

        if not LAVALINK_PASSWORD:
            LOGGER.warning(
                "LAVALINK_PASSWORD is unset; League is active but music is unavailable"
            )
            return False

        async with self._node_lock:
            if self.node_connected:
                return True

            try:
                if self.node is None:
                    self._node_session = aiohttp.ClientSession()
                    self.node = wavelink.Node(
                        identifier="betterkyle",
                        uri=LAVALINK_URI,
                        password=LAVALINK_PASSWORD,
                        session=self._node_session,
                        retries=LAVALINK_CONNECT_RETRIES,
                        inactive_player_timeout=MUSIC_IDLE_TIMEOUT_SECONDS,
                        inactive_channel_tokens=None,
                    )

                # Pool.connect only retains nodes that connected successfully.
                # A node that exhausted its bounded startup retries is therefore
                # absent from the Pool and must be registered again when a later
                # command retries after Lavalink has recovered.
                if self.node.identifier in wavelink.Pool.nodes:
                    await wavelink.Pool.reconnect()
                else:
                    await wavelink.Pool.connect(
                        nodes=[self.node],
                        client=self.client,
                    )
            except Exception:
                LOGGER.exception(
                    "Unexpected Lavalink connection failure at %s; "
                    "League remains fully active",
                    LAVALINK_URI,
                )
                return False

            if not self.node_connected:
                LOGGER.warning(
                    "Lavalink is unavailable at %s; League remains fully active",
                    LAVALINK_URI,
                )
                return False

            LOGGER.info("Connected to Lavalink at %s", LAVALINK_URI)
            return True

    async def _require_node(self) -> wavelink.Node:
        if not self.node_connected and not await self.connect_lavalink():
            raise MusicUnavailableError(
                "Music is temporarily unavailable because Lavalink is not connected."
            )
        assert self.node is not None
        return self.node

    async def ensure_player(self, interaction: discord.Interaction) -> wavelink.Player:
        """Join the requester's channel or reuse the sole existing player."""

        voice_state = getattr(interaction.user, "voice", None)
        requested_channel = getattr(voice_state, "channel", None)
        if requested_channel is None:
            raise VoiceChannelRequiredError(
                "Join a voice channel before using music or radio."
            )
        if interaction.guild is None:
            raise MusicError("Music commands can only be used in BetterKyle's server.")

        existing = interaction.guild.voice_client
        if existing is not None:
            if not isinstance(existing, wavelink.Player):
                raise MusicUnavailableError(
                    "The existing voice connection is unavailable."
                )
            if existing.channel is None or existing.channel.id != requested_channel.id:
                channel_name = getattr(existing.channel, "name", "another channel")
                raise VoiceChannelConflictError(
                    f"BetterKyle is already playing in **{channel_name}**."
                )
            self.state.attach_player(existing)
            return existing

        await self._require_node()
        connected = await requested_channel.connect(
            cls=wavelink.Player,
            self_deaf=True,
        )
        if not isinstance(connected, wavelink.Player):
            raise MusicUnavailableError("Discord did not create a Wavelink player.")

        connected.autoplay = wavelink.AutoPlayMode.partial
        self.state.attach_player(connected)
        await connected.set_volume(self.state.volume)
        # Wavelink does not start its initial idle countdown until a track has
        # played unless this property is explicitly reset after connecting.
        connected.inactive_timeout = MUSIC_IDLE_TIMEOUT_SECONDS
        LOGGER.info("Joined Discord voice channel %s", requested_channel)
        return connected

    @staticmethod
    def _query_is_url(query: str) -> bool:
        parsed = urlparse(query.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _search(self, query: str, node: wavelink.Node) -> wavelink.Search:
        if self._query_is_url(query):
            return await wavelink.Playable.search(query, node=node)
        return await wavelink.Playable.search(
            query,
            source=wavelink.TrackSource.SoundCloud,
            node=node,
        )

    @staticmethod
    def _loaded_tracks(
        loaded: wavelink.Search,
    ) -> tuple[list[wavelink.Playable], str | None]:
        if isinstance(loaded, wavelink.Playlist):
            return list(loaded.tracks), loaded.name
        return list(loaded[:1]), None

    def _set_track_metadata(
        self,
        track: wavelink.Playable,
        *,
        interaction: discord.Interaction,
        origin_source: str,
        display_title: str | None = None,
        display_author: str | None = None,
    ) -> str:
        self._playback_token_counter += 1
        playback_token = f"betterkyle-{self._playback_token_counter}"
        track.extras = {
            "requester_id": interaction.user.id,
            "requester_name": getattr(
                interaction.user, "display_name", str(interaction.user)
            ),
            "request_channel_id": interaction.channel_id or 0,
            "origin_source": origin_source,
            "display_title": display_title or track.title,
            "display_author": display_author or track.author,
            "playback_token": playback_token,
        }
        return playback_token

    @staticmethod
    def _playback_token(track: wavelink.Playable | None) -> str | None:
        if track is None:
            return None
        return getattr(track.extras, "playback_token", None)

    def _is_active_radio_track(self, track: wavelink.Playable) -> bool:
        """Reject delayed events from a track replaced during a mode switch."""

        token = self._playback_token(track)
        return (
            self.state.is_radio
            and token is not None
            and token == self.state.radio_playback_token
        )

    def _is_current_track(
        self,
        player: wavelink.Player,
        event_track: wavelink.Playable,
    ) -> bool:
        """Match an event to the current generation of the active playable."""

        current = player.current
        if current is None:
            return False
        return self._tracks_match(event_track, current)

    def _is_loaded_track(
        self,
        player: wavelink.Player,
        event_track: wavelink.Playable,
    ) -> bool:
        """Match TrackEnd after Wavelink has already cleared ``current``."""

        loaded = player.queue.loaded
        if loaded is None:
            return False
        return self._tracks_match(event_track, loaded)

    def _tracks_match(
        self,
        event_track: wavelink.Playable,
        active_track: wavelink.Playable,
    ) -> bool:
        """Compare BetterKyle generations, falling back to encoded identity."""

        event_token = self._playback_token(event_track)
        active_token = self._playback_token(active_track)
        if event_token is not None and active_token is not None:
            return event_token == active_token
        return event_track.encoded == active_track.encoded

    async def _resolve_spotify_tracks(
        self,
        metadata_tracks: list[wavelink.Playable],
        interaction: discord.Interaction,
        node: wavelink.Node,
    ) -> tuple[list[wavelink.Playable], int]:
        """Resolve Spotify metadata to SoundCloud audio with bounded concurrency."""

        semaphore = asyncio.Semaphore(SPOTIFY_RESOLVE_CONCURRENCY)

        async def resolve_one(metadata: wavelink.Playable) -> wavelink.Playable | None:
            async with semaphore:
                query = f"{metadata.title} {metadata.author}"
                try:
                    loaded = await wavelink.Playable.search(
                        query,
                        source=wavelink.TrackSource.SoundCloud,
                        node=node,
                    )
                except wavelink.WavelinkException:
                    LOGGER.warning(
                        "SoundCloud resolution failed for Spotify track %s",
                        metadata.title,
                        exc_info=True,
                    )
                    return None

                tracks, _ = self._loaded_tracks(loaded)
                if not tracks:
                    return None

                playable = tracks[0]
                self._set_track_metadata(
                    playable,
                    interaction=interaction,
                    origin_source="Spotify → SoundCloud",
                    display_title=metadata.title,
                    display_author=metadata.author,
                )
                return playable

        resolved = await asyncio.gather(
            *(resolve_one(track) for track in metadata_tracks)
        )
        successful = [track for track in resolved if track is not None]
        return successful, len(metadata_tracks) - len(successful)

    async def play_query(
        self,
        interaction: discord.Interaction,
        query: str,
    ) -> PlayOutcome:
        """Resolve one query and start or enqueue its playable tracks."""

        async with self._operation_lock:
            player = await self.ensure_player(interaction)
            node = await self._require_node()
            switched_from_radio = self.state.is_radio

            try:
                loaded = await self._search(query.strip(), node)
            except wavelink.WavelinkException as exc:
                LOGGER.exception("Music resolution failed for a /play request")
                raise NoTracksFoundError(
                    "That request could not be resolved to playable audio."
                ) from exc

            metadata_tracks, collection_name = self._loaded_tracks(loaded)
            spotify = is_spotify_url(query)
            if spotify:
                tracks, failed = await self._resolve_spotify_tracks(
                    metadata_tracks,
                    interaction,
                    node,
                )
            else:
                tracks = metadata_tracks
                failed = 0
                for track in tracks:
                    source = (
                        "SoundCloud" if track.source == "soundcloud" else track.source
                    )
                    self._set_track_metadata(
                        track,
                        interaction=interaction,
                        origin_source=source,
                    )

            if not tracks:
                raise NoTracksFoundError(
                    "No playable tracks were found for that request."
                )

            if switched_from_radio:
                # Replace the live stream atomically with the first resolved
                # track. A separate skip can deliver its TrackEnd after the new
                # track starts, causing Wavelink to clear the new current item.
                player.autoplay = wavelink.AutoPlayMode.disabled
                player.queue.reset()
                self.state.enter_music_mode()

            player.autoplay = wavelink.AutoPlayMode.partial
            should_start = switched_from_radio or player.current is None
            if should_start:
                first, *remaining = tracks
                if remaining:
                    await player.queue.put_wait(remaining)
                await player.play(
                    first,
                    replace=True,
                    volume=self.state.volume,
                    paused=False,
                )
            else:
                await player.queue.put_wait(tracks)

            self.state.enter_music_mode()
            return PlayOutcome(
                added=len(tracks),
                failed=failed,
                started=should_start,
                collection_name=collection_name,
                first_track_label=track_label(tracks[0]),
                spotify=spotify,
            )

    async def start_radio(
        self,
        interaction: discord.Interaction,
        station: RadioStation,
    ) -> None:
        """Replace normal playback with one configured direct HTTP stream."""

        async with self._operation_lock:
            player = await self.ensure_player(interaction)
            node = await self._require_node()

            try:
                loaded = await wavelink.Playable.search(station.stream_url, node=node)
            except wavelink.WavelinkException as exc:
                LOGGER.exception("Radio stream resolution failed for %s", station.key)
                raise NoTracksFoundError(
                    f"{station.name} is unavailable right now. Please try again later."
                ) from exc

            tracks, _ = self._loaded_tracks(loaded)
            if not tracks:
                raise NoTracksFoundError(
                    f"{station.name} is unavailable right now. Please try again later."
                )

            track = tracks[0]
            playback_token = self._set_track_metadata(
                track,
                interaction=interaction,
                origin_source="Live Radio",
                display_title=station.name,
                display_author=f"{station.frequency} {station.callsign}",
            )
            # Resolve first, then atomically replace the current playable. This
            # avoids a delayed stop event racing and clearing the new stream.
            player.autoplay = wavelink.AutoPlayMode.disabled
            player.queue.reset()
            self.state.enter_radio_mode(
                station,
                interaction.channel_id or 0,
                playback_token,
            )
            try:
                await player.play(
                    track,
                    replace=True,
                    volume=self.state.volume,
                    paused=False,
                )
            except Exception:
                self.state.enter_music_mode()
                LOGGER.exception("Radio playback failed for %s", station.key)
                raise

            LOGGER.info("Streaming radio station %s", station.name)

    def active_player(self) -> wavelink.Player | None:
        """Return the connected player, discarding a stale reference."""

        player = self.state.player
        if player is None:
            return None
        if not player.connected:
            self.state.reset()
            return None
        return player

    async def stop(self) -> bool:
        """Stop playback, clear both modes/queue, and remain connected."""

        async with self._operation_lock:
            player = self.active_player()
            if player is None:
                self.state.reset()
                return False
            had_content = player.current is not None or not player.queue.is_empty
            player.autoplay = wavelink.AutoPlayMode.disabled
            player.queue.reset()
            self.state.enter_music_mode()
            if player.current is not None:
                await player.skip(force=True)
            player.autoplay = wavelink.AutoPlayMode.partial
            player.inactive_timeout = MUSIC_IDLE_TIMEOUT_SECONDS
            return had_content

    async def skip(self) -> bool:
        """Skip normal music; return false for radio or empty playback."""

        if not self.state.can_skip:
            return False
        player = self.active_player()
        if player is None or player.current is None:
            return False
        if player.paused:
            # Wavelink retains the paused flag across skip/autoplay. Resume the
            # player first so the next FIFO item does not begin silently paused.
            await player.pause(False)
        await player.skip(force=True)
        return True

    async def disconnect(self) -> bool:
        """Stop, clear state, and leave voice."""

        async with self._operation_lock:
            player = self.active_player()
            self.state.reset()
            if player is None:
                return False
            player.queue.reset()
            await player.disconnect()
            LOGGER.info("Disconnected from Discord voice")
            return True

    async def set_volume(self, value: int) -> None:
        """Persist the chosen volume and apply it to the active player."""

        self.state.volume = value
        player = self.active_player()
        if player is not None:
            await player.set_volume(value)

    def clear_queue(self) -> int:
        """Clear upcoming normal music and return the previous size."""

        player = self.active_player()
        if player is None:
            return 0
        count = player.queue.count
        player.queue.clear()
        return count

    def shuffle_queue(self) -> int:
        """Shuffle upcoming normal music and return its size."""

        player = self.active_player()
        if player is None:
            return 0
        count = player.queue.count
        if count > 1:
            player.queue.shuffle()
        return count

    def remove_from_queue(self, position: int) -> wavelink.Playable:
        """Remove and return a one-based upcoming queue item."""

        player = self.active_player()
        if player is None:
            raise IndexError("The queue is empty")
        try:
            track = player.queue.peek(position - 1)
        except wavelink.QueueEmpty as exc:
            raise IndexError("The queue is empty") from exc
        player.queue.delete(position - 1)
        return track

    async def _notify_channel(self, channel_id: int | None, message: str) -> None:
        if not channel_id:
            return
        channel = self.client.get_channel(channel_id)
        if channel is None or not hasattr(channel, "send"):
            return
        try:
            await channel.send(message)  # type: ignore[union-attr]
        except discord.DiscordException:
            LOGGER.warning("Could not send music failure notice", exc_info=True)

    async def _notify_music_failure(
        self,
        player: wavelink.Player | None,
        track: wavelink.Playable,
        *,
        problem: str,
    ) -> None:
        """Report one asynchronous failure without competing with AutoPlay."""

        if player is None or player is not self.state.player or self.state.is_radio:
            return

        token = self._playback_token(track)
        origin = getattr(track.extras, "origin_source", None)
        if token is None or origin == "Live Radio":
            return
        if token in self._reported_music_failures:
            return

        # TrackException and TrackEnd(loadFailed) are separate events for the
        # same failure. Claim the token before the Discord request so racing
        # handlers cannot emit duplicate notices.
        self._reported_music_failures.append(token)

        has_next = not player.queue.is_empty
        action = (
            "More tracks remain queued."
            if has_next
            else "Nothing else is queued; try a different search or SoundCloud URL."
        )
        label = track_label(track)
        if len(label) > 180:
            label = f"{label[:179]}…"
        channel_id = getattr(track.extras, "request_channel_id", None)
        await self._notify_channel(
            channel_id,
            f"⚠️ **{label}** {problem} {action}",
        )

    async def handle_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        """Clean radio state and surface asynchronous music load failures."""

        if payload.player is not self.state.player or payload.reason == "replaced":
            return

        if self._is_active_radio_track(payload.track):
            station = self.state.radio_station
            channel_id = self.state.radio_text_channel_id
            self.state.enter_music_mode()
            payload.player.autoplay = wavelink.AutoPlayMode.partial
            if station is not None:
                LOGGER.warning(
                    "Radio stream ended for %s (%s)", station.name, payload.reason
                )
                await self._notify_channel(
                    channel_id,
                    f"📻 {station.name} stopped streaming. Use /radio to try again.",
                )
            return

        if payload.reason == "loadFailed" and self._is_loaded_track(
            payload.player,
            payload.track,
        ):
            await self._notify_music_failure(
                payload.player,
                payload.track,
                problem="couldn't load its audio.",
            )

    async def handle_track_exception(
        self,
        payload: wavelink.TrackExceptionEventPayload,
    ) -> None:
        """Log load failures and clear invalid radio state without reconnect loops."""

        LOGGER.error("Lavalink track exception: %s", payload.exception)
        if payload.player is self.state.player and self._is_active_radio_track(
            payload.track
        ):
            station = self.state.radio_station
            channel_id = self.state.radio_text_channel_id
            self.state.enter_music_mode()
            payload.player.autoplay = wavelink.AutoPlayMode.partial
            if station is not None:
                await self._notify_channel(
                    channel_id,
                    f"📻 {station.name} could not continue streaming. Use /radio to retry.",
                )
            return

        if payload.player is not None and self._is_current_track(
            payload.player,
            payload.track,
        ):
            await self._notify_music_failure(
                payload.player,
                payload.track,
                problem="couldn't load its audio.",
            )

    async def handle_track_stuck(
        self, payload: wavelink.TrackStuckEventPayload
    ) -> None:
        """Skip a stuck item once; never enter an aggressive retry loop."""

        LOGGER.error("Lavalink track stuck after %sms", payload.threshold)
        if payload.player is not None and payload.player is self.state.player:
            if self.state.is_radio:
                if not self._is_active_radio_track(payload.track):
                    return
                self.state.enter_music_mode()
                payload.player.autoplay = wavelink.AutoPlayMode.partial
            elif not self._is_current_track(payload.player, payload.track):
                return
            await payload.player.skip(force=True)
            await self._notify_music_failure(
                payload.player,
                payload.track,
                problem="stopped responding.",
            )

    async def handle_inactive_player(self, player: wavelink.Player) -> None:
        """Disconnect after the configured no-playback timeout."""

        if (
            player is self.state.player
            and not self.state.is_radio
            and player.current is None
            and player.queue.is_empty
        ):
            LOGGER.info("Disconnecting idle music player")
            await self.disconnect()

    def handle_voice_disconnect(self) -> None:
        """Forget a Player that Discord has disconnected externally."""

        self.state.reset()

    async def handle_node_disconnected(self, node: wavelink.Node) -> None:
        """Discard a stale voice client after terminal Lavalink retry failure."""

        if node is not self.node or node.status is not wavelink.NodeStatus.DISCONNECTED:
            return

        player = self.state.player
        station = self.state.radio_station
        channel_id = self.state.radio_text_channel_id
        self.state.reset()
        if player is not None and player.connected:
            with suppress(discord.DiscordException, wavelink.WavelinkException):
                await player.disconnect()
        if station is not None:
            await self._notify_channel(
                channel_id,
                f"📻 {station.name} stopped because the audio node disconnected. "
                "Use /radio to retry.",
            )

    async def shutdown(self) -> None:
        """Cancel connection work and close Wavelink resources."""

        if self._node_task is not None and not self._node_task.done():
            self._node_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._node_task
        player = self.active_player()
        self.state.reset()
        if player is not None:
            with suppress(discord.DiscordException, wavelink.WavelinkException):
                await player.disconnect()

        # Wavelink retains only successfully connected nodes in Pool. Close an
        # initial failed node explicitly, and always close the ClientSession we
        # supplied so a Lavalink outage cannot leak an orphan HTTP session.
        try:
            if (
                self.node is not None
                and self.node.identifier not in wavelink.Pool.nodes
            ):
                await self.node.close()
            await wavelink.Pool.close()
        finally:
            if self._node_session is not None and not self._node_session.closed:
                await self._node_session.close()
