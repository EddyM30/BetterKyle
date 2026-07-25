#Chat am running out of jokes

import discord



def create_match_embed(
    match
):

    info = match["info"]


    victory = False


    for player in info["participants"]:

        if player["win"]:
            victory = True
            break



    embed = discord.Embed(

        title=(
            "VICTORY"
            if victory
            else
            "DEFEAT"
        ),

        color=(
            0x00ff00
            if victory
            else
            0xff0000
        )

    )



    embed.add_field(
        name="Queue",
        value=str(info["queueId"]),
        inline=True
    )


    embed.add_field(
        name="Duration",
        value=(
            f"{info['gameDuration']//60}:"
            f"{info['gameDuration']%60:02}"
        ),
        inline=True
    )



    for player in info["participants"]:


        text = (

            f"Champion:\n"
            f"{player['championName']}\n\n"

            f"KDA:\n"
            f"{player['kills']}/"
            f"{player['deaths']}/"
            f"{player['assists']}\n\n"

            f"Highlights:\n"

        )



        highlights = []



        if player["pentaKills"]:

            highlights.append(
                f"Pentakill x{player['pentaKills']}"
            )



        if player["quadraKills"]:

            highlights.append(
                f"Quadra Kill x{player['quadraKills']}"
            )



        if player["tripleKills"]:

            highlights.append(
                f"Triple Kill x{player['tripleKills']}"
            )



        if not highlights:

            highlights.append(
                "None"
            )



        text += "\n".join(
            highlights
        )



        embed.add_field(

            name=f"{player['summonerName']}",

            value=text,

            inline=False

        )



    return embed