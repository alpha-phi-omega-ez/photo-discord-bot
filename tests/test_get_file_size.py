"""Tests for main.get_file_size over mocked HTTP (responses)."""

import requests
import responses

from main import get_file_size

URL = "https://cdn.discordapp.com/attachments/1/2/photo.png"


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
