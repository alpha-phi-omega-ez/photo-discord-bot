"""Tests for guild-limited command authorization."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import main

GUILD_ID = "123456789"


def test_ensure_guild_accepts_configured_guild(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    interaction = MagicMock()
    interaction.guild = MagicMock(id=int(GUILD_ID))

    assert main.ensure_guild(interaction) is True


def test_ensure_guild_rejects_foreign_guild(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    interaction = MagicMock()
    interaction.guild = MagicMock(id=999)

    assert main.ensure_guild(interaction) is False


def test_ensure_guild_rejects_dm(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    interaction = MagicMock()
    interaction.guild = None

    assert main.ensure_guild(interaction) is False


@pytest.mark.parametrize(
    "command,args",
    [
        (main.read_thread, ("1",)),
        (main.read_message, ("1", "folder")),
        (main.change_folder_command, ("folder-id",)),
    ],
)
async def test_commands_reject_outside_configured_guild(monkeypatch, command, args):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    interaction = MagicMock()
    interaction.guild = MagicMock(id=999)
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    await command.callback(interaction, *args)

    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.parametrize(
    "command,args",
    [
        (main.read_thread, ("1",)),
        (main.read_message, ("1", "folder")),
        (main.change_folder_command, ("folder-id",)),
    ],
)
async def test_commands_reject_in_dm(monkeypatch, command, args):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    interaction = MagicMock()
    interaction.guild = None
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    await command.callback(interaction, *args)

    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


async def test_threadimages_proceeds_in_configured_guild(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    interaction = MagicMock()
    interaction.guild = MagicMock(id=int(GUILD_ID))
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    monkeypatch.setattr(main.bot, "fetch_channel", AsyncMock(return_value=object()))

    await main.read_thread.callback(interaction, "1")

    interaction.response.defer.assert_awaited_once()
    interaction.response.send_message.assert_not_called()


async def test_on_ready_moves_global_commands_into_guild(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    guild = MagicMock(id=int(GUILD_ID))
    monkeypatch.setattr(main.bot, "get_guild", MagicMock(return_value=guild))
    monkeypatch.setattr(type(main.bot), "user", property(lambda _self: "test-bot"))

    tree = MagicMock()
    tree.get_commands.return_value = [MagicMock()]
    tree.sync = AsyncMock()
    monkeypatch.setattr(type(main.bot), "tree", property(lambda _self: tree))

    await main.on_ready()

    tree.copy_global_to.assert_called_once_with(guild=guild)
    tree.clear_commands.assert_called_once_with(guild=None)
    assert tree.sync.await_count == 2
    assert any(call.kwargs == {"guild": guild} for call in tree.sync.await_args_list)


async def test_on_ready_does_not_resync_on_reconnect(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    guild = MagicMock(id=int(GUILD_ID))
    monkeypatch.setattr(main.bot, "get_guild", MagicMock(return_value=guild))
    monkeypatch.setattr(type(main.bot), "user", property(lambda _self: "test-bot"))

    tree = MagicMock()
    tree.get_commands.return_value = [MagicMock()]
    tree.sync = AsyncMock()
    monkeypatch.setattr(type(main.bot), "tree", property(lambda _self: tree))

    await main.on_ready()
    await main.on_ready()

    assert tree.sync.await_count == 2
    tree.copy_global_to.assert_called_once()


async def test_on_ready_retries_sync_after_failure(monkeypatch):
    monkeypatch.setattr(main, "GUILD_ID", GUILD_ID)
    guild = MagicMock(id=int(GUILD_ID))
    monkeypatch.setattr(main.bot, "get_guild", MagicMock(return_value=guild))
    monkeypatch.setattr(type(main.bot), "user", property(lambda _self: "test-bot"))

    tree = MagicMock()
    tree.get_commands.side_effect = [[MagicMock()], []]
    tree.sync = AsyncMock(side_effect=[RuntimeError("boom"), [], []])
    monkeypatch.setattr(type(main.bot), "tree", property(lambda _self: tree))

    await main.on_ready()
    assert main._commands_synced is False

    await main.on_ready()

    assert tree.sync.await_count == 3
    assert main._commands_synced is True
