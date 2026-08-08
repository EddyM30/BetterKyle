"""Deterministic Match-V5 discovery and party-deduplication tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch, sentinel

from database import TrackedUser
import league.tracker as tracker


def _user(
    discord_id: int,
    puuid: str,
    *,
    last_match: str = "NA1_MARKER",
) -> TrackedUser:
    return TrackedUser(
        discord_id=discord_id,
        puuid=puuid,
        riot_name=f"Player {discord_id}",
        riot_tag="NA1",
        last_match=last_match,
        announce_initial=False,
        streak_type="none",
        streak_count=0,
    )


def _match(
    queue_id: int,
    participants: list[dict[str, object]],
    *,
    ended_at: int = 100,
) -> dict[str, object]:
    return {
        "info": {
            "queueId": queue_id,
            "gameEndTimestamp": ended_at,
            "participants": participants,
        }
    }


class TrackerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise filtering and dedup without Riot or Discord network access."""

    async def test_unsupported_newest_match_does_not_hide_supported_match(self) -> None:
        user = _user(1, "puuid-one")
        unsupported = _match(1700, [{"puuid": "puuid-one", "win": False}])
        supported = _match(450, [{"puuid": "puuid-one", "win": True}])

        async def match_details(match_id: str) -> dict[str, object]:
            return {
                "NA1_ARENA": unsupported,
                "NA1_ARAM": supported,
            }[match_id]

        channel = SimpleNamespace(send=AsyncMock())
        fake_bot = SimpleNamespace(get_channel=Mock(return_value=channel))

        with (
            patch.object(tracker, "get_users", AsyncMock(return_value=[user])),
            patch.object(
                tracker,
                "get_recent_matches",
                AsyncMock(return_value=["NA1_ARENA", "NA1_ARAM", "NA1_MARKER"]),
            ),
            patch.object(tracker, "get_saved_match_ids", AsyncMock(return_value=set())),
            patch.object(
                tracker, "get_match", AsyncMock(side_effect=match_details)
            ) as get_match,
            patch.object(
                tracker, "create_match_embed", Mock(return_value=sentinel.embed)
            ) as create_embed,
            patch.object(
                tracker, "record_match_result", AsyncMock(return_value=True)
            ) as record,
            patch.object(tracker, "finalize_poll", AsyncMock()) as finalize,
            patch.object(tracker, "CHANNEL_ID", 99),
            patch.object(tracker, "bot", fake_bot),
        ):
            posted = await tracker._check_matches_once()

        self.assertEqual(posted, 1)
        self.assertEqual(
            get_match.await_args_list,
            [call("NA1_ARENA"), call("NA1_ARAM")],
        )
        create_embed.assert_called_once()
        self.assertIs(create_embed.call_args.args[0], supported)
        channel.send.assert_awaited_once_with(embed=sentinel.embed)
        record.assert_awaited_once_with("NA1_ARAM", {1: "win"})
        finalize.assert_awaited_once_with({"NA1_ARENA"}, {1: "NA1_ARENA"})

    async def test_same_match_is_announced_once_for_linked_party(self) -> None:
        first = _user(1, "puuid-one")
        second = _user(2, "puuid-two")
        match = _match(
            420,
            [
                {"puuid": "puuid-one", "win": True, "championName": "Lux"},
                {"puuid": "puuid-two", "win": True, "championName": "Garen"},
            ],
        )
        channel = SimpleNamespace(send=AsyncMock())
        fake_bot = SimpleNamespace(get_channel=Mock(return_value=channel))

        with (
            patch.object(tracker, "get_users", AsyncMock(return_value=[first, second])),
            patch.object(
                tracker,
                "get_recent_matches",
                AsyncMock(return_value=["NA1_PARTY", "NA1_MARKER"]),
            ) as histories,
            patch.object(tracker, "get_saved_match_ids", AsyncMock(return_value=set())),
            patch.object(
                tracker, "get_match", AsyncMock(return_value=match)
            ) as get_match,
            patch.object(
                tracker, "create_match_embed", Mock(return_value=sentinel.embed)
            ) as create_embed,
            patch.object(
                tracker, "record_match_result", AsyncMock(return_value=True)
            ) as record,
            patch.object(tracker, "finalize_poll", AsyncMock()) as finalize,
            patch.object(tracker, "CHANNEL_ID", 99),
            patch.object(tracker, "bot", fake_bot),
        ):
            posted = await tracker._check_matches_once()

        self.assertEqual(posted, 1)
        self.assertEqual(histories.await_count, 2)
        get_match.assert_awaited_once_with("NA1_PARTY")
        channel.send.assert_awaited_once_with(embed=sentinel.embed)
        record.assert_awaited_once_with("NA1_PARTY", {1: "win", 2: "win"})
        finalize.assert_awaited_once_with(
            set(),
            {1: "NA1_PARTY", 2: "NA1_PARTY"},
        )

        party = create_embed.call_args.args[1]
        self.assertEqual([member["discord_id"] for member in party], [1, 2])
        self.assertEqual(
            [member["riot_name"] for member in party],
            ["Player 1", "Player 2"],
        )

    async def test_targeted_refresh_enriches_every_linked_party_member(self) -> None:
        target = _user(1, "puuid-target")
        party_member = _user(2, "puuid-party")
        match = _match(
            440,
            [
                {"puuid": "puuid-target", "win": False},
                {"puuid": "puuid-party", "win": False},
                {"puuid": "unlinked", "win": True},
            ],
        )
        channel = SimpleNamespace(send=AsyncMock())
        fake_bot = SimpleNamespace(get_channel=Mock(return_value=channel))

        with (
            patch.object(
                tracker,
                "get_users",
                AsyncMock(return_value=[target, party_member]),
            ),
            patch.object(
                tracker,
                "get_recent_matches",
                AsyncMock(return_value=["NA1_TARGET", "NA1_MARKER"]),
            ) as histories,
            patch.object(tracker, "get_saved_match_ids", AsyncMock(return_value=set())),
            patch.object(tracker, "get_match", AsyncMock(return_value=match)),
            patch.object(
                tracker, "create_match_embed", Mock(return_value=sentinel.embed)
            ) as create_embed,
            patch.object(
                tracker, "record_match_result", AsyncMock(return_value=True)
            ) as record,
            patch.object(tracker, "finalize_poll", AsyncMock()),
            patch.object(tracker, "CHANNEL_ID", 99),
            patch.object(tracker, "bot", fake_bot),
        ):
            posted = await tracker._check_matches_once("puuid-target")

        self.assertEqual(posted, 1)
        histories.assert_awaited_once_with(
            "puuid-target", count=tracker.MATCH_LOOKBACK_COUNT
        )
        party = create_embed.call_args.args[1]
        self.assertEqual(
            [member["puuid"] for member in party], ["puuid-target", "puuid-party"]
        )
        self.assertEqual([member["discord_id"] for member in party], [1, 2])
        record.assert_awaited_once_with("NA1_TARGET", {1: "loss", 2: "loss"})

    async def test_link_mutation_waits_for_an_in_flight_poll(self) -> None:
        poll_started = asyncio.Event()
        release_poll = asyncio.Event()

        async def slow_poll(_: str | None = None) -> int:
            poll_started.set()
            await release_poll.wait()
            return 0

        add_user = AsyncMock()
        isolated_lock = asyncio.Lock()
        with (
            patch.object(tracker, "match_check_lock", isolated_lock),
            patch.object(tracker, "_check_matches_once", side_effect=slow_poll),
            patch.object(tracker, "add_user", add_user),
        ):
            poll_task = asyncio.create_task(tracker.check_matches_once())
            await poll_started.wait()
            link_task = asyncio.create_task(
                tracker.add_tracked_user(1, "new-puuid", "Player", "NA1")
            )
            await asyncio.sleep(0)
            add_user.assert_not_awaited()

            release_poll.set()
            await poll_task
            await link_task

        add_user.assert_awaited_once_with(1, "new-puuid", "Player", "NA1")


if __name__ == "__main__":
    unittest.main()
