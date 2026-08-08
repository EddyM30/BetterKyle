"""Application settings for BetterKyle.

Secrets and machine-specific values come from ``.env``. Stable application
behavior belongs here or in the small source-controlled registries under
``league`` and ``music``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _optional_int(name: str) -> int | None:
    """Return an integer environment value, leaving missing values unset."""

    value = os.getenv(name)
    if value is None or not value.strip():
        return None

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


# Existing production variable names are intentionally preserved.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = _optional_int("GUILD_ID")
CHANNEL_ID = _optional_int("CHANNEL_ID")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

# Riot Account-V1 and Match-V5 use the regional Americas routing host for NA.
RIOT_API_BASE_URL = "https://americas.api.riotgames.com"
RIOT_REQUEST_TIMEOUT_SECONDS = 10
RIOT_REQUEST_ATTEMPTS = 2

# Polling and refresh behavior are normal application configuration, not
# deployment secrets.
CHECK_INTERVAL_MINUTES = 2
REFRESH_COOLDOWN_SECONDS = 120
MATCH_LOOKBACK_COUNT = 20

# Keeping this path anchored to the repository prevents systemd's working
# directory from accidentally creating a second, empty SQLite database.
DATABASE_PATH = PROJECT_ROOT / "users.db"

# Lavalink is optional at bot startup. Missing/unavailable music configuration
# must not prevent League polling and commands from loading.
LAVALINK_URI = os.getenv("LAVALINK_URI", "http://127.0.0.1:2333")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_CONNECT_RETRIES = 3
MUSIC_DEFAULT_VOLUME = 100
MUSIC_IDLE_TIMEOUT_SECONDS = 300
MUSIC_QUEUE_DISPLAY_LIMIT = 10
SPOTIFY_RESOLVE_CONCURRENCY = 5


def validate_core_settings() -> None:
    """Raise a single actionable error for missing League/bot settings."""

    required = {
        "DISCORD_TOKEN": DISCORD_TOKEN,
        "GUILD_ID": GUILD_ID,
        "CHANNEL_ID": CHANNEL_ID,
        "RIOT_API_KEY": RIOT_API_KEY,
    }
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {names}")
