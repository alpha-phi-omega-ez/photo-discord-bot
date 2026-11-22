"""Tests for main.get_file_size over mocked HTTP (responses)."""

import logging
from unittest.mock import MagicMock

import requests
import responses

import main
from main import get_file_size

URL = "https://cdn.discordapp.com/attachments/1/2/photo.png"
SIGNED_URL = f"{URL}?ex=1&is=2&hm=secret"


@responses.activate
def test_size_from_content_length():
    responses.add(responses.HEAD, URL, status=200, headers={"Content-Length": "12345"})
    assert get_file_size(URL) == 12345


@responses.activate
def test_missing_content_length_returns_none():
    responses.add(responses.HEAD, URL, status=200)
    assert get_file_size(URL) is None


@responses.activate
def test_non_200_returns_none():
    responses.add(responses.HEAD, URL, status=403)
    assert get_file_size(URL) is None


@responses.activate
def test_connection_error_returns_none():
    responses.add(responses.HEAD, URL, body=requests.exceptions.ConnectionError("boom"))
    assert get_file_size(URL) is None


def test_head_uses_request_timeout(monkeypatch):
    response = MagicMock(status_code=200, headers={"Content-Length": "5"})
    head = MagicMock(return_value=response)
    monkeypatch.setattr(main, "head", head)

    assert get_file_size(SIGNED_URL) == 5
    head.assert_called_once_with(SIGNED_URL, timeout=main.REQUEST_TIMEOUT)


def test_signed_query_is_not_logged(monkeypatch, caplog):
    response = MagicMock(status_code=200, headers={"Content-Length": "5"})
    monkeypatch.setattr(main, "head", MagicMock(return_value=response))
    caplog.set_level(logging.DEBUG, logger="photo-bot")

    main.get_file_size(SIGNED_URL)

    assert "hm=secret" not in caplog.text
    assert URL in caplog.text


def test_connection_error_does_not_log_signed_query(monkeypatch, caplog):
    monkeypatch.setattr(
        main,
        "head",
        MagicMock(side_effect=ConnectionError(f"failed for {SIGNED_URL}")),
    )
    caplog.set_level(logging.WARNING, logger="photo-bot")

    assert get_file_size(SIGNED_URL) is None
    assert "hm=secret" not in caplog.text
