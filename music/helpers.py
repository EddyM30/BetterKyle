"""Formatting and source-detection helpers shared by music commands."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

import discord
import wavelink

from config import MUSIC_QUEUE_DISPLAY_LIMIT
from music.state import MusicState


EMBED_FIELD_VALUE_LIMIT = 1024
QUEUE_TRACK_LABEL_LIMIT = 72


def _truncate(value: str, limit: int) -> str:
    """Bound user/source text to Discord's fixed embed component limits."""

    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def is_spotify_url(query: str) -> bool:
    """Return whether a query is an open.spotify.com URL."""

    parsed = urlparse(query.strip())
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in {
        "open.spotify.com",
        "www.open.spotify.com",
    }


def format_duration(milliseconds: int) -> str:
    """Format Lavalink milliseconds as M:SS or H:MM:SS."""

    total_seconds = max(0, int(milliseconds) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes}:{seconds:02}"


def track_metadata(track: wavelink.Playable) -> dict[str, str | int]:
    """Read JSON-safe requester/origin metadata attached at queue time."""

    extras = track.extras
    return {
        "title": getattr(extras, "display_title", track.title),
        "author": getattr(extras, "display_author", track.author),
        "requester": getattr(extras, "requester_name", "Unknown"),
        "requester_id": getattr(extras, "requester_id", 0),
        "origin": getattr(extras, "origin_source", track.source),
    }


def track_label(track: wavelink.Playable) -> str:
    """Return a compact artist/title label for queue output."""

    metadata = track_metadata(track)
    title = str(metadata["title"])
    author = str(metadata["author"])
    return f"{author} — {title}" if author and author != "Unknown" else title


def build_now_playing_embed(
    state: MusicState,
    player: wavelink.Player,
) -> discord.Embed:
    """Build a mode-aware now-playing embed without inventing radio duration."""

    if state.is_radio and state.radio_station is not None:
        station = state.radio_station
        embed = discord.Embed(
            title="📻 Now Playing",
            description=f"**{station.name}**\n{station.frequency} {station.callsign}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Source", value="Live Radio", inline=True)
        embed.add_field(name="Status", value="LIVE", inline=True)
        return embed

    track = player.current
    if track is None:
        raise ValueError("Nothing is currently playing")

    metadata = track_metadata(track)
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{track_label(track)}**",
        color=discord.Color.blurple(),
    )
    if track.is_stream:
        progress = "LIVE"
    else:
        progress = (
            f"{format_duration(player.position)} / {format_duration(track.length)}"
        )
    embed.add_field(name="Position", value=progress, inline=True)
    embed.add_field(name="Source", value=str(metadata["origin"]), inline=True)
    embed.add_field(name="Requested by", value=str(metadata["requester"]), inline=True)
    if track.uri:
        embed.url = track.uri
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


def build_queue_embed(
    player: wavelink.Player,
    upcoming: Iterable[wavelink.Playable],
    *,
    limit: int = MUSIC_QUEUE_DISPLAY_LIMIT,
) -> discord.Embed:
    """Build a bounded queue view that stays within Discord embed limits."""

    items = list(upcoming)
    embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blurple())
    if player.current is not None:
        embed.add_field(
            name="Now Playing",
            value=_truncate(track_label(player.current), EMBED_FIELD_VALUE_LIMIT),
            inline=False,
        )
    else:
        embed.add_field(name="Now Playing", value="Nothing", inline=False)

    if items:
        lines = [
            f"{index}. {_truncate(track_label(track), QUEUE_TRACK_LABEL_LIMIT)} · "
            f"{'LIVE' if track.is_stream else format_duration(track.length)}"
            for index, track in enumerate(items[:limit], start=1)
        ]
        remaining = len(items) - limit
        if remaining > 0:
            lines.append(f"\n…and {remaining} more")
        upcoming_value = _truncate(
            "\n".join(lines),
            EMBED_FIELD_VALUE_LIMIT,
        )
    else:
        upcoming_value = "No upcoming tracks."

    embed.add_field(name="Up Next", value=upcoming_value, inline=False)
    return embed
