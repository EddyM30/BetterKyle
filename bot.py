#The Main Script 7/24/26

import discord
from discord.ext import tasks
from dotenv import load_dotenv
import os

from database import setup_database
from commands import setup_commands
from tracker import check_matches, set_bot
from config import GUILD_ID


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")


intents = discord.Intents.default()

bot = discord.Client(
    intents=intents
)

tree = discord.app_commands.CommandTree(bot)
guild = discord.Object(id=GUILD_ID)
commands_registered = False


@bot.event
async def on_ready():

    global commands_registered

    print(f"Logged in as {bot.user}")

    await setup_database()

    if not commands_registered:

        await setup_commands(tree)

        # Guild command sync is nearly immediate. Global commands can take up
        # to an hour to appear, which makes newly added commands look missing.
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)

        commands_registered = True

    set_bot(bot)

    if not check_matches.is_running():
        check_matches.start()



bot.run(TOKEN)
