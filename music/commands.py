"""Guild-specific slash commands for music queue and live radio control."""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from music.helpers import build_now_playing_embed, build_queue_embed, track_label
from music.player import MusicController, MusicError, PlayOutcome
from music.radio_stations import (
    DEFAULT_RADIO_STATION_KEY,
    RADIO_STATIONS,
    get_radio_station,
)


LOGGER = logging.getLogger(__name__)


def _play_response(outcome: PlayOutcome) -> str:
    if outcome.collection_name:
        message = (
            f"Added {outcome.added} track"
            f"{'s' if outcome.added != 1 else ''} from "
            f"**{outcome.collection_name}**."
        )
        if outcome.started:
            message += " Playback started."
    elif outcome.started:
        message = f"Now playing **{outcome.first_track_label}**."
    else:
        message = f"Added **{outcome.first_track_label}** to the queue."

    if outcome.failed:
        message += (
            f"\n{outcome.failed} Spotify track"
            f"{'s' if outcome.failed != 1 else ''} could not be resolved "
            "to playable SoundCloud audio and were skipped."
        )
    return message


async def _send_music_error(
    interaction: discord.Interaction,
    error: MusicError,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(str(error), ephemeral=True)
    else:
        await interaction.response.send_message(str(error), ephemeral=True)


def setup_music_commands(
    tree: app_commands.CommandTree,
    guild: discord.Object,
    controller: MusicController,
) -> None:
    """Register all music commands directly to BetterKyle's one guild."""

    @tree.command(
        name="play",
        description="Play a Spotify/SoundCloud URL or search SoundCloud",
        guild=guild,
    )
    @app_commands.describe(query="Track, album, playlist URL, or search text")
    async def play(interaction: discord.Interaction, query: str) -> None:
        """Resolve a request asynchronously and preserve playlist order."""

        await interaction.response.defer(thinking=True)
        try:
            outcome = await controller.play_query(interaction, query)
        except MusicError as exc:
            await _send_music_error(interaction, exc)
            return
        except Exception:
            LOGGER.exception("Unexpected /play failure")
            await interaction.followup.send(
                "Playback failed unexpectedly. League tracking is still running.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(_play_response(outcome))

    station_choices = [
        app_commands.Choice(
            name=f"{station.name} — {station.frequency} {station.callsign}",
            value=station.key,
        )
        for station in RADIO_STATIONS.values()
    ]

    @tree.command(
        name="radio",
        description="Stream a configured live radio station",
        guild=guild,
    )
    @app_commands.choices(station=station_choices)
    async def radio(
        interaction: discord.Interaction,
        station: str = DEFAULT_RADIO_STATION_KEY,
    ) -> None:
        """Replace music with a configured direct Lavalink HTTP stream."""

        await interaction.response.defer(thinking=True)
        configured = get_radio_station(station)
        if configured is None:
            await interaction.followup.send("That radio station is not configured.")
            return
        try:
            await controller.start_radio(interaction, configured)
        except MusicError as exc:
            await _send_music_error(interaction, exc)
            return
        except Exception:
            LOGGER.exception("Unexpected /radio failure for %s", station)
            await interaction.followup.send(
                f"{configured.name} could not start. Please try again later.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📻 {configured.name}",
            description=(
                f"**{configured.frequency} {configured.callsign}**\nNow streaming live."
            ),
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)

    @tree.command(name="pause", description="Pause playback", guild=guild)
    async def pause(interaction: discord.Interaction) -> None:
        player = controller.active_player()
        if player is None or player.current is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
        elif player.paused:
            await interaction.response.send_message(
                "Playback is already paused.", ephemeral=True
            )
        else:
            await player.pause(True)
            await interaction.response.send_message("Playback paused.")

    @tree.command(name="resume", description="Resume playback", guild=guild)
    async def resume(interaction: discord.Interaction) -> None:
        player = controller.active_player()
        if player is None or player.current is None:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
        elif not player.paused:
            await interaction.response.send_message(
                "Playback is not paused.", ephemeral=True
            )
        else:
            await player.pause(False)
            await interaction.response.send_message("Playback resumed.")

    @tree.command(name="skip", description="Skip the current track", guild=guild)
    async def skip(interaction: discord.Interaction) -> None:
        if controller.state.is_radio:
            station = controller.state.radio_station
            name = station.name if station else "Live radio"
            await interaction.response.send_message(
                f"{name} is a live stream, so there is nothing to skip. "
                "Use /play to switch to music or /stop to stop radio.",
                ephemeral=True,
            )
            return
        if await controller.skip():
            await interaction.response.send_message("Skipped.")
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @tree.command(
        name="stop", description="Stop playback and clear the queue", guild=guild
    )
    async def stop(interaction: discord.Interaction) -> None:
        stopped = await controller.stop()
        await interaction.response.send_message(
            "Playback stopped. The queue was cleared."
            if stopped
            else "Nothing is playing.",
            ephemeral=not stopped,
        )

    @tree.command(name="queue", description="Show the music queue", guild=guild)
    async def queue_command(interaction: discord.Interaction) -> None:
        if controller.state.is_radio and controller.state.radio_station is not None:
            station = controller.state.radio_station
            await interaction.response.send_message(
                f"📻 **{station.name}** is currently streaming.\n\n"
                "The normal music queue is empty. Use /play to switch back to music."
            )
            return
        player = controller.active_player()
        if player is None:
            await interaction.response.send_message(
                "The music queue is empty.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=build_queue_embed(player, list(player.queue))
        )

    @tree.command(name="nowplaying", description="Show current playback", guild=guild)
    async def nowplaying(interaction: discord.Interaction) -> None:
        player = controller.active_player()
        if player is None or (player.current is None and not controller.state.is_radio):
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        try:
            embed = build_now_playing_embed(controller.state, player)
        except ValueError:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=embed)

    @tree.command(name="shuffle", description="Shuffle upcoming music", guild=guild)
    async def shuffle(interaction: discord.Interaction) -> None:
        if controller.state.is_radio:
            await interaction.response.send_message(
                "Shuffle applies only to the normal music queue.",
                ephemeral=True,
            )
            return
        count = controller.shuffle_queue()
        if count < 2:
            await interaction.response.send_message(
                "At least two queued tracks are needed to shuffle.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"Shuffled {count} queued tracks.")

    @tree.command(name="volume", description="Set volume from 0 to 200", guild=guild)
    async def volume(
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 200],
    ) -> None:
        await controller.set_volume(level)
        await interaction.response.send_message(f"Volume set to {level}%.")

    @tree.command(
        name="disconnect",
        description="Stop playback and leave voice",
        guild=guild,
    )
    async def disconnect(interaction: discord.Interaction) -> None:
        disconnected = await controller.disconnect()
        await interaction.response.send_message(
            "Disconnected from voice."
            if disconnected
            else "BetterKyle is not connected to voice.",
            ephemeral=not disconnected,
        )

    @tree.command(
        name="clearqueue",
        description="Clear upcoming music",
        guild=guild,
    )
    async def clearqueue(interaction: discord.Interaction) -> None:
        if controller.state.is_radio:
            await interaction.response.send_message(
                "The normal music queue is already empty while radio is active.",
                ephemeral=True,
            )
            return
        count = controller.clear_queue()
        await interaction.response.send_message(
            f"Cleared {count} queued track{'s' if count != 1 else ''}."
            if count
            else "The queue is already empty.",
            ephemeral=count == 0,
        )

    @tree.command(
        name="remove", description="Remove an upcoming queue item", guild=guild
    )
    async def remove(
        interaction: discord.Interaction,
        position: app_commands.Range[int, 1],
    ) -> None:
        if controller.state.is_radio:
            await interaction.response.send_message(
                "Live radio has no upcoming tracks to remove.",
                ephemeral=True,
            )
            return
        try:
            removed = controller.remove_from_queue(position)
        except IndexError:
            await interaction.response.send_message(
                "That queue position does not exist.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Removed **{track_label(removed)}** from the queue."
        )
