"""Tests for the explicit Riot queue allowlist."""

from __future__ import annotations

import unittest

from league.queues import (
    QueueCategory,
    SUPPORTED_QUEUE_IDS,
    SUPPORTED_QUEUES,
    get_queue_definition,
    is_supported_queue,
)


class SupportedQueueTests(unittest.TestCase):
    """Pin the complete allowlist so broad queue acceptance cannot return."""

    def test_allowlist_contains_exactly_the_intended_current_queues(self) -> None:
        self.assertEqual(SUPPORTED_QUEUE_IDS, frozenset({400, 420, 440, 450, 480}))
        self.assertEqual(set(SUPPORTED_QUEUES), set(SUPPORTED_QUEUE_IDS))

        expected = {
            400: (
                "Summoner's Rift — Normal Draft",
                QueueCategory.SUMMONERS_RIFT_NORMAL,
                False,
                "Summoner's Rift",
            ),
            420: (
                "Summoner's Rift — Ranked Solo/Duo",
                QueueCategory.SUMMONERS_RIFT_RANKED,
                True,
                "Summoner's Rift",
            ),
            440: (
                "Summoner's Rift — Ranked Flex",
                QueueCategory.SUMMONERS_RIFT_RANKED,
                True,
                "Summoner's Rift",
            ),
            450: ("ARAM", QueueCategory.ARAM, False, "Howling Abyss"),
            480: ("Swiftplay", QueueCategory.SWIFTPLAY, False, "Summoner's Rift"),
        }

        actual = {
            queue_id: (
                definition.display_name,
                definition.category,
                definition.ranked,
                definition.map_name,
            )
            for queue_id, definition in SUPPORTED_QUEUES.items()
        }
        self.assertEqual(actual, expected)
        for queue_id, definition in SUPPORTED_QUEUES.items():
            self.assertEqual(definition.queue_id, queue_id)

    def test_known_excluded_queues_and_non_integer_ids_are_rejected(self) -> None:
        excluded_queue_ids = {
            0,  # custom games
            430,  # retired normal blind
            490,  # retired quickplay
            700,  # Clash
            830,  # Co-op vs AI
            900,  # URF
            1020,  # One For All
            1300,  # Nexus Blitz
            1400,  # Ultimate Spellbook
            1700,  # Arena
            1810,  # Swarm/alternate mode family
            2000,  # tutorial
            2400,  # ARAM Mayhem
        }
        for queue_id in excluded_queue_ids:
            with self.subTest(queue_id=queue_id):
                self.assertFalse(is_supported_queue(queue_id))
                self.assertIsNone(get_queue_definition(queue_id))

        for invalid in (None, "450", 450.0, True, object()):
            with self.subTest(invalid=invalid):
                self.assertFalse(is_supported_queue(invalid))
                self.assertIsNone(get_queue_definition(invalid))


if __name__ == "__main__":
    unittest.main()
