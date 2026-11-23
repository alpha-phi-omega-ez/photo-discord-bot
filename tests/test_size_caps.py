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


def test_unsafe_folder_name_is_sanitized(monkeypatch, caplog):
    monkeypatch.setattr(main, "sleep", lambda _seconds: None)
    executor = MagicMock()
    monkeypatch.setattr(main, "EXECUTOR", executor)

    with caplog.at_level(logging.INFO):
        main.queue_file_downloads(
            "bad'name",
            [_attachment("https://cdn.discordapp.com/a/photo.png", 10)],
            folder_id="folder-id",
        )

    executor.submit.assert_called_once()
    assert executor.submit.call_args.args[5] == "bad_name"
    assert "to 'bad_name'" in caplog.text


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
    response.close.assert_called_once()


def test_video_mid_stream_abort_while_writing_to_disk(monkeypatch, caplog):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    response = _video_response(
        chunks=[b"a" * (600 * 1024), b"a" * (600 * 1024)],
    )
    temp_file = MagicMock()
    temp_file.name = "/tmp/partial-video.mp4"
    monkeypatch.setattr(main, "NamedTemporaryFile", MagicMock(return_value=temp_file))
    unlink = MagicMock()
    monkeypatch.setattr(main, "unlink", unlink)
    monkeypatch.setattr(main, "get_file_size", lambda _url: None)
    monkeypatch.setattr(main, "get", MagicMock(return_value=response))
    upload = MagicMock()
    monkeypatch.setattr(main, "upload", upload)

    with caplog.at_level(logging.WARNING):
        main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", True)

    upload.assert_not_called()
    response.close.assert_called_once()
    temp_file.close.assert_called_once()
    unlink.assert_called_once_with("/tmp/partial-video.mp4")
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
    response.close.assert_called_once()
    assert "exceeded the 1 MB limit mid-stream" in caplog.text


def test_video_head_size_requested_once_across_retries(monkeypatch):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    monkeypatch.setattr(main, "sleep", lambda _seconds: None)
    get_file_size = MagicMock(return_value=None)
    monkeypatch.setattr(main, "get_file_size", get_file_size)
    response = MagicMock()
    response.status_code = 503
    response.headers = {}
    get = MagicMock(return_value=response)
    monkeypatch.setattr(main, "get", get)
    upload = MagicMock()
    monkeypatch.setattr(main, "upload", upload)

    main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", True)

    get_file_size.assert_called_once()
    assert get.call_count == 5
    assert response.close.call_count == 5
    upload.assert_not_called()


def test_video_skipped_before_download_when_head_reports_oversize(monkeypatch):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    get_file_size = MagicMock(return_value=2 * MAX_BYTES)
    monkeypatch.setattr(main, "get_file_size", get_file_size)
    get = MagicMock()
    monkeypatch.setattr(main, "get", get)
    upload = MagicMock()
    monkeypatch.setattr(main, "upload", upload)

    main.download_video("folder-id", URL, "clip.mp4", "mp4", "thread", False)

    get_file_size.assert_called_once()
    get.assert_not_called()
    upload.assert_not_called()


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
