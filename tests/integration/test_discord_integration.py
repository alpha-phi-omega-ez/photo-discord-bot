"""Integration-style tests for Discord command workflows."""

from types import SimpleNamespace
from typing import Iterable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord import Thread

from main import change_folder_command, process_message, read_message, read_thread


def async_iter(items: Iterable):
    """Return an async iterator over ``items``."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


async def invoke_command(command, *args, **kwargs):
    """Invoke the callback for an app command."""
    return await command.callback(*args, **kwargs)


class TestDiscordMessageWorkflow:
    """High-level behavioural checks for Discord workflows."""

    @pytest.mark.asyncio
    @patch("main.submit_task_with_tracking")
    @patch("main.check_folder_exists")
    async def test_thread_command_triggers_processing(self, mock_check, mock_submit):
        from main import bot

        mock_check.return_value = "folder-id"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.is_done.return_value = False
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        thread = MagicMock(spec=Thread)
        thread.name = "test"
        thread.parent = MagicMock()
        thread.parent.name = "channel"

        message = MagicMock()
        message.reactions = []
        message.attachments = [MagicMock(url="https://example.com/image.jpg")]

        thread.history = MagicMock(return_value=async_iter([message]))

        with patch.object(bot, "fetch_channel", return_value=thread):
            with patch("main.process_message", new_callable=AsyncMock) as mock_process:
                await invoke_command(read_thread, interaction, "123")

        mock_process.assert_called_once_with(message)

    @pytest.mark.asyncio
    @patch("main.submit_task_with_tracking")
    @patch("main.check_folder_exists")
    async def test_message_command_triggers_processing(self, mock_check, mock_submit):
        mock_check.return_value = "folder-id"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.response.is_done.return_value = False
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        channel = MagicMock()
        message = MagicMock()
        message.reactions = []
        message.attachments = [MagicMock(url="https://example.com/image.jpg")]
        channel.fetch_message = AsyncMock(return_value=message)

        guild = MagicMock()
        guild.text_channels = [channel]

        with patch("main.GUILD", guild):
            with patch("main.process_message", new_callable=AsyncMock) as mock_process:
                await invoke_command(read_message, interaction, "456", "folder")

        mock_process.assert_called_once()

    @pytest.mark.asyncio
    @patch("main.check_parent_folder_id")
    @patch("main.aiofiles.open")
    @patch("main.submit_task_with_tracking")
    @patch("main.check_folder_exists")
    async def test_change_folder_followed_by_message_processing(
        self,
        mock_check_folder,
        mock_submit,
        mock_aioopen,
        mock_check_parent,
        mock_discord_member,
    ):
        mock_check_parent.return_value = True
        mock_check_folder.return_value = "folder-id"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.response.is_done.return_value = False
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        mock_discord_member.roles = [SimpleNamespace(name="required")]
        interaction.user = mock_discord_member

        cm = AsyncMock()
        mock_aioopen.return_value.__aenter__.return_value = cm

        with patch("main.ROLE_NAME", "required"):
            with patch("main.folder_cache", {}):
                await invoke_command(change_folder_command, interaction, "folder")

        mock_check_parent.assert_called_once_with("folder")

        message = MagicMock()
        message.content = "hello"
        message.channel = MagicMock()
        message.channel.name = "thread"
        message.attachments = [MagicMock(url="https://example.com/image.jpg")]
        message.guild = MagicMock()
        message.guild.emojis = []
        message.add_reaction = AsyncMock()

        await process_message(message)
        mock_submit.assert_called()
