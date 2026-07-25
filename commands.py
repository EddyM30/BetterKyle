#Make it do da ting

import discord
from discord import app_commands

from riot_api import get_account
from database import add_user



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



        await interaction.followup.send(
            f"Linked {account['gameName']}#{account['tagLine']}"
        )



    tree.add_command(
        riot_group
    )