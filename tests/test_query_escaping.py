"""Tests for Drive query escaping and log-hygiene helpers."""

from unittest.mock import MagicMock

import main
from main import (
    _public_url,
    _safe_log_value,
    escape_drive_query_value,
)


def test_benign_value_is_unchanged():
    assert escape_drive_query_value("Photos 2024") == "Photos 2024"


def test_single_quote_is_escaped():
    assert escape_drive_query_value("O'Brien's thread") == "O\\'Brien\\'s thread"


def test_backslash_is_escaped_before_quote():
    assert escape_drive_query_value("a\\b'c") == "a\\\\b\\'c"


def _fake_service():
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {"files": []}
    return service


def test_check_folder_exists_escapes_name_in_query(monkeypatch):
    service = _fake_service()
    monkeypatch.setattr(main, "SERVICE", service)
    monkeypatch.setattr(main, "PARENT_FOLDER_ID", "parent-id")
    monkeypatch.setattr(main, "SHARED_DRIVE_ID", "drive-id")

    main.check_folder_exists("O'Brien's thread")

    q = service.files.return_value.list.call_args.kwargs["q"]
    assert q == "'parent-id' in parents and name='O\\'Brien\\'s thread'"


def test_check_folder_exists_escapes_backslashes_in_query(monkeypatch):
    service = _fake_service()
    monkeypatch.setattr(main, "SERVICE", service)
    monkeypatch.setattr(main, "PARENT_FOLDER_ID", "parent-id")
    monkeypatch.setattr(main, "SHARED_DRIVE_ID", "drive-id")

    main.check_folder_exists("a\\b")

    q = service.files.return_value.list.call_args.kwargs["q"]
    assert q == "'parent-id' in parents and name='a\\\\b'"


def test_public_url_strips_signed_query():
    url = "https://cdn.discordapp.com/attachments/1/2/photo.png?ex=1&is=2&hm=secret"
    assert _public_url(url) == "https://cdn.discordapp.com/attachments/1/2/photo.png"


def test_safe_log_value_strips_newlines():
    assert _safe_log_value("line1\r\nline2\nline3") == "line1line2line3"
