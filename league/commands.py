"""Guild-specific slash commands for Riot account linking and refreshes."""

from __future__ import annotations

import logging
import math
import time

import discord
from discord import app_commands

from config import REFRESH_COOLDOWN_SECONDS
from database import AccountAlreadyLinkedError
from league.riot_api import get_account
from league.tracker import add_tracked_user, check_matches_once, remove_tracked_user


LOGGER = logging.getLogger(__name__)
refresh_cooldowns: dict[int, float] = {}


def setup_league_commands(
    tree: app_commands.CommandTree,
    guild: discord.Object,
) -> None:
    """Register BetterKyle's League commands directly to its single guild."""

    riot_group = app_commands.Group(name="riot", description="League commands")

    @riot_group.command(name="link", description="Link Riot account")
    @app_commands.describe(riot_id="Example: czg#408")
    async def link(interaction: discord.Interaction, riot_id: str) -> None:
        """Link the invoking Discord user to a Riot ID."""

        await interaction.response.defer(thinking=True)
        if "#" not in riot_id:
            await interaction.followup.send("Use the format GameName#TagLine.")
            return

        name, tag = (part.strip() for part in riot_id.split("#", 1))
        if not name or not tag:
            await interaction.followup.send("Use the format GameName#TagLine.")
            return

        account = await get_account(name, tag)
        if not account:
            await interaction.followup.send(
                "That account could not be found. If the Riot API is having "
                "trouble, please try again shortly."
            )
            return

        try:
            await add_tracked_user(
                interaction.user.id,
                account["puuid"],
                account["gameName"],
                account["tagLine"],
            )
        except AccountAlreadyLinkedError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        try:
            posted_matches = await check_matches_once(account["puuid"])
        except Exception:
            LOGGER.exception("Initial match check failed after linking Riot account")
            posted_matches = 0

        match_message = " Latest supported match announced." if posted_matches else ""
        await interaction.followup.send(
            f"Linked {account['gameName']}#{account['tagLine']}.{match_message}"
        )

    tree.add_command(riot_group, guild=guild)

    @tree.command(
        name="unlink",
        description="Unlink your Riot account",
        guild=guild,
    )
    async def unlink(interaction: discord.Interaction) -> None:
        """Remove the invoking user's Riot link without touching match history."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        unlinked = await remove_tracked_user(interaction.user.id)
        message = (
            "Your Riot account has been unlinked."
            if unlinked
            else "You do not have a linked Riot account."
        )
        await interaction.followup.send(message, ephemeral=True)

    @tree.command(
        name="refresh",
        description="Check for new League matches now",
        guild=guild,
    )
    async def refresh(interaction: discord.Interaction) -> None:
        """Run a manual poll with the existing per-user in-memory cooldown."""

        now = time.monotonic()
        last_refresh = refresh_cooldowns.get(interaction.user.id)
        if last_refresh is not None:
            remaining = REFRESH_COOLDOWN_SECONDS - (now - last_refresh)
            if remaining > 0:
                await interaction.response.send_message(
                    f"You can use /refresh again in {math.ceil(remaining)} seconds.",
                    ephemeral=True,
                )
                return

        # Record before awaiting so simultaneous interactions cannot bypass the
        # cooldown while the serialized Match-V5 check is in progress.
        refresh_cooldowns[interaction.user.id] = now
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            posted_matches = await check_matches_once()
        except Exception:
            LOGGER.exception("Manual League refresh failed")
            await interaction.followup.send(
                "The refresh failed. BetterKyle will retry automatically.",
                ephemeral=True,
            )
            return

        if posted_matches:
            suffix = "" if posted_matches == 1 else "s"
            message = (
                f"Refresh complete — posted {posted_matches} new match "
                f"announcement{suffix}."
            )
        else:
            message = "Refresh complete — no new matches found."

        await interaction.followup.send(message, ephemeral=True)
