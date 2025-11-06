"""Unit tests for Discord commands and message processing."""

from types import SimpleNamespace
from typing import Iterable
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from discord import Forbidden, NotFound

from main import (
    change_folder_command,
    help_message,
    process_message,
    queue_file_downloads,
    read_message,
    read_thread,
    submit_task_with_tracking,
)


def async_iter(items: Iterable):
    """Return an async iterator over ``items``."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


async def invoke_command(command, *args, **kwargs):
    """Invoke a Discord app command's callback."""
    return await command.callback(*args, **kwargs)


class TestProcessMessage:
    """Tests for ``process_message``."""

    @pytest.mark.asyncio
    @patch("main.submit_task_with_tracking")
    async def test_process_message_with_attachments(self, mock_submit):
        message = MagicMock()
        message.content = "hello"
        message.attachments = [MagicMock()]
        message.channel = MagicMock()
        message.channel.name = "test-thread"
        message.guild = MagicMock()
        message.guild.emojis = []
        message.add_reaction = AsyncMock()

        await process_message(message)

        mock_submit.assert_called_once()
        message.add_reaction.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content", "attachment_count"),
        [("No Upload", 1), ("hello", 0)],
        ids=["flagged", "no_attachments"],
    )
    async def test_process_message_skips_submission(self, content, attachment_count):
        message = MagicMock()
        message.content = content
        message.attachments = [MagicMock() for _ in range(attachment_count)]

        with patch("main.submit_task_with_tracking") as mock_submit:
            await process_message(message)
            mock_submit.assert_not_called()

    @pytest.mark.asyncio
    @patch("main.utils.get")
    async def test_process_message_custom_emoji(self, mock_get):
        custom_emoji = MagicMock()
        mock_get.return_value = custom_emoji

        message = MagicMock()
        message.content = "hello"
        message.attachments = [MagicMock()]
        message.channel = MagicMock()
        message.channel.name = "test-thread"
        message.guild = MagicMock()
        message.guild.emojis = [custom_emoji]
        message.add_reaction = AsyncMock()

        with patch("main.submit_task_with_tracking"):
            await process_message(message)

        message.add_reaction.assert_called_once_with(custom_emoji)

    @pytest.mark.asyncio
    async def test_process_message_fallback_reaction(self):
        message = MagicMock()
        message.content = "hello"
        message.attachments = [MagicMock()]
        message.channel = MagicMock()
        message.channel.name = "test-thread"
        message.guild = MagicMock()
        message.guild.emojis = []
        message.add_reaction = AsyncMock(side_effect=[Exception(), None])

        with patch("main.submit_task_with_tracking"):
            with patch("main.errors.HTTPException", Exception):
                await process_message(message)

        assert message.add_reaction.call_count == 2  # custom + fallback


class TestQueueFileDownloads:
    """Tests for ``queue_file_downloads``."""

    @patch("main.submit_task_with_tracking")
    @patch("main.create_folder")
    @patch("main.check_folder_exists")
    def test_queue_images(self, mock_check, mock_create, mock_submit):
        mock_check.return_value = None
        mock_create.return_value = "folder-id"

        attachment = MagicMock()
        attachment.url = "https://example.com/photo.jpg"

        queue_file_downloads("thread", [attachment])

        mock_create.assert_called_once_with("thread")
        mock_submit.assert_called_once()

    @patch("main.submit_task_with_tracking")
    @patch("main.check_folder_exists")
    def test_queue_videos(self, mock_check, mock_submit):
        mock_check.return_value = "folder-id"

        attachment = MagicMock()
        attachment.url = "https://example.com/video.mp4"

        queue_file_downloads("thread", [attachment])

        mock_submit.assert_called_once()

    @patch("main.check_folder_exists")
    def test_queue_missing_folder(self, mock_check):
        mock_check.return_value = None
        with patch("main.create_folder", return_value=None):
            with patch("main.submit_task_with_tracking") as mock_submit:
                attachment = MagicMock()
                attachment.url = "https://example.com/photo.jpg"

                queue_file_downloads("thread", [attachment])
                mock_submit.assert_not_called()


class TestSubmitTaskWithTracking:
    """Tests for ``submit_task_with_tracking``."""

    @patch("main.task_lock")
    def test_tracks_future(self, mock_lock):
        future = MagicMock()
        future.done.return_value = False
        future.add_done_callback = MagicMock()

        executor = MagicMock()
        executor.submit.return_value = future

        with patch("main.EXECUTOR", executor):
            with patch("main.task_futures", []):
                result = submit_task_with_tracking(lambda x: x, "arg")

        assert result == future
        executor.submit.assert_called_once()
        future.add_done_callback.assert_called_once()


class TestReadThread:
    """Tests for the ``/threadimages`` command."""

    @pytest.mark.asyncio
    async def test_invalid_id(self, mock_discord_interaction):
        await invoke_command(read_thread, mock_discord_interaction, "invalid")
        mock_discord_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_thread_not_found(self, mock_discord_interaction):
        with patch("main.bot.fetch_channel", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = NotFound(Mock(), "not found")
            await invoke_command(read_thread, mock_discord_interaction, "111")

        mock_discord_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_a_thread(self, mock_discord_interaction, mock_discord_channel):
        with patch("main.bot.fetch_channel", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_discord_channel
            await invoke_command(read_thread, mock_discord_interaction, "111")

        mock_discord_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_success(self, mock_discord_interaction, mock_discord_thread):
        mock_message = MagicMock()
        mock_message.reactions = []
        mock_message.attachments = []
        mock_discord_thread.history = MagicMock(return_value=async_iter([mock_message]))

        mock_discord_interaction.response.defer = AsyncMock()
        mock_discord_interaction.response.is_done.return_value = False

        with patch("main.bot.fetch_channel", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_discord_thread
            with patch("main.process_message", new_callable=AsyncMock) as mock_process:
                await invoke_command(read_thread, mock_discord_interaction, "111")

        mock_discord_interaction.response.defer.assert_called_once()
        mock_process.assert_called_once_with(mock_message)


class TestReadMessage:
    """Tests for the ``/messageimages`` command."""

    @pytest.mark.asyncio
    async def test_invalid_id(self, mock_discord_interaction):
        await invoke_command(
            read_message, mock_discord_interaction, "invalid", "folder"
        )
        mock_discord_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_success(self, mock_discord_interaction, mock_discord_guild):
        channel = MagicMock()
        message = MagicMock()
        message.reactions = []
        channel.fetch_message = AsyncMock(return_value=message)
        mock_discord_guild.text_channels = [channel]

        mock_discord_interaction.response.defer = AsyncMock()
        mock_discord_interaction.response.is_done.return_value = False

        with patch("main.GUILD", mock_discord_guild):
            with patch("main.process_message", new_callable=AsyncMock) as mock_process:
                await invoke_command(
                    read_message, mock_discord_interaction, "123", "folder"
                )

        mock_process.assert_called_once()
        mock_discord_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exception_cls", "message"),
        [(NotFound, "missing"), (Forbidden, "forbidden")],
        ids=["not_found", "forbidden"],
    )
    async def test_message_fetch_errors(
        self, mock_discord_interaction, mock_discord_guild, exception_cls, message
    ):
        channel = MagicMock()
        channel.fetch_message = AsyncMock(side_effect=exception_cls(Mock(), message))
        mock_discord_guild.text_channels = [channel]

        with patch("main.GUILD", mock_discord_guild):
            await invoke_command(
                read_message, mock_discord_interaction, "123", "folder"
            )

        assert mock_discord_interaction.followup.send.called


class TestChangeFolderCommand:
    """Tests for ``/changefolder``."""

    @pytest.mark.asyncio
    async def test_invalid_format(self, mock_discord_interaction):
        await invoke_command(change_folder_command, mock_discord_interaction, "bad<>id")
        mock_discord_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_role(self, mock_discord_interaction, mock_discord_member):
        mock_discord_interaction.user = mock_discord_member
        mock_discord_member.roles = []

        with patch("main.ROLE_NAME", "required"):
            await invoke_command(
                change_folder_command, mock_discord_interaction, "folder"
            )

        mock_discord_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    @patch("main.check_parent_folder_id")
    @patch("main.aiofiles.open")
    async def test_success(
        self, mock_aioopen, mock_check, mock_discord_interaction, mock_discord_member
    ):
        mock_check.return_value = True
        mock_discord_interaction.user = mock_discord_member
        mock_discord_member.roles = [SimpleNamespace(name="required")]

        cm = AsyncMock()
        mock_aioopen.return_value.__aenter__.return_value = cm

        with patch("main.ROLE_NAME", "required"):
            with patch("main.folder_cache", {}):
                await invoke_command(
                    change_folder_command, mock_discord_interaction, "folder"
                )

        mock_check.assert_called_once_with("folder")
        mock_discord_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    @patch("main.check_parent_folder_id")
    async def test_invalid_folder_id(
        self, mock_check, mock_discord_interaction, mock_discord_member
    ):
        mock_check.return_value = False
        mock_discord_interaction.user = mock_discord_member
        mock_discord_member.roles = [SimpleNamespace(name="required")]

        with patch("main.ROLE_NAME", "required"):
            await invoke_command(
                change_folder_command, mock_discord_interaction, "folder"
            )

        mock_discord_interaction.followup.send.assert_called_once()


class TestHelpMessage:
    """Tests for ``/help`` command."""

    @pytest.mark.asyncio
    async def test_help_text(self, mock_discord_interaction):
        await invoke_command(help_message, mock_discord_interaction)
        mock_discord_interaction.response.send_message.assert_called_once()
        payload = str(mock_discord_interaction.response.send_message.call_args)
        assert "threadimages" in payload
        assert "messageimages" in payload
        assert "changefolder" in payload
