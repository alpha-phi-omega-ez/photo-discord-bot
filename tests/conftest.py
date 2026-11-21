"""Shared fixtures for the photo-discord-bot test suite."""

import pytest

import main

REQUIRED_ENV_VARS = (
    "DISCORD_TOKEN",
    "PARENT_FOLDER_ID",
    "CHANNEL_NAME",
    "SHARED_DRIVE_ID",
    "GUILD_ID",
    "DELEGATE_EMAIL",
    "ROLE_NAME",
)

FAKE_ENV_VALUES = {
    "DISCORD_TOKEN": "test-discord-token",
    "PARENT_FOLDER_ID": "test-parent-folder-id",
    "CHANNEL_NAME": "test-channel",
    "SHARED_DRIVE_ID": "test-shared-drive-id",
    "GUILD_ID": "123456789",
    "DELEGATE_EMAIL": "bot@example.iam.gserviceaccount.com",
    "ROLE_NAME": "test-role",
}

# Every module global that config/bootstrapping code mutates. Snapshotted
# and restored around each test so results are independent of execution
# order (pytest-randomly shuffles the suite).
MODULE_STATE_GLOBALS = (
    "DISCORD_TOKEN",
    "CHANNEL_NAME",
    "SHARED_DRIVE_ID",
    "GUILD_ID",
    "VIDEO_IN_MEMORY",
    "DELEGATE_EMAIL",
    "LOG_LEVEL",
    "ROLE_NAME",
    "PARENT_FOLDER_ID",
    "SERVICE",
    "GUILD",
    "parent_folder_file",
)


@pytest.fixture(autouse=True)
def required_env(monkeypatch):
    """Set the 7 required env vars so config-dependent code paths can run."""
    for var in REQUIRED_ENV_VARS:
        monkeypatch.setenv(var, FAKE_ENV_VALUES[var])


@pytest.fixture(autouse=True)
def pristine_module_state():
    """Snapshot mutable module state and restore it after each test."""
    snapshot = {name: getattr(main, name) for name in MODULE_STATE_GLOBALS}
    yield
    for name, value in snapshot.items():
        setattr(main, name, value)
