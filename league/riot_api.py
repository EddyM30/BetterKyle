"""Small asynchronous client for Riot Account-V1 and Match-V5."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from config import (
    RIOT_API_BASE_URL,
    RIOT_API_KEY,
    RIOT_REQUEST_ATTEMPTS,
    RIOT_REQUEST_TIMEOUT_SECONDS,
)


LOGGER = logging.getLogger(__name__)


class RiotAPIClient:
    """Reuse one HTTP session and provide restrained retry/error handling."""

    def __init__(self, api_key: str, base_url: str = RIOT_API_BASE_URL) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=RIOT_REQUEST_TIMEOUT_SECONDS)
                    self._session = aiohttp.ClientSession(
                        headers={"X-Riot-Token": self.api_key},
                        timeout=timeout,
                    )
        return self._session

    async def close(self) -> None:
        """Close the reusable HTTP session during bot shutdown."""

        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(
        self,
        path: str,
        *,
        params: Mapping[str, int] | None = None,
    ) -> Any | None:
        """Request JSON, returning ``None`` for a logged Riot/API failure."""

        url = f"{self.base_url}{path}"
        for attempt in range(1, RIOT_REQUEST_ATTEMPTS + 1):
            session = await self._get_session()
            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()

                    if response.status == 404:
                        LOGGER.debug("Riot resource not found: %s", path)
                        return None

                    retryable = response.status == 429 or response.status >= 500
                    if response.status == 429:
                        LOGGER.warning("Riot API rate limit reached for %s", path)
                    else:
                        LOGGER.error(
                            "Riot API returned HTTP %s for %s",
                            response.status,
                            path,
                        )

                    if not retryable or attempt == RIOT_REQUEST_ATTEMPTS:
                        return None

                    retry_after = response.headers.get("Retry-After", "1")
                    try:
                        delay = min(max(float(retry_after), 0.25), 5.0)
                    except ValueError:
                        delay = 1.0
            except (aiohttp.ClientError, TimeoutError):
                LOGGER.warning(
                    "Riot request failed (%s/%s): %s",
                    attempt,
                    RIOT_REQUEST_ATTEMPTS,
                    path,
                    exc_info=attempt == RIOT_REQUEST_ATTEMPTS,
                )
                if attempt == RIOT_REQUEST_ATTEMPTS:
                    return None
                delay = min(float(attempt), 2.0)

            await asyncio.sleep(delay)

        return None

    async def get_account(self, name: str, tag: str) -> dict[str, Any] | None:
        """Resolve a Riot ID (``GameName#TagLine``) through Account-V1."""

        encoded_name = quote(name, safe="")
        encoded_tag = quote(tag, safe="")
        return await self.request(
            f"/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}"
        )

    async def get_recent_matches(
        self,
        puuid: str,
        *,
        count: int,
    ) -> list[str] | None:
        """Return recent Match-V5 IDs without applying a broad mode filter."""

        encoded_puuid = quote(puuid, safe="")
        result = await self.request(
            f"/lol/match/v5/matches/by-puuid/{encoded_puuid}/ids",
            params={"start": 0, "count": count},
        )
        return result if isinstance(result, list) else None

    async def get_match(self, match_id: str) -> dict[str, Any] | None:
        """Return one Match-V5 match payload."""

        encoded_match_id = quote(match_id, safe="")
        result = await self.request(f"/lol/match/v5/matches/{encoded_match_id}")
        return result if isinstance(result, dict) else None


_CLIENT: RiotAPIClient | None = None


def _client() -> RiotAPIClient:
    global _CLIENT
    if _CLIENT is None:
        if not RIOT_API_KEY:
            raise RuntimeError("RIOT_API_KEY is required before using the Riot API")
        _CLIENT = RiotAPIClient(RIOT_API_KEY)
    return _CLIENT


async def get_account(name: str, tag: str) -> dict[str, Any] | None:
    """Resolve a Riot account through the process-wide client."""

    return await _client().get_account(name, tag)


async def get_recent_matches(puuid: str, *, count: int) -> list[str] | None:
    """Load recent match IDs through the process-wide client."""

    return await _client().get_recent_matches(puuid, count=count)


async def get_match(match_id: str) -> dict[str, Any] | None:
    """Load match details through the process-wide client."""

    return await _client().get_match(match_id)


async def close_riot_client() -> None:
    """Release the process-wide Riot HTTP session."""

    if _CLIENT is not None:
        await _CLIENT.close()
