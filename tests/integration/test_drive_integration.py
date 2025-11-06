"""Integration tests for Google Drive workflows."""

from datetime import datetime, timedelta
from unittest.mock import patch

from main import (
    CACHE_TTL,
    cache_lock,
    check_folder_exists,
    create_folder,
    folder_cache,
    upload,
)


class TestDriveFolderWorkflow:
    """Tests for folder creation and lookup workflow."""

    def test_folder_creation_lookup_flow(self, mock_google_drive_service):
        """Test complete flow: create folder, then lookup."""
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent_id"):
                with patch("main.SHARED_DRIVE_ID", "drive_id"):
                    files_resource = mock_google_drive_service.files.return_value
                    files_resource.list.return_value.execute.return_value = {
                        "files": []
                    }
                    files_resource.create.return_value.execute.return_value = {
                        "id": "new_folder_id",
                        "name": "test_folder",
                    }

                    # Create folder
                    folder_id = create_folder("test_folder")
                    assert folder_id == "new_folder_id"

                    # Now folder exists in cache
                    with cache_lock:
                        assert "test_folder" in folder_cache

                    # Lookup should use cache
                    files_resource.list.return_value.execute.return_value = {
                        "files": [{"id": "new_folder_id", "name": "test_folder"}]
                    }

                    found_id = check_folder_exists("test_folder")
                    assert found_id == "new_folder_id"

                    # Clean up
                    with cache_lock:
                        folder_cache.clear()

    def test_multiple_file_uploads_to_same_folder(self, mock_google_drive_service):
        """Test multiple files uploaded to same folder."""
        with patch("main.SERVICE", mock_google_drive_service):
            from io import BytesIO

            files_resource = mock_google_drive_service.files.return_value
            files_resource.create.return_value.execute.side_effect = [
                {"id": "file1_id", "name": "FILE1.JPG"},
                {"id": "file2_id", "name": "FILE2.JPG"},
                {"id": "file3_id", "name": "FILE3.JPG"},
            ]

            folder_id = "test_folder_id"

            # Upload multiple files
            upload(folder_id, BytesIO(b"image1"), "file1.jpg", "jpg", "thread", "image")
            upload(folder_id, BytesIO(b"image2"), "file2.jpg", "jpg", "thread", "image")
            upload(folder_id, BytesIO(b"image3"), "file3.jpg", "jpg", "thread", "image")

            # Verify all files were uploaded
            assert files_resource.create.call_count == 3

    def test_folder_caching_behavior(self, mock_google_drive_service):
        """Test folder caching works across multiple lookups."""
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent_id"):
                with patch("main.SHARED_DRIVE_ID", "drive_id"):
                    files_resource = mock_google_drive_service.files.return_value
                    files_resource.list.return_value.execute.return_value = {
                        "files": [{"id": "cached_folder_id", "name": "cached_folder"}]
                    }

                    folder_id1 = check_folder_exists("cached_folder")
                    assert folder_id1 == "cached_folder_id"

                    # Second lookup - should use cache (no API call)
                    initial_call_count = files_resource.list.call_count
                    folder_id2 = check_folder_exists("cached_folder")
                    assert folder_id2 == "cached_folder_id"

                    # Should not have called API again
                    assert files_resource.list.call_count == initial_call_count

                    # Clean up
                    with cache_lock:
                        folder_cache.clear()

    def test_retry_logic_with_transient_errors(self, mock_google_drive_service):
        """Test retry logic handles transient errors correctly."""
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent_id"):
                with patch("main.SHARED_DRIVE_ID", "drive_id"):
                    files_resource = mock_google_drive_service.files.return_value
                    files_resource.list.return_value.execute.side_effect = [
                        Exception("HTTP 500 error"),
                        {"files": [{"id": "folder_id", "name": "test_folder"}]},
                    ]

                    folder_id = check_folder_exists("test_folder")
                    assert folder_id == "folder_id"

                    # Should have been called twice (retry)
                    assert files_resource.list.call_count == 2

    def test_cache_expiration_behavior(self, mock_google_drive_service):
        """Test cache expiration triggers new API call."""
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent_id"):
                with patch("main.SHARED_DRIVE_ID", "drive_id"):
                    files_resource = mock_google_drive_service.files.return_value
                    expired_time = datetime.now() - CACHE_TTL - timedelta(hours=1)
                    with cache_lock:
                        folder_cache["expired_folder"] = ("old_folder_id", expired_time)

                    # Lookup should call API and update cache
                    files_resource.list.return_value.execute.return_value = {
                        "files": [{"id": "new_folder_id", "name": "expired_folder"}]
                    }

                    folder_id = check_folder_exists("expired_folder")
                    assert folder_id == "new_folder_id"

                    # Cache should be updated with new timestamp
                    with cache_lock:
                        assert folder_cache["expired_folder"][0] == "new_folder_id"
                        assert folder_cache["expired_folder"][1] > expired_time

                    # Clean up
                    with cache_lock:
                        folder_cache.clear()


class TestDriveUploadWorkflow:
    """Tests for file upload workflows."""

    def test_image_upload_workflow(self, mock_google_drive_service):
        """Test complete image upload workflow."""
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent_id"):
                with patch("main.SHARED_DRIVE_ID", "drive_id"):
                    from io import BytesIO

                    files_resource = mock_google_drive_service.files.return_value
                    files_resource.list.return_value.execute.return_value = {
                        "files": []
                    }
                    files_resource.create.return_value.execute.side_effect = [
                        {"id": "folder_id", "name": "test_thread"},  # Folder creation
                        {"id": "file_id", "name": "IMAGE.JPG"},  # File upload
                    ]

                    # Create folder
                    folder_id = create_folder("test_thread")

                    # Upload image
                    image_data = BytesIO(b"fake image data")
                    upload(
                        folder_id,
                        image_data,
                        "image.jpg",
                        "jpg",
                        "test_thread",
                        "image",
                    )

                    # Verify both folder and file were created
                    assert files_resource.create.call_count == 2

    def test_video_upload_workflow(self, mock_google_drive_service, tmp_path):
        """Test complete video upload workflow."""
        with patch("main.SERVICE", mock_google_drive_service):
            with patch("main.PARENT_FOLDER_ID", "parent_id"):
                with patch("main.SHARED_DRIVE_ID", "drive_id"):
                    files_resource = mock_google_drive_service.files.return_value
                    files_resource.list.return_value.execute.return_value = {
                        "files": []
                    }
                    files_resource.create.return_value.execute.side_effect = [
                        {"id": "folder_id", "name": "test_thread"},  # Folder creation
                        {"id": "file_id", "name": "VIDEO.MP4"},  # File upload
                    ]

                    # Create folder
                    folder_id = create_folder("test_thread")

                    # Create temp video file
                    video_file = tmp_path / "video.mp4"
                    video_file.write_bytes(b"fake video data")

                    # Upload video
                    upload(
                        folder_id,
                        None,
                        "video.mp4",
                        "mp4",
                        "test_thread",
                        "video",
                        str(video_file),
                    )

                    # Verify both folder and file were created
                    assert files_resource.create.call_count == 2
