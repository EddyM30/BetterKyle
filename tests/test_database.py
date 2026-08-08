"""SQLite safety and idempotency tests using an isolated temporary database."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aiosqlite

import database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    """Verify account updates preserve state and match writes are atomic."""

    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "betterkyle-test.db"
        self.database_patch = patch.object(database, "DATABASE", self.database_path)
        self.database_patch.start()
        await database.setup_database()

    async def asyncTearDown(self) -> None:
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    async def _set_state(
        self,
        discord_id: int,
        *,
        last_match: str,
        streak_type: str,
        streak_count: int,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                UPDATE users
                SET last_match = ?, announce_initial = 0,
                    streak_type = ?, streak_count = ?
                WHERE discord_id = ?
                """,
                (last_match, streak_type, streak_count, discord_id),
            )
            await connection.commit()

    async def test_same_account_relink_updates_identity_without_erasing_state(
        self,
    ) -> None:
        await database.add_user(1, "puuid-one", "Old Name", "OLD")
        await self._set_state(
            1,
            last_match="NA1_EXISTING",
            streak_type="win",
            streak_count=4,
        )

        await database.add_user(1, "puuid-one", "New Name", "NEW")

        users = await database.get_users()
        self.assertEqual(len(users), 1)
        user = users[0]
        self.assertEqual((user.riot_name, user.riot_tag), ("New Name", "NEW"))
        self.assertEqual(user.last_match, "NA1_EXISTING")
        self.assertFalse(user.announce_initial)
        self.assertEqual((user.streak_type, user.streak_count), ("win", 4))

    async def test_conflicting_puuid_is_rejected_without_modifying_owner(self) -> None:
        await database.add_user(1, "shared-puuid", "Owner", "NA1")
        await self._set_state(
            1,
            last_match="NA1_OWNER",
            streak_type="loss",
            streak_count=2,
        )

        with self.assertRaises(database.AccountAlreadyLinkedError):
            await database.add_user(2, "shared-puuid", "Intruder", "EUW")

        users = await database.get_users()
        self.assertEqual(len(users), 1)
        owner = users[0]
        self.assertEqual(owner.discord_id, 1)
        self.assertEqual(owner.puuid, "shared-puuid")
        self.assertEqual(owner.last_match, "NA1_OWNER")
        self.assertEqual((owner.streak_type, owner.streak_count), ("loss", 2))

    async def test_switching_accounts_resets_only_that_discord_users_cursor_and_streak(
        self,
    ) -> None:
        await database.add_user(1, "old-puuid", "Old", "NA1")
        await self._set_state(
            1,
            last_match="NA1_OLD",
            streak_type="win",
            streak_count=7,
        )

        await database.add_user(1, "new-puuid", "New", "NA1")

        user = (await database.get_users())[0]
        self.assertEqual(user.puuid, "new-puuid")
        self.assertIsNone(user.last_match)
        self.assertTrue(user.announce_initial)
        self.assertEqual((user.streak_type, user.streak_count), ("none", 0))

    async def test_recording_same_match_twice_advances_streak_only_once(self) -> None:
        await database.add_user(1, "puuid-one", "Player", "NA1")

        first_write = await database.record_match_result("NA1_MATCH", {1: "win"})
        retry_write = await database.record_match_result("NA1_MATCH", {1: "win"})

        self.assertTrue(first_write)
        self.assertFalse(retry_write)
        user = (await database.get_users())[0]
        self.assertEqual((user.streak_type, user.streak_count), ("win", 1))
        self.assertEqual(
            await database.get_saved_match_ids({"NA1_MATCH", "NA1_MISSING"}),
            {"NA1_MATCH"},
        )

    async def test_legacy_user_row_survives_additive_schema_migration(self) -> None:
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        async with aiosqlite.connect(legacy_path) as connection:
            await connection.execute(
                """
                CREATE TABLE users(
                    discord_id INTEGER PRIMARY KEY,
                    puuid TEXT UNIQUE,
                    riot_name TEXT,
                    riot_tag TEXT,
                    last_match TEXT
                )
                """
            )
            await connection.execute(
                "INSERT INTO users VALUES(?, ?, ?, ?, ?)",
                (7, "legacy-puuid", "Legacy Player", "NA1", "NA1_OLD"),
            )
            await connection.execute("CREATE TABLE matches(match_id TEXT PRIMARY KEY)")
            await connection.execute(
                "INSERT INTO matches VALUES(?)",
                ("NA1_RECORDED",),
            )
            await connection.commit()

        with patch.object(database, "DATABASE", legacy_path):
            await database.setup_database()
            users = await database.get_users()
            saved = await database.get_saved_match_ids({"NA1_RECORDED"})

        self.assertEqual(len(users), 1)
        user = users[0]
        self.assertEqual(user.discord_id, 7)
        self.assertEqual(user.puuid, "legacy-puuid")
        self.assertEqual(user.last_match, "NA1_OLD")
        self.assertFalse(user.announce_initial)
        self.assertEqual((user.streak_type, user.streak_count), ("none", 0))
        self.assertEqual(saved, {"NA1_RECORDED"})


if __name__ == "__main__":
    unittest.main()
