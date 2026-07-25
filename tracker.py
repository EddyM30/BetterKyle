#If this fails to complie its bc loverboy texted me
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



def set_bot(
    value
):

    global bot

    bot = value



async def check_matches_once():


    users = await get_users()


    tracked = {
        user[1]: user
        for user in users
    }



    processed = set()



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





@tasks.loop(
    minutes=5
)

async def check_matches():

    await check_matches_once()    await check_matches_once()