"""Unit tests for download functions."""

from unittest.mock import MagicMock, patch

import pytest

from main import download_image, download_video


class TestDownloadImage:
    """Tests for download_image function."""

    @patch("main.upload")
    @patch("main.http_session")
    @patch("main.MAX_FILE_SIZE_BYTES", 0)
    def test_successful_image_download(self, mock_session, mock_upload):
        """Test successful image download."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"image data"
        mock_session.get.return_value = mock_response

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            download_image(
                "https://example.com/image.jpg",
                "image.jpg",
                "folder_id",
                "jpg",
                "thread_name",
            )

            mock_session.get.assert_called_once_with("https://example.com/image.jpg")
            mock_upload.assert_called_once()

    @patch("main.upload")
    @patch("main.http_session")
    @patch("main.convert_to_jpeg")
    @patch("main.MAX_FILE_SIZE_BYTES", 0)
    def test_heic_conversion(self, mock_convert, mock_session, mock_upload):
        """Test HEIC image is converted to JPEG."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"heic data"
        mock_session.get.return_value = mock_response

        mock_convert.return_value = (b"jpeg data", "image.jpeg", "jpeg")

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            download_image(
                "https://example.com/image.heic",
                "image.heic",
                "folder_id",
                "heic",
                "thread_name",
            )

            mock_convert.assert_called_once()
            mock_upload.assert_called_once()

    @pytest.mark.parametrize(
        ("max_file_size", "status_code", "content", "exception_message"),
        [
            (0, 404, b"", "HTTP 404 error downloading"),
            (100, 200, b"x" * 200, "File size 200 exceeds limit 100"),
        ],
        ids=["http_status_error", "file_size_limit"],
    )
    @patch("main.http_session")
    def test_failure_conditions(
        self,
        mock_session,
        max_file_size,
        status_code,
        content,
        exception_message,
    ):
        """Test failure conditions raise appropriate exceptions."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.content = content
        mock_session.get.return_value = mock_response

        with patch("main.MAX_FILE_SIZE_BYTES", max_file_size):
            with patch("main.retry_with_backoff") as mock_retry:

                def failing_func(*_args, **_kwargs):
                    raise Exception(exception_message)

                mock_retry.side_effect = failing_func

                with pytest.raises(Exception, match=exception_message):
                    download_image(
                        "https://example.com/image.jpg",
                        "image.jpg",
                        "folder_id",
                        "jpg",
                        "thread_name",
                    )

    @patch("main.upload")
    @patch("main.http_session")
    @patch("main.MAX_FILE_SIZE_BYTES", 0)
    def test_retry_on_transient_error(self, mock_session, mock_upload):
        """Test retry logic on transient errors."""
        # First call fails, second succeeds
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.content = b"image data"

        mock_session.get.side_effect = [
            Exception("Connection error"),
            mock_response_success,
        ]

        with patch("main.exponential_backoff_sleep"):
            download_image(
                "https://example.com/image.jpg",
                "image.jpg",
                "folder_id",
                "jpg",
                "thread_name",
            )

        assert mock_session.get.call_count == 2
        mock_upload.assert_called_once()


class TestDownloadVideo:
    """Tests for download_video function."""

    @patch("main.upload")
    @patch("main.get_file_size")
    @patch("main.http_session")
    @patch("main.VIDEO_IN_MEMORY", False)
    def test_video_download_to_disk(self, mock_session, mock_get_size, mock_upload):
        """Test video download to disk."""
        mock_get_size.return_value = 1024 * 1024  # 1MB

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])
        mock_session.get.return_value = mock_response

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            download_video(
                "folder_id",
                "https://example.com/video.mp4",
                "video.mp4",
                "mp4",
                "thread_name",
            )

            mock_get_size.assert_called_once_with("https://example.com/video.mp4")
            mock_session.get.assert_called_once_with(
                "https://example.com/video.mp4",
                stream=True,
            )
            mock_upload.assert_called_once()

    @patch("main.upload")
    @patch("main.get_file_size")
    @patch("main.is_memory_available")
    @patch("main.http_session")
    @patch("main.VIDEO_IN_MEMORY", True)
    def test_video_download_to_memory(
        self, mock_session, mock_memory, mock_get_size, mock_upload
    ):
        """Test video download to memory when enabled."""
        mock_get_size.return_value = 100 * 1024  # 100KB
        mock_memory.return_value = True  # Memory available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])
        mock_session.get.return_value = mock_response

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            download_video(
                "folder_id",
                "https://example.com/video.mp4",
                "video.mp4",
                "mp4",
                "thread_name",
            )

            mock_memory.assert_called_once_with(100 * 1024)
            mock_upload.assert_called_once()
            # Verify stream_data was passed (memory mode)
            call_args = mock_upload.call_args
            assert call_args[0][1] is not None  # stream_data should be BytesIO

    @patch("main.upload")
    @patch("main.get_file_size")
    @patch("main.is_memory_available")
    @patch("main.http_session")
    @patch("main.VIDEO_IN_MEMORY", True)
    def test_video_download_to_disk_when_insufficient_memory(
        self, mock_session, mock_memory, mock_get_size, mock_upload
    ):
        """Test video downloads to disk when memory is insufficient."""
        mock_get_size.return_value = 1024 * 1024 * 1024  # 1GB
        mock_memory.return_value = False  # Memory not available

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])
        mock_session.get.return_value = mock_response

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            download_video(
                "folder_id",
                "https://example.com/video.mp4",
                "video.mp4",
                "mp4",
                "thread_name",
            )

            mock_memory.assert_called_once()
            mock_upload.assert_called_once()
            # Verify file_path was passed (disk mode)
            call_args = mock_upload.call_args
            assert call_args[0][6] is not None  # file_path should be set
            assert call_args[0][1] is None  # stream_data should be None

    @patch("main.get_file_size")
    @patch("main.http_session")
    @patch("main.VIDEO_IN_MEMORY", False)
    def test_video_download_no_file_size(self, mock_session, mock_get_size):
        """Test video download proceeds when file size is unknown."""
        mock_get_size.return_value = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])
        mock_session.get.return_value = mock_response

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            with patch("main.upload") as mock_upload:
                download_video(
                    "folder_id",
                    "https://example.com/video.mp4",
                    "video.mp4",
                    "mp4",
                    "thread_name",
                )

                # Should proceed with disk download
                mock_upload.assert_called_once()

    @patch("main.get_file_size")
    @patch("main.http_session")
    @patch("main.VIDEO_IN_MEMORY", False)
    def test_non_200_status_code(self, mock_session, mock_get_size):
        """Test non-200 status code raises exception."""
        mock_get_size.return_value = 1024

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        with patch("main.retry_with_backoff") as mock_retry:

            def failing_func():
                raise Exception("HTTP 404 error downloading")

            mock_retry.side_effect = failing_func

            with pytest.raises(Exception):
                download_video(
                    "folder_id",
                    "https://example.com/video.mp4",
                    "video.mp4",
                    "mp4",
                    "thread_name",
                )

    @pytest.mark.parametrize(
        ("upload_side_effect", "expected_exception"),
        [(None, None), (Exception("Upload failed"), "Upload failed")],
        ids=["upload_success", "upload_failure"],
    )
    @patch("main.upload")
    @patch("main.get_file_size")
    @patch("main.http_session")
    @patch("main.VIDEO_IN_MEMORY", False)
    def test_temp_file_cleanup(
        self,
        mock_session,
        mock_get_size,
        mock_upload,
        upload_side_effect,
        expected_exception,
    ):
        """Temporary files should be removed regardless of upload outcome."""
        mock_get_size.return_value = 1024

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])
        mock_session.get.return_value = mock_response

        if upload_side_effect:
            mock_upload.side_effect = upload_side_effect

        with patch("main.retry_with_backoff") as mock_retry:
            mock_retry.side_effect = lambda func: func()

            with patch("main.unlink") as mock_unlink:
                if expected_exception:
                    with pytest.raises(Exception, match=expected_exception):
                        download_video(
                            "folder_id",
                            "https://example.com/video.mp4",
                            "video.mp4",
                            "mp4",
                            "thread_name",
                        )
                else:
                    download_video(
                        "folder_id",
                        "https://example.com/video.mp4",
                        "video.mp4",
                        "mp4",
                        "thread_name",
                    )

                mock_unlink.assert_called_once()
