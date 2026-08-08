"""Discord presentation helpers for League match announcements."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import discord

from league.queues import get_queue_definition


def create_match_embed(
    match: Mapping[str, Any],
    party_players: Sequence[Mapping[str, Any]],
) -> discord.Embed:
    """Build the existing match result/highlight embed for linked players."""

    if not party_players:
        raise ValueError("party_players must contain at least one linked player")

    info = match["info"]
    victory = bool(party_players[0]["win"])
    embed = discord.Embed(
        title="🏆 VICTORY" if victory else "💀 DEFEAT",
        color=discord.Color.green() if victory else discord.Color.red(),
    )

    linked_players = []
    for player in party_players:
        riot_name = (
            player.get("riot_name")
            or player.get("riotIdGameName")
            or player.get("summonerName")
            or "Unknown player"
        )
        discord_id = player.get("discord_id")
        linked_players.append(
            f"<@{discord_id}> ({riot_name})" if discord_id else str(riot_name)
        )

    embed.description = f"Match detected for {', '.join(linked_players)}"

    queue_id = info.get("queueId")
    queue_definition = get_queue_definition(queue_id)
    queue_name = (
        queue_definition.display_name
        if queue_definition is not None
        else f"Queue {queue_id}"
    )
    duration_seconds = int(info.get("gameDuration", 0))
    duration = f"{duration_seconds // 60}:{duration_seconds % 60:02}"
    embed.add_field(
        name="Match Info",
        value=(
            f"🎮 {queue_name}\n⏱️ {duration}\n👥 Linked Players: {len(party_players)}"
        ),
        inline=False,
    )

    for player in party_players:
        highlights = []
        if player.get("pentaKills", 0):
            highlights.append(f"💥 Pentakill x{player['pentaKills']}")
        if player.get("quadraKills", 0):
            highlights.append(f"💥 Quadra Kill x{player['quadraKills']}")
        if player.get("tripleKills", 0):
            highlights.append(f"⚔️ Triple Kill x{player['tripleKills']}")

        in_game_name = (
            player.get("riotIdGameName")
            or player.get("summonerName")
            or player.get("riot_name")
            or "Unknown player"
        )
        discord_id = player.get("discord_id")
        discord_user = f" (<@{discord_id}>)" if discord_id else ""
        embed.add_field(
            name=f"👤 {in_game_name}{discord_user}",
            value=(
                f"🧙 Champion: **{player.get('championName', 'Unknown')}**\n"
                f"⚔️ KDA: **{player.get('kills', 0)}/"
                f"{player.get('deaths', 0)}/{player.get('assists', 0)}**\n"
                f"⭐ Highlights: {', '.join(highlights) if highlights else 'None'}"
            ),
            inline=False,
        )

    return embed
