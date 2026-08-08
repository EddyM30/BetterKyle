"""BetterKyle Discord entry point."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
import wavelink

from config import DISCORD_TOKEN, GUILD_ID, validate_core_settings
from database import setup_database
from league.commands import setup_league_commands
from league.riot_api import close_riot_client
from league.tracker import check_matches, set_bot
from music.commands import setup_music_commands
from music.player import MusicController


LOGGER = logging.getLogger(__name__)


class BetterKyleClient(discord.Client):
    """Single-guild Discord client with isolated League and music lifecycles."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(intents=discord.Intents.default())
        self.guild_object = discord.Object(id=guild_id)
        self.tree = app_commands.CommandTree(self)
        self.music = MusicController(self)
        self.guild_commands_synced = False
        self.tree.error(self.on_tree_error)

    async def sync_guild_commands(self) -> bool:
        """Publish the one-guild command set, allowing a ready-time retry."""

        try:
            synced = await self.tree.sync(guild=self.guild_object)
        except discord.DiscordException:
            LOGGER.exception("Guild command sync failed")
            return False

        self.guild_commands_synced = True
        LOGGER.info("Synced %s guild commands", len(synced))
        return True

    async def setup_hook(self) -> None:
        """Initialize durable state and guild commands before ready events."""

        try:
            await setup_database()
        except Exception:
            LOGGER.exception("Database initialization failed; startup aborted")
            raise
        set_bot(self)
        setup_league_commands(self.tree, self.guild_object)
        setup_music_commands(self.tree, self.guild_object, self.music)

        # BetterKyle permanently serves one guild. Clear stale global copies
        # from older builds, then publish only the guild-specific command set.
        self.tree.clear_commands(guild=None)
        await self.sync_guild_commands()

        try:
            await self.tree.sync()
        except discord.DiscordException:
            LOGGER.exception("Stale global command cleanup failed")

        # This bounded background attempt cannot delay or disable League startup.
        self.music.start_node_connection()

    async def on_ready(self) -> None:
        LOGGER.info("Connected to Discord as %s", self.user)
        if not check_matches.is_running():
            check_matches.start()
        if not self.guild_commands_synced:
            # A setup-time sync outage must not delay polling, but a successful
            # gateway connection gives the guild commands one immediate retry.
            await self.sync_guild_commands()

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Return a clean Discord error while retaining a technical traceback."""

        underlying = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        LOGGER.error(
            "Unhandled slash-command failure in %s",
            interaction.command.name if interaction.command else "unknown command",
            exc_info=(type(underlying), underlying, underlying.__traceback__),
        )
        message = "That command failed unexpectedly. Please try again shortly."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def on_wavelink_node_ready(
        self,
        payload: wavelink.NodeReadyEventPayload,
    ) -> None:
        LOGGER.info(
            "Lavalink node %s ready (resumed=%s)",
            payload.node.identifier,
            payload.resumed,
        )

    async def on_wavelink_node_disconnected(
        self,
        payload: wavelink.NodeDisconnectedEventPayload,
    ) -> None:
        LOGGER.warning("Lavalink node %s disconnected", payload.node.identifier)
        if payload.node.status is wavelink.NodeStatus.DISCONNECTED:
            await self.music.handle_node_disconnected(payload.node)

    async def on_wavelink_node_closed(
        self,
        node: wavelink.Node,
        disconnected_players: list[wavelink.Player],
    ) -> None:
        LOGGER.warning(
            "Lavalink node %s closed; %s player(s) disconnected",
            node.identifier,
            len(disconnected_players),
        )
        self.music.handle_voice_disconnect()

    async def on_wavelink_track_end(
        self,
        payload: wavelink.TrackEndEventPayload,
    ) -> None:
        await self.music.handle_track_end(payload)

    async def on_wavelink_track_exception(
        self,
        payload: wavelink.TrackExceptionEventPayload,
    ) -> None:
        await self.music.handle_track_exception(payload)

    async def on_wavelink_track_stuck(
        self,
        payload: wavelink.TrackStuckEventPayload,
    ) -> None:
        await self.music.handle_track_stuck(payload)

    async def on_wavelink_websocket_closed(
        self,
        payload: wavelink.WebsocketClosedEventPayload,
    ) -> None:
        LOGGER.warning(
            "Discord voice websocket closed (code=%s, remote=%s): %s",
            payload.code,
            payload.by_remote,
            payload.reason,
        )

    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        await self.music.handle_inactive_player(player)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        del before
        if (
            self.user is not None
            and member.id == self.user.id
            and after.channel is None
        ):
            self.music.handle_voice_disconnect()

    async def close(self) -> None:
        """Release subsystem resources without coupling their failure paths."""

        if check_matches.is_running():
            check_matches.cancel()
        await close_riot_client()
        await self.music.shutdown()
        await super().close()


def main() -> None:
    """Validate runtime configuration and start the Discord client."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    validate_core_settings()
    assert DISCORD_TOKEN is not None and GUILD_ID is not None
    client = BetterKyleClient(GUILD_ID)
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
