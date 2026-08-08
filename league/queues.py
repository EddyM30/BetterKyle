"""Authoritative supported Riot queue registry.

Queue identities come from Riot's public queue metadata:
https://static.developer.riotgames.com/docs/lol/queues.json

Live availability was rechecked on 2026-08-07 against Riot patch notes. Blind
Pick (430) was retired when Quickplay launched, and Quickplay (490) was retired
when Swiftplay completed its rollout. They are therefore not included even
though Riot's historical queue catalog still lists them without deprecation
notes.

Retirement/current-status sources:
https://www.leagueoflegends.com/en-us/news/game-updates/patch-13-22-notes/
https://www.leagueoflegends.com/en-sg/news/game-updates/patch-25-07-notes/
https://www.leagueoflegends.com/en-us/news/game-updates/league-of-legends-patch-26-13-notes/

Eligibility is always decided by this explicit queue-ID allowlist. A map name
or ``gameMode`` value is never sufficient because rotating and experimental
modes can share Summoner's Rift and broad mode labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QueueCategory(str, Enum):
    """Display and reporting category for a supported match queue."""

    ARAM = "aram"
    SUMMONERS_RIFT_RANKED = "summoners_rift_ranked"
    SUMMONERS_RIFT_NORMAL = "summoners_rift_normal"
    SWIFTPLAY = "swiftplay"


@dataclass(frozen=True, slots=True)
class QueueDefinition:
    """Metadata for one explicitly supported Riot queue."""

    queue_id: int
    display_name: str
    category: QueueCategory
    ranked: bool
    map_name: str


SUPPORTED_QUEUES: dict[int, QueueDefinition] = {
    400: QueueDefinition(
        queue_id=400,
        display_name="Summoner's Rift — Normal Draft",
        category=QueueCategory.SUMMONERS_RIFT_NORMAL,
        ranked=False,
        map_name="Summoner's Rift",
    ),
    420: QueueDefinition(
        queue_id=420,
        display_name="Summoner's Rift — Ranked Solo/Duo",
        category=QueueCategory.SUMMONERS_RIFT_RANKED,
        ranked=True,
        map_name="Summoner's Rift",
    ),
    440: QueueDefinition(
        queue_id=440,
        display_name="Summoner's Rift — Ranked Flex",
        category=QueueCategory.SUMMONERS_RIFT_RANKED,
        ranked=True,
        map_name="Summoner's Rift",
    ),
    450: QueueDefinition(
        queue_id=450,
        display_name="ARAM",
        category=QueueCategory.ARAM,
        ranked=False,
        map_name="Howling Abyss",
    ),
    480: QueueDefinition(
        queue_id=480,
        display_name="Swiftplay",
        category=QueueCategory.SWIFTPLAY,
        ranked=False,
        map_name="Summoner's Rift",
    ),
}

SUPPORTED_QUEUE_IDS = frozenset(SUPPORTED_QUEUES)


def get_queue_definition(queue_id: object) -> QueueDefinition | None:
    """Return supported metadata for ``queue_id`` or ``None`` by default."""

    return SUPPORTED_QUEUES.get(queue_id) if isinstance(queue_id, int) else None


def is_supported_queue(queue_id: object) -> bool:
    """Return whether a Match-V5 queue ID is explicitly supported."""

    return get_queue_definition(queue_id) is not None
