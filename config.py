#Options

import os
import json
from pathlib import Path
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


with open(
    Path(__file__).parent / "data" / "config.json",
    encoding="utf-8"
) as config_file:

    game_config = json.load(config_file)


ALLOWED_QUEUES = frozenset(
    game_config["allowed_queues"]
)


CHECK_INTERVAL_MINUTES = game_config[
    "check_interval_minutes"
]

#Can add other regions later (we all US)

RIOT_REGION = (
    "https://americas.api.riotgames.com"
)
