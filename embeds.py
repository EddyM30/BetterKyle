#Chat am running out of jokes
import discord



QUEUE_NAMES = {

    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM"

}



def create_match_embed(
    match,
    party_players
):


    info = match["info"]



    victory = party_players[0]["win"]



    embed = discord.Embed(

        title=(

            "🏆 VICTORY"

            if victory

            else

            "💀 DEFEAT"

        ),

        color=(

            discord.Color.green()

            if victory

            else

            discord.Color.red()

        )

    )



    queue = QUEUE_NAMES.get(

        info["queueId"],

        f"Queue {info['queueId']}"

    )



    duration = (

        f"{info['gameDuration']//60}:"

        f"{info['gameDuration']%60:02}"

    )



    embed.add_field(

        name="Match Info",

        value=(

            f"🎮 {queue}\n"

            f"⏱️ {duration}\n"

            f"👥 Party Size: {len(party_players)}"

        ),

        inline=False

    )



    for player in party_players:



        highlights = []



        if player.get("pentaKills",0):

            highlights.append(

                f"💥 Pentakill x{player['pentaKills']}"

            )



        if player.get("quadraKills",0):

            highlights.append(

                f"💥 Quadra Kill x{player['quadraKills']}"

            )



        if player.get("tripleKills",0):

            highlights.append(

                f"⚔️ Triple Kill x{player['tripleKills']}"

            )



        if not highlights:

            highlights.append(
                "None"
            )



        embed.add_field(

            name=(

                f"👤 {player['summonerName']}"

            ),

            value=(

                f"🧙 Champion: **{player['championName']}**\n"

                f"⚔️ KDA: **"

                f"{player['kills']}/"

                f"{player['deaths']}/"

                f"{player['assists']}**\n"

                f"⭐ Highlights: "

                f"{', '.join(highlights)}"

            ),

            inline=False

        )



    return embed    return embed