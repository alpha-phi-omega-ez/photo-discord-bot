"""Shared fixtures for testing."""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import Member, Thread


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing."""
    env_vars = {
        "DISCORD_TOKEN": "test_discord_token",
        "CHANNEL_NAME": "test_channel",
        "SHARED_DRIVE_ID": "test_drive_id",
        "GUILD_ID": "123456789",
        "VIDEO_IN_MEMORY": "False",
        "DELEGATE_EMAIL": "test@example.com",
        "LOG_LEVEL": "DEBUG",
        "ROLE_NAME": "test_role",
        "PARENT_FOLDER_ID": "test_parent_folder_id",
        "MAX_FILE_SIZE_MB": "0",
        "MEMORY_RESERVE_PERCENT": "10.0",
        "THREAD_POOL_WORKERS": "2",
        "MAX_RETRIES": "3",
        "RETRY_BACKOFF_MULTIPLIER": "2.0",
        "SENTRY_DSN": "",
        "SENTRY_TRACE_RATE": "0.0",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def mock_google_drive_service():
    """Create a mock Google Drive service object."""
    service = MagicMock()

    # Mock files() method chain
    files_mock = MagicMock()
    service.files.return_value = files_mock

    # Mock get() method
    get_mock = MagicMock()
    get_execute_mock = MagicMock()
    get_execute_mock.return_value = {"id": "test_folder_id"}
    get_mock.execute.return_value = get_execute_mock.return_value
    files_mock.get.return_value = get_mock

    # Mock list() method
    list_execute_mock = MagicMock()
    list_execute_mock.return_value = {
        "files": [{"id": "test_folder_id", "name": "test_folder"}]
    }

    list_mock = MagicMock()
    list_mock.execute.return_value = list_execute_mock.return_value
    files_mock.list.return_value = list_mock

    # Mock create() method
    create_execute_mock = MagicMock()
    create_execute_mock.return_value = {"id": "new_folder_id", "name": "new_folder"}

    create_mock = MagicMock()
    create_mock.execute.return_value = create_execute_mock.return_value
    files_mock.create.return_value = create_mock

    return service


@pytest.fixture
def mock_discord_bot():
    """Create a mock Discord bot object."""
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.name = "TestBot"
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock()
    bot.get_guild = MagicMock(return_value=MagicMock())
    bot.fetch_channel = AsyncMock()
    return bot


@pytest.fixture
def mock_discord_guild():
    """Create a mock Discord guild object."""
    guild = MagicMock()
    guild.id = 123456789
    guild.name = "Test Guild"
    guild.text_channels = []
    guild.emojis = []
    return guild


@pytest.fixture
def mock_discord_channel():
    """Create a mock Discord channel object."""
    channel = MagicMock()
    channel.id = 987654321
    channel.name = "test_channel"
    return channel


@pytest.fixture
def mock_discord_thread(mock_discord_channel):
    """Create a mock Discord thread object."""
    thread = MagicMock(spec=Thread)
    thread.id = 111222333
    thread.name = "test_thread"
    thread.parent = mock_discord_channel
    thread.history = AsyncMock()
    return thread


@pytest.fixture
def mock_discord_message(mock_discord_thread):
    """Create a mock Discord message object."""
    message = MagicMock()
    message.id = 444555666
    message.content = "test message"
    message.channel = mock_discord_thread
    message.attachments = []
    message.guild = MagicMock()
    message.guild.emojis = []
    message.reactions = []
    message.add_reaction = AsyncMock()
    return message


@pytest.fixture
def mock_discord_attachment():
    """Create a mock Discord attachment object."""
    attachment = MagicMock()
    attachment.url = "https://example.com/image.jpg"
    attachment.filename = "image.jpg"
    attachment.size = 1024
    return attachment


@pytest.fixture
def mock_discord_interaction(mock_discord_guild):
    """Create a mock Discord interaction object."""
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done.return_value = False
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 777888999
    interaction.guild = mock_discord_guild
    return interaction


@pytest.fixture
def mock_discord_member():
    """Create a mock Discord member object."""
    member = MagicMock(spec=Member)
    member.id = 777888999
    member.name = "TestUser"
    member.roles = []
    return member


@pytest.fixture
def mock_http_session():
    """Create a mock HTTP session."""
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Length": "1024"}
    response.content = b"test image data"
    response.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])
    session.head.return_value = response
    session.get.return_value = response
    return session


@pytest.fixture
def mock_virtual_memory():
    """Create a mock virtual_memory object."""
    memory = MagicMock()
    memory.available = 1024 * 1024 * 1024  # 1GB
    return memory


@pytest.fixture
def sample_image_data():
    """Create sample image data."""
    return BytesIO(b"fake image data")


@pytest.fixture
def sample_video_data():
    """Create sample video data."""
    return BytesIO(b"fake video data")


@pytest.fixture
def mock_heif_file():
    """Create a mock HEIF file object."""
    heif_file = MagicMock()
    heif_file.mode = "RGB"
    heif_file.size = (100, 100)
    heif_file.data = b"fake image data"
    heif_file.stride = 300
    return heif_file


@pytest.fixture
def mock_executor():
    """Create a mock ThreadPoolExecutor."""
    executor = MagicMock()
    future = MagicMock()
    future.done.return_value = False
    executor.submit.return_value = future
    return executor


@pytest.fixture(autouse=True)
def patch_sentry(monkeypatch):
    """Disable Sentry SDK initialization during tests."""
    monkeypatch.setattr("sentry_sdk.init", MagicMock())


@pytest.fixture(autouse=True)
def patch_dotenv(monkeypatch):
    """Disable dotenv loading during tests."""
    monkeypatch.setattr("dotenv.load_dotenv", MagicMock())
