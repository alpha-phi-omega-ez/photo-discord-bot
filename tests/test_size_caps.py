"""Tests for download timeouts and file-size caps."""

import logging
from unittest.mock import MagicMock

import main

URL = "https://cdn.discordapp.com/attachments/1/2/file.bin?ex=1&hm=secret"
MAX_BYTES = 1024 * 1024


def _attachment(url, size):
    attachment = MagicMock()
    attachment.url = url
    attachment.size = size
    return attachment


def _video_response(headers=None, chunks=None):
    response = MagicMock()
    response.status_code = 200
    response.headers = headers or {}
    response.iter_content.return_value = chunks or [b"chunk"]
    return response


def test_oversized_attachment_is_skipped(monkeypatch, caplog):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    monkeypatch.setattr(main, "sleep", lambda _seconds: None)
    executor = MagicMock()
    monkeypatch.setattr(main, "EXECUTOR", executor)

    with caplog.at_level(logging.WARNING):
        main.queue_file_downloads(
            "thread-name",
            [_attachment("https://cdn.discordapp.com/a/big.png?hm=x", 2 * MAX_BYTES)],
            folder_id="folder-id",
        )

    executor.submit.assert_not_called()
    assert "exceeds the 1 MB limit" in caplog.text


def test_unsafe_folder_name_is_rejected(monkeypatch, caplog):
    monkeypatch.setattr(main, "sleep", lambda _seconds: None)
    executor = MagicMock()
    monkeypatch.setattr(main, "EXECUTOR", executor)

    with caplog.at_level(logging.WARNING):
        main.queue_file_downloads(
            "bad'name",
            [_attachment("https://cdn.discordapp.com/a/photo.png", 10)],
            folder_id="folder-id",
        )

    executor.submit.assert_not_called()
    assert "Rejecting unsafe folder name" in caplog.text


def test_video_content_length_over_cap_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    response = _video_response(headers={"Content-Length": str(2 * MAX_BYTES)})
    monkeypatch.setattr(main, "get_file_size", lambda _url: None)
    monkeypatch.setattr(main, "get", MagicMock(return_value=response))
    upload = MagicMock()
    monkeypatch.setattr(main, "upload", upload)

    main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", True)

    upload.assert_not_called()
    response.iter_content.assert_not_called()


def test_video_mid_stream_abort_while_writing_to_disk(monkeypatch, caplog):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    response = _video_response(
        chunks=[b"a" * (600 * 1024), b"a" * (600 * 1024)],
    )
    monkeypatch.setattr(main, "get_file_size", lambda _url: None)
    monkeypatch.setattr(main, "get", MagicMock(return_value=response))
    upload = MagicMock()
    monkeypatch.setattr(main, "upload", upload)

    with caplog.at_level(logging.WARNING):
        main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", True)

    upload.assert_not_called()
    assert "exceeded the 1 MB limit mid-stream" in caplog.text


def test_video_mid_stream_abort_while_writing_to_memory(monkeypatch, caplog):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    monkeypatch.setattr(main, "VIDEO_IN_MEMORY", True)
    monkeypatch.setattr(main, "is_memory_available", lambda _size: True)
    response = _video_response(
        chunks=[b"a" * (600 * 1024), b"a" * (600 * 1024)],
    )
    monkeypatch.setattr(main, "get_file_size", lambda _url: 100)
    monkeypatch.setattr(main, "get", MagicMock(return_value=response))
    upload = MagicMock()
    monkeypatch.setattr(main, "upload", upload)

    with caplog.at_level(logging.WARNING):
        main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", True)

    upload.assert_not_called()
    assert "exceeded the 1 MB limit mid-stream" in caplog.text


def test_download_image_uses_request_timeout(monkeypatch):
    response = MagicMock()
    response.status_code = 200
    response.content = b"image-bytes"
    get = MagicMock(return_value=response)
    monkeypatch.setattr(main, "get", get)
    monkeypatch.setattr(main, "upload", MagicMock())

    main.download_image(URL, "photo.png", "folder-id", "png", "thread", True)

    get.assert_called_once_with(URL, timeout=main.REQUEST_TIMEOUT)


def test_download_video_uses_request_timeout(monkeypatch):
    response = _video_response(chunks=[b"video-bytes"])
    get = MagicMock(return_value=response)
    monkeypatch.setattr(main, "get_file_size", lambda _url: None)
    monkeypatch.setattr(main, "get", get)
    monkeypatch.setattr(main, "upload", MagicMock())

    main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", True)

    get.assert_called_once_with(URL, stream=True, timeout=main.REQUEST_TIMEOUT)
