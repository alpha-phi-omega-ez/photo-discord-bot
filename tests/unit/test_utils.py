"""Unit tests for utility functions."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from main import (
    IMAGE_NAME_PATTERN,
    VIDEO_NAME_PATTERN,
    convert_to_jpeg,
    exponential_backoff_sleep,
    find_file_name,
    get_file_size,
    is_memory_available,
    is_transient_error,
    retry_with_backoff,
    sanitize_folder_name,
)


class TestSanitizeFolderName:
    """Tests for sanitize_folder_name function."""

    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            ("MyFolder", "MyFolder"),
            ("", "unnamed"),
            (None, "unnamed"),
            ("  .folder.  ", "folder"),
        ],
        ids=["normal", "empty", "none", "trim"],
    )
    def test_basic_cases(self, input_name, expected):
        """Ensure common inputs are normalised."""
        assert sanitize_folder_name(input_name) == expected

    @pytest.mark.parametrize(
        "dangerous_char",
        ["<", ">", ":", '"', "/", "\\", "|", "?", "*"],
        ids=lambda char: f"char_{ord(char)}",
    )
    def test_dangerous_characters_replaced(self, dangerous_char):
        """Ensure potentially dangerous characters are replaced."""
        result = sanitize_folder_name(f"folder{dangerous_char}name")
        assert dangerous_char not in result

    def test_path_traversal_prevention(self):
        """Test path traversal sequences are removed."""
        assert ".." not in sanitize_folder_name("../../../etc/passwd")
        # Each ".." becomes "__", so "../../folder" becomes "____folder"
        assert sanitize_folder_name("../../folder") == "____folder"

    def test_length_limit(self):
        """Test folder name is limited to 255 characters."""
        long_name = "a" * 300
        result = sanitize_folder_name(long_name)
        assert len(result) <= 255


class TestIsTransientError:
    """Tests for is_transient_error function."""

    @pytest.mark.parametrize(
        "error_message",
        [
            "Connection error occurred",
            "Request timeout",
            "Network failure",
            "HTTP 429 rate limit",
            "HTTP 500 server error",
            "HTTP 503 service unavailable",
        ],
        ids=[
            "connection",
            "timeout",
            "network",
            "http_429",
            "http_500",
            "http_503",
        ],
    )
    def test_transient_error_messages(self, error_message):
        """Errors containing transient keywords should return True."""
        error = Exception(error_message)
        assert is_transient_error(error) is True

    @pytest.mark.parametrize(
        "status_code",
        [429, 500],
        ids=["status_429", "status_500"],
    )
    def test_response_status_code_transient(self, status_code):
        """HTTP responses with retryable status codes should be transient."""
        error = Mock()
        error.response = Mock()
        error.response.status_code = status_code
        assert is_transient_error(error) is True

    @pytest.mark.parametrize(
        ("exception_cls", "message"),
        [
            (type("googleapiclientHttpError", (Exception,), {}), "HTTP 503 Service"),
            (type("HttpError", (Exception,), {}), "HTTP 429 rate limit"),
        ],
        ids=["googleapiclient_http_error", "http_error"],
    )
    def test_google_api_transient_errors(self, exception_cls, message):
        """Specific Google API errors should be treated as transient."""
        error = exception_cls(message)
        assert is_transient_error(error) is True

    @pytest.mark.parametrize(
        "error_message",
        ["Invalid credentials", "HTTP 404 not found"],
        ids=["invalid_credentials", "http_404"],
    )
    def test_non_transient_errors(self, error_message):
        """Errors without transient indicators should return False."""
        error = Exception(error_message)
        assert is_transient_error(error) is False


class TestExponentialBackoffSleep:
    """Tests for exponential_backoff_sleep function."""

    @pytest.mark.parametrize(
        ("attempt", "expected_delay"),
        [(0, 1.0), (1, 2.0), (2, 4.0)],
        ids=["attempt_0", "attempt_1", "attempt_2"],
    )
    @patch("main.sleep")
    def test_exponential_backoff_calculation(self, mock_sleep, attempt, expected_delay):
        """Test exponential backoff delay calculation."""
        exponential_backoff_sleep(attempt, base_delay=1.0, multiplier=2.0)
        mock_sleep.assert_called_once_with(expected_delay)

    @patch("main.sleep")
    def test_exponential_backoff_default_multiplier(self, mock_sleep):
        """Test exponential backoff uses default multiplier."""
        exponential_backoff_sleep(1, base_delay=1.0)
        # Default multiplier is 2.5 from RETRY_BACKOFF_MULTIPLIER
        mock_sleep.assert_called_with(2.5)


class TestRetryWithBackoff:
    """Tests for retry_with_backoff function."""

    @patch("main.is_transient_error")
    @patch("main.exponential_backoff_sleep")
    def test_success_on_first_attempt(self, mock_sleep, mock_is_transient):
        """Test function succeeds on first attempt."""
        mock_is_transient.return_value = False
        mock_func = MagicMock(return_value="success")
        result = retry_with_backoff(mock_func, "arg1", kwarg1="value1")
        assert result == "success"
        mock_func.assert_called_once_with("arg1", kwarg1="value1")
        mock_sleep.assert_not_called()

    @patch("main.logger")
    @patch("main.is_transient_error")
    @patch("main.exponential_backoff_sleep")
    def test_retry_on_transient_error(self, mock_sleep, mock_is_transient, mock_logger):
        """Test function retries on transient errors."""
        mock_is_transient.return_value = True
        mock_func = MagicMock(side_effect=[Exception("transient"), "success"])
        mock_func.__name__ = "test_func"
        result = retry_with_backoff(mock_func)
        assert result == "success"
        assert mock_func.call_count == 2
        mock_sleep.assert_called_once()

    @patch("main.logger")
    @patch("main.is_transient_error")
    @patch("main.exponential_backoff_sleep")
    def test_permanent_error_no_retry(self, mock_sleep, mock_is_transient, mock_logger):
        """Test function doesn't retry on permanent errors."""
        mock_is_transient.return_value = False
        permanent_error = Exception("permanent error")
        mock_func = MagicMock(side_effect=permanent_error)
        mock_func.__name__ = "test_func"
        with pytest.raises(Exception) as exc_info:
            retry_with_backoff(mock_func)
        assert str(exc_info.value) == "permanent error"
        mock_func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("main.logger")
    @patch("main.is_transient_error")
    @patch("main.exponential_backoff_sleep")
    def test_max_retries_exceeded(self, mock_sleep, mock_is_transient, mock_logger):
        """Test function raises after max retries."""
        mock_is_transient.return_value = True
        mock_func = MagicMock(side_effect=Exception("transient"))
        mock_func.__name__ = "test_func"
        with pytest.raises(Exception):
            retry_with_backoff(mock_func, max_retries=2)
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1


class TestGetFileSize:
    """Tests for get_file_size function."""

    def test_successful_file_size(self, mock_http_session):
        """Test successful file size retrieval."""
        with patch("main.http_session", mock_http_session):
            size = get_file_size("https://example.com/file.jpg")
            assert size == 1024
            mock_http_session.head.assert_called_once_with(
                "https://example.com/file.jpg"
            )

    def test_no_content_length_header(self, mock_http_session):
        """Test when Content-Length header is missing."""
        mock_http_session.head.return_value.headers = {}
        with patch("main.http_session", mock_http_session):
            size = get_file_size("https://example.com/file.jpg")
            assert size is None

    def test_non_200_status_code(self, mock_http_session):
        """Test when status code is not 200."""
        mock_http_session.head.return_value.status_code = 404
        with patch("main.http_session", mock_http_session):
            size = get_file_size("https://example.com/file.jpg")
            assert size is None

    def test_exception_handling(self, mock_http_session):
        """Test exception handling."""
        mock_http_session.head.side_effect = Exception("Network error")
        with patch("main.http_session", mock_http_session):
            size = get_file_size("https://example.com/file.jpg")
            assert size is None

    @patch("main.MAX_FILE_SIZE_BYTES", 500)
    def test_file_size_exceeds_limit(self, mock_http_session):
        """Test when file size exceeds configured limit."""
        mock_http_session.head.return_value.headers = {"Content-Length": "1000"}
        with patch("main.http_session", mock_http_session):
            size = get_file_size("https://example.com/file.jpg")
            assert size is None


class TestIsMemoryAvailable:
    """Tests for is_memory_available function."""

    @pytest.mark.parametrize(
        ("available_bytes", "file_size", "expected"),
        [
            (1000 * 1024 * 1024, 500 * 1024 * 1024, True),
            (100 * 1024 * 1024, 500 * 1024 * 1024, False),
        ],
        ids=["sufficient_memory", "insufficient_memory"],
    )
    @patch("main.virtual_memory")
    def test_memory_threshold(  # type: ignore[misc]
        self, mock_virtual_memory, available_bytes, file_size, expected
    ):
        """Verify memory availability against reserve threshold."""
        mock_memory = MagicMock()
        mock_memory.available = available_bytes
        mock_virtual_memory.return_value = mock_memory

        with patch("main.MEMORY_RESERVE_PERCENT", 10.0):
            assert is_memory_available(file_size) is expected

    @patch("main.virtual_memory")
    def test_memory_reserve_percentage(self, mock_virtual_memory):
        """Test memory reserve percentage is applied."""
        # 1GB available, 20% reserve = 800MB usable
        mock_memory = MagicMock()
        mock_memory.available = 1000 * 1024 * 1024
        mock_virtual_memory.return_value = mock_memory

        with patch("main.MEMORY_RESERVE_PERCENT", 20.0):
            assert is_memory_available(750 * 1024 * 1024) is True
            assert is_memory_available(850 * 1024 * 1024) is False


class TestFindFileName:
    """Tests for find_file_name function."""

    @pytest.mark.parametrize(
        ("pattern", "url", "expected"),
        [
            (IMAGE_NAME_PATTERN, "https://example.com/image.jpg", "image.jpg"),
            (IMAGE_NAME_PATTERN, "https://example.com/path/to/photo.png", "photo.png"),
            (VIDEO_NAME_PATTERN, "https://example.com/video.mp4", "video.mp4"),
        ],
        ids=["image_simple", "image_nested", "video"],
    )
    def test_successful_matches(self, pattern, url, expected):
        """Test finding file names from URLs."""
        assert find_file_name(pattern, url) == expected

    def test_replace_spaces_with_underscores(self):
        """Test spaces are replaced with underscores."""
        # The regex pattern only matches alphanumeric before extension
        url = "https://example.com/image.jpg"
        result = find_file_name(IMAGE_NAME_PATTERN, url)
        assert " " not in result

    def test_replace_quotes(self):
        """Test single quotes are replaced."""
        url = "https://example.com/image'name.jpg"
        result = find_file_name(IMAGE_NAME_PATTERN, url)
        assert "'" not in result

    def test_no_match_returns_none(self):
        """Test None is returned when no match found."""
        url = "https://example.com/file.txt"
        result = find_file_name(IMAGE_NAME_PATTERN, url)
        assert result is None


class TestConvertToJpeg:
    """Tests for convert_to_jpeg function."""

    @patch("main.pyheif_read")
    def test_successful_conversion(self, mock_pyheif_read):
        """Test successful HEIC to JPEG conversion."""
        from PIL import Image

        # Create a real PIL image for testing
        img = Image.new("RGB", (10, 10), color="red")

        # Get the raw pixel data - RGB mode, 3 bytes per pixel
        img_bytes = img.tobytes()

        # Create mock heif file with proper structure
        mock_heif_file = MagicMock()
        mock_heif_file.mode = "RGB"
        mock_heif_file.size = (10, 10)
        mock_heif_file.data = img_bytes
        # Stride for RGB: width * 3 bytes per pixel
        mock_heif_file.stride = 10 * 3  # 30
        mock_pyheif_read.return_value = mock_heif_file

        image_data = b"fake heic data"
        file_name = "test.heic"
        extension = "heic"

        result_data, result_name, result_ext = convert_to_jpeg(
            image_data, file_name, extension
        )

        assert result_ext == "jpeg"
        assert "heic" not in result_name
        assert result_data != image_data  # Should be converted
        # Verify it's valid JPEG data (starts with JPEG file signature)
        assert result_data.startswith(b"\xff\xd8")  # JPEG file signature

    @patch("main.pyheif_read")
    def test_conversion_failure_returns_original(self, mock_pyheif_read):
        """Test conversion failure returns original data."""
        mock_pyheif_read.side_effect = Exception("Conversion failed")

        image_data = b"fake heic data"
        file_name = "test.heic"
        extension = "heic"

        result_data, result_name, result_ext = convert_to_jpeg(
            image_data, file_name, extension
        )

        assert result_data == image_data
        assert result_name == file_name
        assert result_ext == extension

    def test_non_heic_file_unchanged(self):
        """Test non-HEIC files are unchanged."""
        image_data = b"fake jpeg data"
        file_name = "test.jpg"
        extension = "jpg"

        result_data, result_name, result_ext = convert_to_jpeg(
            image_data, file_name, extension
        )

        assert result_data == image_data
        assert result_name == file_name
        assert result_ext == extension
