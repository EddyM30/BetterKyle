"""Match polling and publishing."""

import asyncio

from discord.ext import tasks

from database import (
    get_users,
    match_exists,
    save_match,
    update_streak
)

from riot_api import (
    get_recent_matches,
    get_match
)

from embeds import create_match_embed

from config import CHANNEL_ID


bot = None
match_check_lock = asyncio.Lock()



def set_bot(
    value
):

    global bot

    bot = value



async def check_matches_once():

    """Check for unannounced matches and return how many were posted."""

    async with match_check_lock:
        return await _check_matches_once()


async def _check_matches_once():

    users = await get_users()


    tracked = {
        user[1]: user
        for user in users
    }



    processed = set()
    posted_matches = 0



    for user in users:


        puuid = user[1]


        matches = await get_recent_matches(
            puuid
        )


        if not matches:

            continue



        match_id = matches[0]



        if match_id in processed:

            continue



        if await match_exists(
            match_id
        ):

            continue



        processed.add(
            match_id
        )



        match = await get_match(
            match_id
        )



        if not match:

            continue



        party = []



        for player in match["info"]["participants"]:


            if player["puuid"] in tracked:

                linked_user = tracked[player["puuid"]]

                # Keep the Discord user alongside the Riot participant so the
                # announcement can say whose linked account played the match.
                player["discord_id"] = linked_user[0]
                player["riot_name"] = linked_user[2]

                party.append(
                    player
                )


                result = (
                    "win"
                    if player["win"]
                    else
                    "loss"
                )


                await update_streak(

                    tracked[player["puuid"]][0],

                    result

                )



        if not party:

            continue



        channel = bot.get_channel(
            CHANNEL_ID
        )



        await channel.send(

            embed=create_match_embed(

                match,

                party

            )

        )



        await save_match(
            match_id
        )

        posted_matches += 1


    return posted_matches





@tasks.loop(
    minutes=2
)

async def check_matches():

    await check_matches_once()
