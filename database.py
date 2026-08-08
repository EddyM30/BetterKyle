"""Async SQLite persistence for linked Riot accounts and match state."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path

import aiosqlite

from config import DATABASE_PATH


LOGGER = logging.getLogger(__name__)
DATABASE: Path = DATABASE_PATH


class AccountAlreadyLinkedError(RuntimeError):
    """Raised when a PUUID already belongs to another Discord account."""


@dataclass(frozen=True, slots=True)
class TrackedUser:
    """A linked Riot account loaded from the existing ``users`` table."""

    discord_id: int
    puuid: str
    riot_name: str
    riot_tag: str
    last_match: str | None
    announce_initial: bool
    streak_type: str
    streak_count: int


_ADDITIVE_USER_COLUMNS = {
    "announce_initial": "INTEGER DEFAULT 0",
    "streak_type": "TEXT DEFAULT 'none'",
    "streak_count": "INTEGER DEFAULT 0",
}


async def setup_database() -> None:
    """Create missing tables and apply only backwards-safe column additions."""

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                discord_id INTEGER PRIMARY KEY,
                puuid TEXT UNIQUE,
                riot_name TEXT,
                riot_tag TEXT,
                last_match TEXT,
                announce_initial INTEGER DEFAULT 0,
                streak_type TEXT DEFAULT 'none',
                streak_count INTEGER DEFAULT 0
            )
            """
        )

        cursor = await db.execute("PRAGMA table_info(users)")
        columns = {column[1] for column in await cursor.fetchall()}
        for name, declaration in _ADDITIVE_USER_COLUMNS.items():
            if name not in columns:
                LOGGER.info("Adding backwards-safe users.%s column", name)
                await db.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS matches(
                match_id TEXT PRIMARY KEY
            )
            """
        )
        await db.commit()


async def add_user(
    discord_id: int,
    puuid: str,
    name: str,
    tag: str,
) -> None:
    """Link an account without erasing state when the same account is relinked.

    Changing to a different Riot account intentionally starts a fresh marker
    and streak for that Discord user. A PUUID owned by another Discord user is
    rejected instead of relying on SQLite's destructive ``OR REPLACE`` rules.
    """

    async with aiosqlite.connect(DATABASE) as db:
        try:
            await db.execute(
                """
                INSERT INTO users(
                    discord_id,
                    puuid,
                    riot_name,
                    riot_tag,
                    announce_initial
                )
                VALUES(?, ?, ?, ?, 1)
                ON CONFLICT(discord_id) DO UPDATE SET
                    last_match = CASE
                        WHEN users.puuid = excluded.puuid THEN users.last_match
                        ELSE NULL
                    END,
                    announce_initial = CASE
                        WHEN users.puuid = excluded.puuid
                            THEN users.announce_initial
                        ELSE 1
                    END,
                    streak_type = CASE
                        WHEN users.puuid = excluded.puuid
                            THEN users.streak_type
                        ELSE 'none'
                    END,
                    streak_count = CASE
                        WHEN users.puuid = excluded.puuid
                            THEN users.streak_count
                        ELSE 0
                    END,
                    puuid = excluded.puuid,
                    riot_name = excluded.riot_name,
                    riot_tag = excluded.riot_tag
                """,
                (discord_id, puuid, name, tag),
            )
        except aiosqlite.IntegrityError as exc:
            await db.rollback()
            raise AccountAlreadyLinkedError(
                "That Riot account is already linked to another Discord user."
            ) from exc

        await db.commit()


async def delete_user(discord_id: int) -> bool:
    """Unlink one Discord user's Riot account."""

    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            "DELETE FROM users WHERE discord_id = ?",
            (discord_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_users() -> list[TrackedUser]:
    """Return every linked account using a named model instead of tuple offsets."""

    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT
                discord_id,
                puuid,
                riot_name,
                riot_tag,
                last_match,
                announce_initial,
                COALESCE(streak_type, 'none'),
                COALESCE(streak_count, 0)
            FROM users
            """
        )
        rows = await cursor.fetchall()

    return [
        TrackedUser(
            discord_id=row[0],
            puuid=row[1],
            riot_name=row[2],
            riot_tag=row[3],
            last_match=row[4],
            announce_initial=bool(row[5]),
            streak_type=row[6],
            streak_count=row[7],
        )
        for row in rows
    ]


async def get_saved_match_ids(match_ids: Collection[str]) -> set[str]:
    """Return the subset of match IDs already present in the dedup table."""

    if not match_ids:
        return set()

    placeholders = ", ".join("?" for _ in match_ids)
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            f"SELECT match_id FROM matches WHERE match_id IN ({placeholders})",
            tuple(match_ids),
        )
        return {row[0] for row in await cursor.fetchall()}


async def record_match_result(
    match_id: str,
    results_by_discord_id: Mapping[int, str],
) -> bool:
    """Atomically deduplicate a posted match and advance participant streaks.

    The unique match insert gates all streak changes, so a retry cannot count
    the same match twice even if Discord delivery and SQLite persistence are
    interrupted at an awkward boundary.
    """

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "INSERT OR IGNORE INTO matches(match_id) VALUES(?)",
            (match_id,),
        )
        if cursor.rowcount == 0:
            await db.rollback()
            return False

        for discord_id, result in results_by_discord_id.items():
            await db.execute(
                """
                UPDATE users
                SET
                    streak_count = CASE
                        WHEN streak_type = ? THEN streak_count + 1
                        ELSE 1
                    END,
                    streak_type = ?
                WHERE discord_id = ?
                """,
                (result, result, discord_id),
            )

        await db.commit()
        return True


async def finalize_poll(
    processed_match_ids: Collection[str],
    marker_updates: Mapping[int, str],
) -> None:
    """Persist excluded IDs and per-user cursors in one short transaction."""

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        if processed_match_ids:
            await db.executemany(
                "INSERT OR IGNORE INTO matches(match_id) VALUES(?)",
                ((match_id,) for match_id in processed_match_ids),
            )

        if marker_updates:
            await db.executemany(
                """
                UPDATE users
                SET last_match = ?, announce_initial = 0
                WHERE discord_id = ?
                """,
                (
                    (match_id, discord_id)
                    for discord_id, match_id in marker_updates.items()
                ),
            )

        await db.commit()
