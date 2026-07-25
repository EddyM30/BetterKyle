#Talk to me Riot

import aiohttp

from config import RIOT_KEY, RIOT_REGION


HEADERS = {
    "X-Riot-Token": RIOT_KEY
}



async def riot_request(url):

    async with aiohttp.ClientSession(
        headers=HEADERS
    ) as session:

        async with session.get(url) as response:

            if response.status != 200:
                print(
                    "Riot API Error:",
                    response.status
                )
                return None

            return await response.json()



async def get_account(
    name,
    tag
):

    url = (
        f"{RIOT_REGION}/riot/account/v1/"
        f"accounts/by-riot-id/{name}/{tag}"
    )

    return await riot_request(url)



async def get_recent_matches(
    puuid
):

    url = (
        f"{RIOT_REGION}/lol/match/v5/"
        f"matches/by-puuid/{puuid}/ids"
        "?start=0&count=1"
    )

    return await riot_request(url)



async def get_match(
    match_id
):

    url = (
        f"{RIOT_REGION}/lol/match/v5/"
        f"matches/{match_id}"
    )

    return await riot_request(url)
