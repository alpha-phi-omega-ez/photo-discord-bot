"""Unit tests for Google Drive functions."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from main import (
    authenticate_google_drive,
    cache_lock,
    check_folder_exists,
    check_parent_folder_id,
    create_folder,
    folder_cache,
    upload,
)


class TestAuthenticateGoogleDrive:
    """Tests for authenticate_google_drive function."""

    @patch("main.build")
    @patch("main.Credentials")
    def test_successful_authentication(self, mock_credentials, mock_build):
        mock_creds = MagicMock()
        delegated = MagicMock()
        mock_creds.with_subject.return_value = delegated
        mock_credentials.from_service_account_file.return_value = mock_creds
        service = MagicMock()
        mock_build.return_value = service

        result = authenticate_google_drive()

        assert result == service
        mock_credentials.from_service_account_file.assert_called_once()
        mock_build.assert_called_once_with("drive", "v3", credentials=delegated)


class TestCheckParentFolderId:
    """Tests for check_parent_folder_id function."""

    def test_successful_folder_check(self, mock_google_drive_service):
        with patch("main.SERVICE", mock_google_drive_service):
            files_resource = mock_google_drive_service.files.return_value
            files_resource.get.return_value.execute.return_value = {"id": "folder"}

            assert check_parent_folder_id("folder") is True
            files_resource.get.assert_called_once()

    def test_invalid_folder_id(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.get.return_value.execute.side_effect = Exception("not found")

        with patch("main.SERVICE", mock_google_drive_service):
            assert check_parent_folder_id("missing") is False

    def test_missing_service_returns_false(self):
        with patch("main.SERVICE", None):
            assert check_parent_folder_id("folder") is False


class TestCheckFolderExists:
    """Tests for check_folder_exists function."""

    @pytest.mark.parametrize(
        ("api_response", "expected_id"),
        [
            ({"files": [{"id": "folder_id", "name": "test"}]}, "folder_id"),
            ({"files": []}, None),
        ],
        ids=["folder_found", "folder_missing"],
    )
    def test_folder_lookup(self, mock_google_drive_service, api_response, expected_id):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.list.return_value.execute.return_value = api_response

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                with patch("main.SHARED_DRIVE_ID", "drive"):
                    with patch("main.folder_cache", {}):
                        result = check_folder_exists("test")

        assert result == expected_id
        files_resource.list.assert_called_once()

    def test_folder_cached(self, mock_google_drive_service):
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                with patch("main.SHARED_DRIVE_ID", "drive"):
                    with cache_lock:
                        folder_cache["cached"] = ("cached-id", datetime.now())

                    assert check_folder_exists("cached") == "cached-id"
                    mock_google_drive_service.files.return_value.list.assert_not_called()

                    with cache_lock:
                        folder_cache.clear()

    def test_folder_cache_expired(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.list.return_value.execute.return_value = {
            "files": [{"id": "fresh-id", "name": "expired"}]
        }

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                with patch("main.SHARED_DRIVE_ID", "drive"):
                    expired_time = datetime.now() - timedelta(hours=2)
                    with cache_lock:
                        folder_cache["expired"] = ("old", expired_time)

                    assert check_folder_exists("expired") == "fresh-id"
                    assert files_resource.list.call_count == 1

                    with cache_lock:
                        folder_cache.clear()

    def test_folder_name_sanitisation(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.list.return_value.execute.return_value = {"files": []}

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                with patch("main.SHARED_DRIVE_ID", "drive"):
                    check_folder_exists("folder<>:name")
                    query = str(files_resource.list.call_args)
                    assert "<" not in query
                    assert ":" not in query

    def test_missing_service_returns_none(self):
        with patch("main.SERVICE", None):
            assert check_folder_exists("test") is None

    def test_retry_on_transient_error(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.list.return_value.execute.side_effect = [
            Exception("HTTP 500"),
            {"files": [{"id": "retry-id"}]},
        ]

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                with patch("main.SHARED_DRIVE_ID", "drive"):
                    assert check_folder_exists("retry") == "retry-id"
                    assert files_resource.list.call_count == 2


class TestCreateFolder:
    """Tests for create_folder function."""

    def test_successful_folder_creation(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.return_value = {
            "id": "new-folder",
            "name": "sanitised",
        }

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                assert create_folder("new") == "new-folder"
                files_resource.create.assert_called_once()

    def test_folder_creation_updates_cache(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.return_value = {
            "id": "cached-folder",
            "name": "cached",
        }

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                assert create_folder("cached") == "cached-folder"
                with cache_lock:
                    assert folder_cache["cached"][0] == "cached-folder"
                    folder_cache.clear()

    def test_name_sanitisation(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.return_value = {
            "id": "folder",
            "name": "folder",
        }

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                create_folder("bad<>:name")
                body = files_resource.create.call_args.kwargs["body"]
                assert "<" not in body["name"]
                assert ":" not in body["name"]

    def test_creation_failure_returns_none(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.side_effect = Exception("fail")

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent"):
                assert create_folder("new") is None

    def test_missing_service_returns_none(self):
        with patch("main.SERVICE", None):
            assert create_folder("folder") is None


class TestUpload:
    """Tests for upload function."""

    def test_upload_with_stream_data(self, mock_google_drive_service):
        from io import BytesIO

        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.return_value = {"id": "file"}

        with patch("main.SERVICE", mock_google_drive_service):
            upload(
                "folder",
                BytesIO(b"data"),
                "test.jpg",
                "jpg",
                "thread",
                "image",
            )

        files_resource.create.assert_called_once()
        body = files_resource.create.call_args.kwargs["body"]
        assert body["name"] == "TEST.JPG"

    def test_upload_with_file_path(self, mock_google_drive_service, tmp_path):
        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.return_value = {"id": "file"}

        temp = tmp_path / "vid.mp4"
        temp.write_bytes(b"video")

        with patch("main.SERVICE", mock_google_drive_service):
            upload(
                "folder",
                None,
                "vid.mp4",
                "mp4",
                "thread",
                "video",
                str(temp),
            )

        files_resource.create.assert_called_once()

    def test_upload_jpg_converted(self, mock_google_drive_service):
        from io import BytesIO

        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.return_value = {"id": "file"}

        with patch("main.SERVICE", mock_google_drive_service):
            upload(
                "folder",
                BytesIO(b"data"),
                "name.jpg",
                "jpg",
                "thread",
                "image",
            )

        media_body = files_resource.create.call_args.kwargs["media_body"]
        mimetype = media_body.mimetype()
        assert "jpeg" in mimetype

    def test_upload_failure_raises(self, mock_google_drive_service):
        from io import BytesIO

        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.side_effect = Exception("fail")

        with patch("main.SERVICE", mock_google_drive_service):
            with pytest.raises(Exception, match="fail"):
                upload(
                    "folder",
                    BytesIO(b"data"),
                    "name.jpg",
                    "jpg",
                    "thread",
                    "image",
                )

    def test_upload_no_media_logs_error(self, mock_google_drive_service):
        files_resource = mock_google_drive_service.files.return_value

        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.logger") as mock_logger:
                upload("folder", None, "name.jpg", "jpg", "thread", "image", None)

        mock_logger.error.assert_called()
        files_resource.create.assert_not_called()

    def test_missing_service_raises(self):
        from io import BytesIO

        with patch("main.SERVICE", None):
            with pytest.raises(Exception, match="not authenticated"):
                upload("folder", BytesIO(b"data"), "name.jpg", "jpg", "thread", "image")

    def test_retry_on_transient_error(self, mock_google_drive_service):
        from io import BytesIO

        files_resource = mock_google_drive_service.files.return_value
        files_resource.create.return_value.execute.side_effect = [
            Exception("HTTP 500"),
            {"id": "file"},
        ]

        with patch("main.SERVICE", mock_google_drive_service):
            upload(
                "folder",
                BytesIO(b"data"),
                "name.jpg",
                "jpg",
                "thread",
                "image",
            )

        assert files_resource.create.call_count == 2
