"""Tests for Wavelink queue semantics, music state, and radio formatting."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import wavelink

from music.commands import _play_response
from music.helpers import build_now_playing_embed, build_queue_embed
from music.player import MusicController, PlayOutcome
from music.radio_stations import (
    DEFAULT_RADIO_STATION_KEY,
    RADIO_STATIONS,
    get_radio_station,
)
from music.state import MusicState


def _track_payload(title: str, *, author: str = "Artist") -> dict[str, object]:
    return {
        "encoded": f"encoded-{title}",
        "info": {
            "identifier": title.lower().replace(" ", "-"),
            "isSeekable": True,
            "author": author,
            "length": 180_000,
            "isStream": False,
            "position": 0,
            "title": title,
            "uri": f"https://soundcloud.com/test/{title.lower()}",
            "artworkUrl": None,
            "isrc": None,
            "sourceName": "soundcloud",
        },
        "pluginInfo": {},
        "userData": {},
    }


def _track(title: str) -> wavelink.Playable:
    return wavelink.Playable(_track_payload(title))  # type: ignore[arg-type]


def _playlist(*titles: str) -> wavelink.Playlist:
    return wavelink.Playlist(  # type: ignore[arg-type]
        {
            "info": {"name": "Test Playlist", "selectedTrack": -1},
            "pluginInfo": {},
            "tracks": [_track_payload(title) for title in titles],
        }
    )


def _player(*, current: wavelink.Playable | None = None) -> SimpleNamespace:
    queue = wavelink.Queue()
    if current is not None:
        queue._loaded = current
    return SimpleNamespace(
        connected=True,
        current=current,
        queue=queue,
        autoplay=wavelink.AutoPlayMode.partial,
        _previous=None,
        paused=False,
        pause=AsyncMock(),
        play=AsyncMock(),
        skip=AsyncMock(),
    )


class MusicQueueTests(unittest.IsolatedAsyncioTestCase):
    """Use Wavelink's real Queue while replacing external player operations."""

    def _controller(self, player: SimpleNamespace) -> MusicController:
        controller = MusicController(SimpleNamespace())  # type: ignore[arg-type]
        controller.state.attach_player(player)  # type: ignore[arg-type]
        controller.ensure_player = AsyncMock(return_value=player)  # type: ignore[method-assign]
        controller._require_node = AsyncMock(return_value=object())  # type: ignore[method-assign]
        return controller

    @staticmethod
    def _interaction() -> SimpleNamespace:
        return SimpleNamespace(
            user=SimpleNamespace(id=42, display_name="Requester"),
            channel_id=100,
        )

    async def test_playlist_starts_first_track_and_queues_remainder_in_order(
        self,
    ) -> None:
        player = _player()
        controller = self._controller(player)
        controller._search = AsyncMock(  # type: ignore[method-assign]
            return_value=_playlist("First", "Second", "Third")
        )

        outcome = await controller.play_query(
            self._interaction(),  # type: ignore[arg-type]
            "https://youtube.com/playlist?list=test",
        )

        self.assertTrue(outcome.started)
        self.assertEqual(outcome.added, 3)
        self.assertEqual(outcome.collection_name, "Test Playlist")
        self.assertEqual([track.title for track in player.queue], ["Second", "Third"])
        player.play.assert_awaited_once()
        started_track = player.play.await_args.args[0]
        self.assertEqual(started_track.title, "First")
        self.assertEqual(
            started_track.extras.requester_name,
            "Requester",
        )

    async def test_new_voice_player_starts_initial_idle_countdown(self) -> None:
        connected = Mock(spec=wavelink.Player)
        connected.set_volume = AsyncMock()
        voice_channel = SimpleNamespace(connect=AsyncMock(return_value=connected))
        interaction = SimpleNamespace(
            user=SimpleNamespace(voice=SimpleNamespace(channel=voice_channel)),
            guild=SimpleNamespace(voice_client=None),
        )
        controller = MusicController(SimpleNamespace())  # type: ignore[arg-type]
        controller._require_node = AsyncMock(return_value=object())  # type: ignore[method-assign]

        player = await controller.ensure_player(interaction)  # type: ignore[arg-type]

        self.assertIs(player, connected)
        self.assertEqual(connected.inactive_timeout, 300)
        connected.set_volume.assert_awaited_once_with(controller.state.volume)

    async def test_playlist_appends_atomically_after_existing_fifo_items(self) -> None:
        player = _player(current=_track("Current"))
        player.queue.put(_track("Already Queued"))
        controller = self._controller(player)
        controller._search = AsyncMock(  # type: ignore[method-assign]
            return_value=_playlist("One", "Two", "Three")
        )

        outcome = await controller.play_query(
            self._interaction(),  # type: ignore[arg-type]
            "https://youtube.com/playlist?list=test",
        )

        self.assertFalse(outcome.started)
        self.assertEqual(
            [track.title for track in player.queue],
            ["Already Queued", "One", "Two", "Three"],
        )
        player.play.assert_not_awaited()

    async def test_shuffle_remove_and_clear_operate_on_upcoming_wavelink_queue(
        self,
    ) -> None:
        player = _player(current=_track("Current"))
        player.queue.put([_track("One"), _track("Two"), _track("Three")])
        controller = self._controller(player)

        with patch(
            "wavelink.queue.random.shuffle",
            side_effect=lambda items: items.reverse(),
        ):
            self.assertEqual(controller.shuffle_queue(), 3)

        self.assertEqual(
            [track.title for track in player.queue], ["Three", "Two", "One"]
        )
        removed = controller.remove_from_queue(2)
        self.assertEqual(removed.title, "Two")
        self.assertEqual([track.title for track in player.queue], ["Three", "One"])
        self.assertEqual(controller.clear_queue(), 2)
        self.assertTrue(player.queue.is_empty)
        self.assertEqual(controller.clear_queue(), 0)

    async def test_youtube_mirroring_preserves_order_and_reports_failures(
        self,
    ) -> None:
        controller = MusicController(SimpleNamespace())  # type: ignore[arg-type]
        metadata = [_track("First"), _track("Missing"), _track("Third")]

        searches: list[str] = []

        async def youtube_search(query: str, **_: object) -> list[wavelink.Playable]:
            searches.append(query)
            if "Missing" in query:
                return []
            title = query.removeprefix("ytmsearch:").split()[0]
            return [_track(f"Mirror {title}")]

        with patch(
            "music.player.wavelink.Playable.search",
            AsyncMock(side_effect=youtube_search),
        ):
            resolved, failed = await controller._resolve_mirrored_tracks(
                metadata,
                self._interaction(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                origin_source="Spotify → YouTube",
            )

        self.assertEqual(failed, 1)
        self.assertEqual(
            [track.title for track in resolved], ["Mirror First", "Mirror Third"]
        )
        self.assertEqual(
            [track.extras.display_title for track in resolved], ["First", "Third"]
        )
        self.assertTrue(
            all(
                track.extras.origin_source == "Spotify → YouTube"
                for track in resolved
            )
        )
        self.assertTrue(all(query.startswith("ytmsearch:") for query in searches))

    async def test_soundcloud_url_uses_metadata_then_youtube_audio(self) -> None:
        player = _player()
        controller = self._controller(player)
        soundcloud_metadata = _track("SoundCloud Metadata")
        youtube_audio = _track("YouTube Audio")
        controller._search = AsyncMock(  # type: ignore[method-assign]
            return_value=[soundcloud_metadata]
        )
        controller._resolve_mirrored_tracks = AsyncMock(  # type: ignore[method-assign]
            return_value=([youtube_audio], 0)
        )

        outcome = await controller.play_query(
            self._interaction(),  # type: ignore[arg-type]
            "https://soundcloud.com/linkinpark/roads-untraveled",
        )

        self.assertTrue(outcome.started)
        controller._resolve_mirrored_tracks.assert_awaited_once_with(
            [soundcloud_metadata],
            self._interaction(),
            controller._require_node.return_value,
            origin_source="SoundCloud → YouTube",
        )
        self.assertIs(player.play.await_args.args[0], youtube_audio)

    async def test_radio_replaces_playback_and_clears_the_music_queue(self) -> None:
        player = _player(current=_track("Current"))
        player.queue.put([_track("Queued One"), _track("Queued Two")])
        controller = self._controller(player)
        radio_track = _track("LIVE 105 Stream")

        with patch(
            "music.player.wavelink.Playable.search",
            AsyncMock(return_value=[radio_track]),
        ):
            await controller.start_radio(
                self._interaction(),  # type: ignore[arg-type]
                RADIO_STATIONS["live105"],
            )

        self.assertTrue(player.queue.is_empty)
        self.assertEqual(player.autoplay, wavelink.AutoPlayMode.disabled)
        player.skip.assert_not_awaited()
        player.play.assert_awaited_once()
        self.assertIs(player.play.await_args.args[0], radio_track)
        self.assertTrue(controller.state.is_radio)
        self.assertIs(controller.state.radio_station, RADIO_STATIONS["live105"])

    async def test_radio_to_single_music_replaces_stream_with_no_upcoming_items(
        self,
    ) -> None:
        radio_track = _track("LIVE 105 Stream")
        radio_track.extras = {
            "playback_token": "radio-1",
            "origin_source": "Live Radio",
        }
        player = _player(current=radio_track)
        controller = self._controller(player)
        controller.state.enter_radio_mode(
            RADIO_STATIONS["live105"],
            text_channel_id=100,
            playback_token="radio-1",
        )
        controller._search = AsyncMock(  # type: ignore[method-assign]
            return_value=[_track("Song")]
        )

        async def adopt_track(track: wavelink.Playable, **_: object) -> None:
            player.current = track

        player.play.side_effect = adopt_track

        outcome = await controller.play_query(
            self._interaction(),  # type: ignore[arg-type]
            "Song",
        )

        self.assertTrue(outcome.started)
        self.assertFalse(controller.state.is_radio)
        self.assertEqual(player.current.title, "Song")
        self.assertTrue(player.queue.is_empty)
        self.assertEqual(player.autoplay, wavelink.AutoPlayMode.partial)
        self.assertTrue(player.play.await_args.kwargs["replace"])
        self.assertFalse(player.play.await_args.kwargs["paused"])

    async def test_music_load_failure_notifies_once_without_double_advancing(
        self,
    ) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        controller = MusicController(  # type: ignore[arg-type]
            SimpleNamespace(get_channel=lambda _: channel)
        )
        failed = _track("Unavailable")
        controller._set_track_metadata(
            failed,
            interaction=self._interaction(),  # type: ignore[arg-type]
            origin_source="SoundCloud",
        )
        player = _player(current=failed)
        player.queue.put(_track("Next"))
        controller.state.attach_player(player)  # type: ignore[arg-type]
        exception_payload = wavelink.TrackExceptionEventPayload(
            player=player,  # type: ignore[arg-type]
            track=failed,
            exception={
                "message": "Something broke when playing the track.",
                "severity": "common",
                "cause": "HTTP 404",
            },
        )
        end_payload = wavelink.TrackEndEventPayload(
            player=player,  # type: ignore[arg-type]
            track=failed,
            reason="loadFailed",
        )

        await controller.handle_track_exception(exception_payload)
        # Wavelink clears current before dispatching TrackEnd. BetterKyle must
        # leave the queue untouched so Wavelink's own AutoPlay advances once.
        player.current = None
        await controller.handle_track_end(end_payload)

        channel.send.assert_awaited_once()
        notice = channel.send.await_args.args[0]
        self.assertIn("Unavailable", notice)
        self.assertIn("More tracks remain queued", notice)
        self.assertEqual([track.title for track in player.queue], ["Next"])
        player.play.assert_not_awaited()
        player.skip.assert_not_awaited()

    async def test_music_load_failed_end_reports_when_exception_event_is_missed(
        self,
    ) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        controller = MusicController(  # type: ignore[arg-type]
            SimpleNamespace(get_channel=lambda _: channel)
        )
        failed = _track("Unavailable")
        controller._set_track_metadata(
            failed,
            interaction=self._interaction(),  # type: ignore[arg-type]
            origin_source="SoundCloud",
        )
        player = _player(current=failed)
        controller.state.attach_player(player)  # type: ignore[arg-type]
        payload = wavelink.TrackEndEventPayload(
            player=player,  # type: ignore[arg-type]
            track=failed,
            reason="loadFailed",
        )

        player.current = None
        await controller.handle_track_end(payload)

        channel.send.assert_awaited_once()
        notice = channel.send.await_args.args[0]
        self.assertIn("couldn't load its audio", notice)
        self.assertIn("Nothing else is queued", notice)

    async def test_stale_music_failure_does_not_report_the_new_current_track(
        self,
    ) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        controller = MusicController(  # type: ignore[arg-type]
            SimpleNamespace(get_channel=lambda _: channel)
        )
        old = _track("Old")
        new = _track("New")
        controller._set_track_metadata(
            old,
            interaction=self._interaction(),  # type: ignore[arg-type]
            origin_source="SoundCloud",
        )
        controller._set_track_metadata(
            new,
            interaction=self._interaction(),  # type: ignore[arg-type]
            origin_source="SoundCloud",
        )
        player = _player(current=new)
        controller.state.attach_player(player)  # type: ignore[arg-type]
        payload = wavelink.TrackExceptionEventPayload(
            player=player,  # type: ignore[arg-type]
            track=old,
            exception={
                "message": "Late failure",
                "severity": "common",
                "cause": "HTTP 404",
            },
        )

        await controller.handle_track_exception(payload)

        channel.send.assert_not_awaited()

    async def test_failed_startup_node_can_be_registered_on_a_later_retry(self) -> None:
        controller = MusicController(SimpleNamespace())  # type: ignore[arg-type]
        stale_node = SimpleNamespace(
            identifier="betterkyle",
            status=wavelink.NodeStatus.DISCONNECTED,
        )
        controller.node = stale_node  # type: ignore[assignment]

        async def connect_again(**_: object) -> dict[str, object]:
            stale_node.status = wavelink.NodeStatus.CONNECTED
            return {stale_node.identifier: stale_node}

        with (
            patch("music.player.LAVALINK_PASSWORD", "test-password"),
            patch.object(wavelink.Pool, "_Pool__nodes", {}),
            patch.object(
                wavelink.Pool,
                "connect",
                AsyncMock(side_effect=connect_again),
            ) as pool_connect,
        ):
            connected = await controller.connect_lavalink()

        self.assertTrue(connected)
        pool_connect.assert_awaited_once()

    async def test_empty_queue_remove_has_clean_index_error(self) -> None:
        player = _player(current=_track("Current"))
        controller = self._controller(player)

        with self.assertRaises(IndexError):
            controller.remove_from_queue(1)

    async def test_skip_resumes_a_paused_player_before_advancing_fifo(self) -> None:
        player = _player(current=_track("Current"))
        player.paused = True
        controller = self._controller(player)

        skipped = await controller.skip()

        self.assertTrue(skipped)
        player.pause.assert_awaited_once_with(False)
        player.skip.assert_awaited_once_with(force=True)

    async def test_shutdown_closes_a_failed_nodes_orphan_session(self) -> None:
        controller = MusicController(SimpleNamespace())  # type: ignore[arg-type]
        session = aiohttp.ClientSession()
        controller._node_session = session
        controller.node = wavelink.Node(
            identifier="failed-node",
            uri="http://127.0.0.1:1",
            password="test-password",
            session=session,
        )

        with patch.object(wavelink.Pool, "_Pool__nodes", {}):
            await controller.shutdown()

        self.assertTrue(session.closed)

    async def test_terminal_node_disconnect_discards_stale_voice_player(self) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        controller = MusicController(  # type: ignore[arg-type]
            SimpleNamespace(get_channel=lambda _: channel)
        )
        node = SimpleNamespace(status=wavelink.NodeStatus.DISCONNECTED)
        controller.node = node  # type: ignore[assignment]
        player = _player(current=_track("Radio"))
        player.disconnect = AsyncMock()
        controller.state.attach_player(player)  # type: ignore[arg-type]
        controller.state.enter_radio_mode(
            RADIO_STATIONS["live105"],
            text_channel_id=123,
            playback_token="radio-1",
        )

        await controller.handle_node_disconnected(node)  # type: ignore[arg-type]

        self.assertIsNone(controller.state.player)
        self.assertFalse(controller.state.is_radio)
        player.disconnect.assert_awaited_once()
        channel.send.assert_awaited_once()


class MusicStateAndRadioTests(unittest.TestCase):
    """Pin one-player radio transitions and station presentation."""

    def test_music_state_radio_transitions_and_can_skip(self) -> None:
        station = RADIO_STATIONS["live105"]
        player = SimpleNamespace()
        state = MusicState()

        self.assertTrue(state.can_skip)
        state.attach_player(player)  # type: ignore[arg-type]
        state.enter_radio_mode(station, text_channel_id=123, playback_token="radio-1")
        self.assertTrue(state.is_radio)
        self.assertFalse(state.can_skip)
        self.assertIs(state.radio_station, station)
        self.assertEqual(state.radio_text_channel_id, 123)
        self.assertEqual(state.radio_playback_token, "radio-1")

        state.enter_music_mode()
        self.assertTrue(state.can_skip)
        self.assertFalse(state.is_radio)
        self.assertIsNone(state.radio_station)
        self.assertIsNone(state.radio_text_channel_id)
        self.assertIs(state.player, player)

        state.enter_radio_mode(station, text_channel_id=456, playback_token="radio-2")
        state.reset(keep_player=True)
        self.assertIs(state.player, player)
        self.assertTrue(state.can_skip)
        state.reset()
        self.assertIsNone(state.player)

    def test_radio_registry_keys_and_default_are_exact(self) -> None:
        self.assertEqual(set(RADIO_STATIONS), {"live105", "mix1065"})
        self.assertEqual(DEFAULT_RADIO_STATION_KEY, "live105")

    def test_live105_registry_url_and_lookup_are_exact(self) -> None:
        station = RADIO_STATIONS["live105"]
        self.assertEqual(station.key, "live105")
        self.assertEqual(station.name, "LIVE 105")
        self.assertEqual(station.frequency, "105.3")
        self.assertEqual(station.callsign, "KITS")
        self.assertEqual(station.stream_type, "AAC/live HTTP stream")
        self.assertEqual(
            station.stream_url,
            "https://live.amperwave.net/direct/audacy-kitsfmaac-imc",
        )
        self.assertIs(get_radio_station(" LIVE105 "), station)
        self.assertIsNone(get_radio_station("unknown"))

    def test_mix1065_registry_url_and_lookup_are_exact(self) -> None:
        station = RADIO_STATIONS["mix1065"]
        self.assertEqual(station.key, "mix1065")
        self.assertEqual(station.name, "MIX 106.5")
        self.assertEqual(station.frequency, "106.5")
        self.assertEqual(station.callsign, "KEZR")
        self.assertEqual(station.stream_type, "AAC/live HTTP stream")
        self.assertEqual(
            station.stream_url,
            "https://live.amperwave.net/direct/alphacorporate-kezrfmaac-ibc4",
        )
        self.assertIs(get_radio_station(" MIX1065 "), station)

    def test_radio_nowplaying_embed_has_live_status_and_no_duration(self) -> None:
        state = MusicState()
        state.enter_radio_mode(
            RADIO_STATIONS["live105"],
            text_channel_id=123,
            playback_token="radio-1",
        )
        player = SimpleNamespace(position=9_999_999, current=_track("Ignored"))

        embed = build_now_playing_embed(state, player)  # type: ignore[arg-type]
        payload = embed.to_dict()

        self.assertEqual(payload["title"], "📻 Now Playing")
        self.assertEqual(payload["description"], "**LIVE 105**\n105.3 KITS")
        fields = {field["name"]: field["value"] for field in payload["fields"]}
        self.assertEqual(fields, {"Source": "Live Radio", "Status": "LIVE"})
        self.assertNotIn("Position", fields)
        self.assertNotIn("Duration", fields)

    def test_radio_state_ignores_a_replaced_tracks_delayed_end_event(self) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        controller = MusicController(  # type: ignore[arg-type]
            SimpleNamespace(get_channel=lambda _: channel)
        )
        player = _player(current=_track("Current Radio"))
        controller.state.attach_player(player)  # type: ignore[arg-type]
        controller.state.enter_radio_mode(
            RADIO_STATIONS["live105"],
            text_channel_id=123,
            playback_token="new-generation",
        )
        stale_track = _track("Old Radio")
        stale_track.extras = {"playback_token": "old-generation"}
        payload = wavelink.TrackEndEventPayload(
            player=player,  # type: ignore[arg-type]
            track=stale_track,
            reason="finished",
        )

        async def exercise() -> None:
            await controller.handle_track_end(payload)

        asyncio.run(exercise())

        self.assertTrue(controller.state.is_radio)
        self.assertEqual(
            controller.state.radio_playback_token,
            "new-generation",
        )
        channel.send.assert_not_awaited()

    def test_queue_embed_bounds_long_source_titles_to_discord_field_limits(
        self,
    ) -> None:
        long_title = "T" * 2_000
        current = _track(long_title)
        player = _player(current=current)
        player.queue.put([_track(f"{long_title}{index}") for index in range(10)])

        embed = build_queue_embed(player, list(player.queue))  # type: ignore[arg-type]

        self.assertTrue(embed.fields)
        self.assertTrue(all(len(field.value) <= 1_024 for field in embed.fields))

    def test_empty_upcoming_queue_is_distinct_from_current_playback(self) -> None:
        player = _player(current=_track("Current"))

        embed = build_queue_embed(player, [])  # type: ignore[arg-type]

        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Current", fields["Now Playing"])
        self.assertEqual(fields["Up Next"], "No upcoming tracks.")

    def test_play_response_does_not_claim_audio_is_already_playing(self) -> None:
        response = _play_response(
            PlayOutcome(
                added=1,
                failed=0,
                started=True,
                collection_name=None,
                first_track_label="Artist — Song",
                spotify=False,
            )
        )

        self.assertEqual(response, "Starting **Artist — Song**.")


if __name__ == "__main__":
    unittest.main()
