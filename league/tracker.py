"""Periodic Match-V5 discovery, filtering, deduplication, and publishing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, cast

import discord
from discord.ext import tasks

from config import CHANNEL_ID, CHECK_INTERVAL_MINUTES, MATCH_LOOKBACK_COUNT
from database import (
    TrackedUser,
    add_user,
    delete_user,
    finalize_poll,
    get_saved_match_ids,
    get_users,
    record_match_result,
)
from league.embeds import create_match_embed
from league.queues import is_supported_queue
from league.riot_api import get_match, get_recent_matches


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """One eligible, not-yet-published Match-V5 payload."""

    match_id: str
    match: dict[str, Any]


bot: discord.Client | None = None
match_check_lock = asyncio.Lock()


def set_bot(value: discord.Client) -> None:
    """Provide the Discord client used for channel announcements."""

    global bot
    bot = value


async def add_tracked_user(
    discord_id: int,
    puuid: str,
    name: str,
    tag: str,
) -> None:
    """Serialize a link mutation against polling that holds an old user snapshot."""

    async with match_check_lock:
        await add_user(discord_id, puuid, name, tag)


async def remove_tracked_user(discord_id: int) -> bool:
    """Serialize unlinking so an in-flight poll cannot rewrite removed state."""

    async with match_check_lock:
        return await delete_user(discord_id)


def get_match_end_time(match: dict[str, Any]) -> int:
    """Return the best sortable completion timestamp in a Match-V5 payload."""

    info = match.get("info", {})
    return int(info.get("gameEndTimestamp") or info.get("gameCreation") or 0)


def _new_match_ids(user: TrackedUser, recent_ids: list[str]) -> list[str]:
    """Return newest-first IDs that appear before the user's durable marker."""

    if user.last_match is None:
        return recent_ids if user.announce_initial else []

    try:
        marker_index = recent_ids.index(user.last_match)
    except ValueError:
        # A long outage can move the marker beyond the bounded lookback. The
        # newest eligible match is still useful; older backlog is intentionally
        # not announced all at once for this personal server.
        return recent_ids

    return recent_ids[:marker_index]


async def check_matches_once(target_puuid: str | None = None) -> int:
    """Run one serialized match check and return announcements successfully sent."""

    async with match_check_lock:
        return await _check_matches_once(target_puuid)


async def _check_matches_once(target_puuid: str | None = None) -> int:
    all_users = await get_users()
    tracked_by_puuid = {user.puuid: user for user in all_users}
    discovery_users = (
        [user for user in all_users if user.puuid == target_puuid]
        if target_puuid is not None
        else all_users
    )
    if not discovery_users:
        return 0

    histories: dict[str, list[str]] = {}
    all_discovered_ids: set[str] = set()
    for user in discovery_users:
        recent_ids = await get_recent_matches(
            user.puuid,
            count=MATCH_LOOKBACK_COUNT,
        )
        if recent_ids is None:
            # An outage is distinct from an empty history. Advancing markers
            # after a partial Riot response could permanently hide a match.
            LOGGER.warning("Aborting match poll after Riot history failure")
            return 0

        histories[user.puuid] = recent_ids
        all_discovered_ids.update(recent_ids)

    saved_ids = await get_saved_match_ids(all_discovered_ids)
    details_cache: dict[str, dict[str, Any]] = {}
    candidates: dict[str, MatchCandidate] = {}
    excluded_ids: set[str] = set()
    marker_updates: dict[int, str] = {}

    for user in discovery_users:
        recent_ids = histories[user.puuid]
        if not recent_ids:
            continue

        marker_updates[user.discord_id] = recent_ids[0]
        for match_id in _new_match_ids(user, recent_ids):
            match = details_cache.get(match_id)
            if match is None:
                match = await get_match(match_id)
                if match is None:
                    LOGGER.warning(
                        "Aborting match poll because %s details are unavailable",
                        match_id,
                    )
                    return 0
                details_cache[match_id] = match

            queue_id = match.get("info", {}).get("queueId")
            if not is_supported_queue(queue_id):
                excluded_ids.add(match_id)
                continue

            # Preserve the bot's no-backlog behavior: locate the newest
            # supported match per linked account, while ensuring excluded modes
            # ahead of it cannot hide it. A dict deduplicates linked party members.
            if match_id not in saved_ids:
                candidates.setdefault(match_id, MatchCandidate(match_id, match))
            break

    posted_matches = 0
    processed_without_announcement = set(excluded_ids)
    for candidate in sorted(
        candidates.values(),
        key=lambda item: get_match_end_time(item.match),
    ):
        party: list[dict[str, Any]] = []
        results: dict[int, str] = {}
        participants = candidate.match.get("info", {}).get("participants", [])
        for participant in participants:
            linked_user = tracked_by_puuid.get(participant.get("puuid"))
            if linked_user is None:
                continue

            enriched = dict(participant)
            enriched["discord_id"] = linked_user.discord_id
            enriched["riot_name"] = linked_user.riot_name
            party.append(enriched)
            results[linked_user.discord_id] = (
                "win" if participant.get("win") else "loss"
            )

        if not party:
            LOGGER.warning(
                "No linked participant found in discovered match %s",
                candidate.match_id,
            )
            processed_without_announcement.add(candidate.match_id)
            continue

        if bot is None or CHANNEL_ID is None:
            raise RuntimeError("Discord announcement channel is not configured")
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None or not hasattr(channel, "send"):
            raise RuntimeError(f"Discord channel {CHANNEL_ID} is unavailable")

        await cast(Any, channel).send(embed=create_match_embed(candidate.match, party))
        if await record_match_result(candidate.match_id, results):
            posted_matches += 1
        else:
            LOGGER.warning(
                "Match %s was recorded concurrently after its send",
                candidate.match_id,
            )

    await finalize_poll(processed_without_announcement, marker_updates)
    return posted_matches


@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def check_matches() -> None:
    """Run periodic polling without allowing one failure to kill the loop."""

    try:
        await check_matches_once()
    except Exception:
        LOGGER.exception("Automatic League match polling failed")


@check_matches.before_loop
async def before_check_matches() -> None:
    """Wait for Discord readiness before the first announcement attempt."""

    if bot is not None:
        await bot.wait_until_ready()
