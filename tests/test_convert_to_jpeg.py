"""Tests for main.convert_to_jpeg (rename bugs pinned for Phase 3)."""

from unittest.mock import MagicMock

import pytest

import main
from main import convert_to_jpeg


def _mock_conversion(monkeypatch, heif_read_side_effect=None):
    """Install pyheif/Pillow mocks; returns (pyheif_read, Image, image)."""
    heif_file = MagicMock()
    heif_file.mode = "RGBA"
    heif_file.size = (2, 2)
    heif_file.data = b"\x01" * 16
    heif_file.stride = 8
    pyheif_read_mock = MagicMock(return_value=heif_file)
    if heif_read_side_effect is not None:
        pyheif_read_mock.side_effect = heif_read_side_effect
    monkeypatch.setattr(main, "pyheif_read", pyheif_read_mock)

    image = MagicMock()
    image.save.side_effect = lambda stream, **kwargs: stream.write(b"jpeg-bytes")
    image_cls = MagicMock()
    image_cls.frombytes.return_value = image
    monkeypatch.setattr(main, "Image", image_cls)

    return pyheif_read_mock, image_cls, image


def test_success_converts_and_returns_jpeg_tuple(monkeypatch):
    pyheif_read_mock, image_cls, image = _mock_conversion(monkeypatch)
    image_data = b"raw-heic-bytes"

    result = convert_to_jpeg(image_data, "photo.heic", "heic")

    assert result == (b"jpeg-bytes", "photo.jpeg", "jpeg")
    pyheif_read_mock.assert_called_once()
    assert pyheif_read_mock.call_args.args[0].getvalue() == image_data
    image_cls.frombytes.assert_called_once_with(
        "RGBA", (2, 2), b"\x01" * 16, "raw", "RGBA", 8
    )
    image.save.assert_called_once()
    assert image.save.call_args.args[0].getvalue() == b"jpeg-bytes"
    assert image.save.call_args.kwargs == {"format": "JPEG"}


def test_converter_failure_returns_original_tuple_unchanged(monkeypatch):
    _mock_conversion(monkeypatch, heif_read_side_effect=Exception("libheif boom"))
    image_data = b"raw-heic-bytes"

    result = convert_to_jpeg(image_data, "photo.heic", "heic")

    assert result == (image_data, "photo.heic", "heic")


@pytest.mark.xfail(
    reason=".heif uploads keep the .heif filename though content is JPEG; fixed"
    " in Phase 3",
    strict=True,
)
def test_heif_name_is_renamed_to_jpeg(monkeypatch):
    _mock_conversion(monkeypatch)
    result = convert_to_jpeg(b"raw-heic-bytes", "photo.heif", "heif")
    assert result == (b"jpeg-bytes", "photo.jpeg", "jpeg")


@pytest.mark.xfail(
    reason='replace("heic", "jpeg") mangles stems containing "heic"; fixed in Phase 3',
    strict=True,
)
def test_stem_containing_heic_is_not_mangled(monkeypatch):
    _mock_conversion(monkeypatch)
    result = convert_to_jpeg(b"raw-heic-bytes", "heicher.heic", "heic")
    assert result == (b"jpeg-bytes", "heicher.jpeg", "jpeg")
