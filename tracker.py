"""Match polling and publishing."""

import asyncio

from discord.ext import tasks

from database import (
    get_users,
    match_exists,
    save_match,
    update_last_match,
    update_streak
)

from riot_api import (
    get_recent_matches,
    get_match
)

from embeds import create_match_embed

from config import (
    ALLOWED_QUEUES,
    CHANNEL_ID,
    CHECK_INTERVAL_MINUTES
)


bot = None
match_check_lock = asyncio.Lock()



def set_bot(
    value
):

    global bot

    bot = value



def get_match_end_time(
    match
):

    info = match["info"]

    return info.get(
        "gameEndTimestamp"
    ) or info.get(
        "gameCreation",
        0
    )



async def check_matches_once(
    target_puuid=None
):

    """Check for unannounced matches and return how many were posted."""

    async with match_check_lock:
        return await _check_matches_once(
            target_puuid
        )


async def _check_matches_once(
    target_puuid=None
):

    users = await get_users()


    if target_puuid:

        users = [
            user
            for user in users
            if user[1] == target_puuid
        ]

    tracked = {
        user[1]: user
        for user in users
    }



    candidates = []
    excluded_match_ids = set()
    baseline_marker_updates = {}
    marker_updates = {}
    api_lookup_failed = False



    for user in users:


        matches = await get_recent_matches(
            user[1]
        )


        if not matches:

            continue



        recent_matches = []



        for match_id in matches:

            match = await get_match(
                match_id
            )



            if not match:

                # Do not choose an older match while Riot is still making a
                # newer one available through the match API.
                api_lookup_failed = True

                break



            recent_matches.append(
                (match_id, match)
            )



        if api_lookup_failed:

            continue



        match_id, match = max(
            recent_matches,
            key=lambda candidate: get_match_end_time(
                candidate[1]
            )
        )



        # Accounts restored from a reset silently establish a baseline.
        # Accounts linked with /riot link are marked to announce their latest
        # allowed match once, even though they do not have a marker yet.
        if user[4] is None and not user[5]:

            baseline_marker_updates[user[0]] = match_id

            continue



        if match_id == user[4]:

            continue



        marker_updates[user[0]] = match_id



        if await match_exists(
            match_id
        ):

            continue



        if match["info"].get("queueId") not in ALLOWED_QUEUES:

            excluded_match_ids.add(
                match_id
            )

            continue



        candidates.append(
            (match_id, match)
        )



    if api_lookup_failed:
        return 0



    posted_match = False



    if candidates:

        latest_match_id, latest_match = max(
            candidates,
            key=lambda candidate: get_match_end_time(
                candidate[1]
            )
        )



        party = []



        for player in latest_match["info"]["participants"]:


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

                    linked_user[0],

                    result

                )



        if party:

            channel = bot.get_channel(
                CHANNEL_ID
            )



            await channel.send(

                embed=create_match_embed(

                    latest_match,

                    party

                )

            )


            posted_match = True



        await save_match(
            latest_match_id
        )



        # Ignore other new matches from the same check. Only the newest
        # match should ever be announced.
        for match_id, _ in candidates:

            if match_id != latest_match_id:

                await save_match(
                    match_id
                )



    for match_id in excluded_match_ids:

        await save_match(
            match_id
        )



    all_marker_updates = {
        **baseline_marker_updates,
        **marker_updates
    }



    for discord_id, match_id in all_marker_updates.items():

        await update_last_match(
            discord_id,
            match_id
        )



    return int(posted_match)





@tasks.loop(
    minutes=CHECK_INTERVAL_MINUTES
)

async def check_matches():

    await check_matches_once()
