"""Tests for the Phase 1 importability refactor: load_config and main()."""

from unittest.mock import MagicMock

import pytest

import main


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Neutralize load_dotenv: python-dotenv finds the repo's real .env via
    the script directory (walking up from main.py), which would resurrect
    vars deleted in these tests and break hermeticity."""
    monkeypatch.setattr(main, "load_dotenv", lambda: None)


def test_load_config_reads_env(monkeypatch, tmp_path):
    """chdir into tmp_path so the repo's real config/ dir cannot interfere."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIDEO_IN_MEMORY", "True")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    main.load_config()

    for var in (
        "DISCORD_TOKEN",
        "CHANNEL_NAME",
        "SHARED_DRIVE_ID",
        "GUILD_ID",
        "DELEGATE_EMAIL",
        "ROLE_NAME",
    ):
        assert getattr(main, var)
    assert main.PARENT_FOLDER_ID == "test-parent-folder-id"
    assert main.VIDEO_IN_MEMORY is True
    assert main.LOG_LEVEL == "DEBUG"


def test_load_config_prefers_file_over_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "parent_folder_id.txt").write_text("  file-folder-id \n")

    main.load_config()

    assert main.PARENT_FOLDER_ID == "file-folder-id"


def test_load_config_missing_vars_listed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GUILD_ID", raising=False)

    with pytest.raises(ValueError) as excinfo:
        main.load_config()

    message = str(excinfo.value)
    assert message.startswith("Missing environment variables: ")
    assert "GUILD_ID" in message


def test_main_prints_and_exits_when_config_missing(monkeypatch, tmp_path, capsys):
    """Missing config exits 1 only when main() runs, never at import."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROLE_NAME", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        main.main()

    assert excinfo.value.code == 1
    assert "ROLE_NAME" in capsys.readouterr().out


def test_main_loads_config_then_starts_bot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(main, "init_sentry", lambda: calls.append("sentry"))
    monkeypatch.setattr(
        main, "setup_logger", lambda *args, **kw: calls.append("logger")
    )
    monkeypatch.setattr(main, "authenticate_google_drive", lambda: "drive-service-mock")
    monkeypatch.setattr(main.bot, "run", lambda token: calls.append(("run", token)))

    main.main()

    assert main.SERVICE == "drive-service-mock"
    assert calls == ["sentry", "logger", ("run", "test-discord-token")]


def test_init_sentry_is_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    init = MagicMock()
    monkeypatch.setattr(main.sentry_sdk, "init", init)

    main.init_sentry()

    init.assert_not_called()


def test_init_sentry_uses_safer_default_trace_rate(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.delenv("SENTRY_TRACE_RATE", raising=False)
    init = MagicMock()
    monkeypatch.setattr(main.sentry_sdk, "init", init)

    main.init_sentry()

    assert init.call_args.kwargs["dsn"] == ("https://public@example.ingest.sentry.io/1")
    assert init.call_args.kwargs["traces_sample_rate"] == 0.2


def test_load_config_reads_max_file_size(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "12")

    main.load_config()

    assert main.MAX_FILE_SIZE_MB == 12


def test_load_config_defaults_invalid_max_file_size(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "not-a-number")

    main.load_config()

    assert main.MAX_FILE_SIZE_MB == 50


@pytest.mark.parametrize("value", ["0", "-5"])
def test_load_config_clamps_non_positive_max_file_size(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAX_FILE_SIZE_MB", value)

    main.load_config()

    assert main.MAX_FILE_SIZE_MB == 50
