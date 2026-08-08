"""Tests that every requested slash command is guild-scoped and registered."""

from __future__ import annotations

import unittest

import discord
from discord import app_commands

from league.commands import setup_league_commands
from music.commands import setup_music_commands


class GuildCommandRegistrationTests(unittest.TestCase):
    def test_exact_guild_command_names(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        guild = discord.Object(id=123456789)

        setup_league_commands(tree, guild)
        setup_music_commands(tree, guild, object())  # type: ignore[arg-type]

        expected_top_level = {
            "riot",
            "unlink",
            "refresh",
            "play",
            "radio",
            "pause",
            "resume",
            "skip",
            "stop",
            "queue",
            "nowplaying",
            "shuffle",
            "volume",
            "disconnect",
            "clearqueue",
            "remove",
        }
        actual_top_level = {command.name for command in tree.get_commands(guild=guild)}
        self.assertEqual(actual_top_level, expected_top_level)
        self.assertEqual(tree.get_commands(), [])

        riot = tree.get_command("riot", guild=guild)
        self.assertIsInstance(riot, app_commands.Group)
        assert isinstance(riot, app_commands.Group)
        self.assertEqual({command.name for command in riot.commands}, {"link"})

        radio = tree.get_command("radio", guild=guild)
        self.assertIsInstance(radio, app_commands.Command)
        assert isinstance(radio, app_commands.Command)
        station_parameter = radio.parameters[0]
        self.assertEqual(station_parameter.name, "station")
        self.assertEqual(station_parameter.default, "live105")
        self.assertEqual(
            [(choice.name, choice.value) for choice in station_parameter.choices],
            [
                ("LIVE 105 — 105.3 KITS", "live105"),
                ("MIX 106.5 — 106.5 KEZR", "mix1065"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
