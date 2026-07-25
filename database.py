#Talk to me SQL
#HATE SQL someone can look it over later
import aiosqlite


DATABASE = "users.db"


async def setup_database():

    async with aiosqlite.connect(DATABASE) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users(

            discord_id INTEGER PRIMARY KEY,

            puuid TEXT UNIQUE,

            riot_name TEXT,

            riot_tag TEXT,

            last_match TEXT,

            announce_initial INTEGER DEFAULT 0,

            streak_type TEXT DEFAULT 'none',

            streak_count INTEGER DEFAULT 0

        )
        """)


        cursor = await db.execute(
            "PRAGMA table_info(users)"
        )

        columns = {
            column[1]
            for column in await cursor.fetchall()
        }


        if "announce_initial" not in columns:

            await db.execute(
                """
                ALTER TABLE users
                ADD COLUMN announce_initial INTEGER DEFAULT 0
                """
            )


        await db.execute("""
        CREATE TABLE IF NOT EXISTS matches(

            match_id TEXT PRIMARY KEY

        )
        """)


        await db.commit()



async def add_user(
    discord_id,
    puuid,
    name,
    tag
):

    async with aiosqlite.connect(DATABASE) as db:

        await db.execute(
        """
        INSERT OR REPLACE INTO users
        (
            discord_id,
            puuid,
            riot_name,
            riot_tag,
            announce_initial
        )

        VALUES(?,?,?,?,?)

        """,
        (
            discord_id,
            puuid,
            name,
            tag,
            1
        ))

        await db.commit()



async def delete_user(
    discord_id
):

    async with aiosqlite.connect(DATABASE) as db:

        cursor = await db.execute(
        """
        DELETE FROM users
        WHERE discord_id=?
        """,
        (discord_id,)
        )

        await db.commit()

        return cursor.rowcount > 0



async def get_users():

    async with aiosqlite.connect(DATABASE) as db:

        cursor = await db.execute(
        """
        SELECT
            discord_id,
            puuid,
            riot_name,
            riot_tag,
            last_match,
            announce_initial,
            streak_type,
            streak_count
        FROM users
        """
        )

        return await cursor.fetchall()



async def get_user_by_puuid(
    puuid
):

    async with aiosqlite.connect(DATABASE) as db:

        cursor = await db.execute(
        """
        SELECT *
        FROM users
        WHERE puuid=?
        """,
        (puuid,)
        )

        return await cursor.fetchone()



async def update_last_match(
    discord_id,
    match_id
):

    async with aiosqlite.connect(DATABASE) as db:

        await db.execute(
        """
        UPDATE users
        SET
            last_match=?,
            announce_initial=0
        WHERE discord_id=?
        """,
        (
            match_id,
            discord_id
        )
        )

        await db.commit()



async def update_streak(
    discord_id,
    result
):

    async with aiosqlite.connect(DATABASE) as db:

        cursor = await db.execute(
        """
        SELECT streak_type, streak_count
        FROM users
        WHERE discord_id=?
        """,
        (discord_id,)
        )

        old = await cursor.fetchone()


        if old:

            streak_type, count = old


            if streak_type == result:

                count += 1

            else:

                count = 1



            await db.execute(
            """
            UPDATE users

            SET

            streak_type=?,
            streak_count=?

            WHERE discord_id=?

            """,

            (
                result,
                count,
                discord_id
            ))

        await db.commit()



async def match_exists(
    match_id
):

    async with aiosqlite.connect(DATABASE) as db:

        cursor = await db.execute(
        """
        SELECT match_id
        FROM matches
        WHERE match_id=?
        """,
        (match_id,)
        )

        return await cursor.fetchone()



async def save_match(
    match_id
):

    async with aiosqlite.connect(DATABASE) as db:

        await db.execute(
        """
        INSERT OR IGNORE INTO matches
        VALUES(?)
        """,
        (match_id,)
        )

        await db.commit()
