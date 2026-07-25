#Options

import os
from dotenv import load_dotenv


load_dotenv()


RIOT_KEY = os.getenv(
    "RIOT_API_KEY"
)


CHANNEL_ID = int(
    os.getenv("CHANNEL_ID")
)


GUILD_ID = int(
    os.getenv("GUILD_ID")
)

#Can add other regions later (we all US)

RIOT_REGION = (
    "https://americas.api.riotgames.com"
)