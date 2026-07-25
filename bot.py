#The Main Script 7/24/26

import discord
from discord.ext import tasks
from dotenv import load_dotenv
import os

from database import setup_database
from commands import setup_commands
from tracker import check_matches, set_bot


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")


intents = discord.Intents.default()

bot = discord.Client(
    intents=intents
)

tree = discord.app_commands.CommandTree(bot)


@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

    await setup_database()

    await setup_commands(tree)

    await tree.sync()

    set_bot(bot)

    if not check_matches.is_running():
        check_matches.start()



bot.run(TOKEN)