"""Smoke test for slash command registration after import."""

import main


def test_all_slash_commands_registered():
    names = {command.name for command in main.bot.tree.get_commands()}
    assert names == {"threadimages", "messageimages", "help", "changefolder"}
