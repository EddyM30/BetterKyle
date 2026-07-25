#Make it do da ting

import discord
from discord import app_commands
import math
import time

from riot_api import get_account
from database import add_user, delete_user
from tracker import check_matches_once


REFRESH_COOLDOWN_SECONDS = 120
refresh_cooldowns = {}



async def setup_commands(tree):


    riot_group = app_commands.Group(
        name="riot",
        description="League commands"
    )



    @riot_group.command(
        name="link",
        description="Link Riot account"
    )
    @app_commands.describe(
        riot_id="Example: czg#408"
    )
    async def link(
        interaction: discord.Interaction,
        riot_id: str
    ):

        await interaction.response.defer()



        if "#" not in riot_id:

            await interaction.followup.send(
                "Use format Name#Tag"
            )

            return



        name, tag = riot_id.split(
            "#",
            1
        )



        account = await get_account(
            name,
            tag
        )


        if not account:

            await interaction.followup.send(
                "Account not found"
            )

            return



        await add_user(

            interaction.user.id,

            account["puuid"],

            account["gameName"],

            account["tagLine"]

        )



        posted_match = await check_matches_once(
            account["puuid"]
        )


        match_message = (
            " Latest allowed match announced."
            if posted_match
            else ""
        )



        await interaction.followup.send(
            f"Linked {account['gameName']}#{account['tagLine']}"
            f".{match_message}"
        )



    tree.add_command(
        riot_group
    )


    @tree.command(
        name="unlink",
        description="Unlink your Riot account"
    )
    async def unlink(interaction: discord.Interaction):

        await interaction.response.defer(
            ephemeral=True
        )


        unlinked = await delete_user(
            interaction.user.id
        )


        message = (
            "Your Riot account has been unlinked."
            if unlinked
            else "You do not have a linked Riot account."
        )


        await interaction.followup.send(
            message,
            ephemeral=True
        )


    @tree.command(
        name="refresh",
        description="Check for new League matches now"
    )
    async def refresh(interaction: discord.Interaction):

        now = time.monotonic()
        last_refresh = refresh_cooldowns.get(interaction.user.id)

        if last_refresh is not None:

            elapsed = now - last_refresh
            remaining = REFRESH_COOLDOWN_SECONDS - elapsed

            if remaining > 0:

                await interaction.response.send_message(
                    "You can use /refresh again in "
                    f"{math.ceil(remaining)} seconds.",
                    ephemeral=True
                )

                return


        # Record the request before the API check so repeated requests cannot
        # be used to bypass the two-minute limit while a check is in progress.
        refresh_cooldowns[interaction.user.id] = now

        await interaction.response.defer(ephemeral=True)

        posted_matches = await check_matches_once()

        if posted_matches:

            message = (
                "Refresh complete — posted "
                f"{posted_matches} new match announcement"
                f"{'s' if posted_matches != 1 else ''}."
            )

        else:

            message = "Refresh complete — no new matches found."


        await interaction.followup.send(
            message,
            ephemeral=True
        )
