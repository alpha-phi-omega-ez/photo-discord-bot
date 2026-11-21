"""Tests for main.find_file_name (regex gaps pinned for Phase 3)."""

import pytest

from main import IMAGE_NAME_PATTERN, VIDEO_NAME_PATTERN, find_file_name

CDN_BASE = "https://cdn.discordapp.com/attachments/1234/5678"


def test_simple_image_name():
    url = f"{CDN_BASE}/photo.png?ex=67abc123&is=67abc456&hm=deadbeefcafe"
    assert find_file_name(IMAGE_NAME_PATTERN, url) == "photo.png"


def test_simple_video_name():
    url = f"{CDN_BASE}/clip.mp4?ex=67abc123&is=67abc456&hm=deadbeefcafe"
    assert find_file_name(VIDEO_NAME_PATTERN, url) == "clip.mp4"


def test_underscore_names_pass_through():
    url = f"{CDN_BASE}/photo_2024.png?ex=67abc123"
    assert find_file_name(IMAGE_NAME_PATTERN, url) == "photo_2024.png"


@pytest.mark.xfail(
    reason=r"[\w]+ drops hyphens from the extracted filename; fixed in Phase 3",
    strict=True,
)
def test_hyphenated_name_is_preserved():
    url = f"{CDN_BASE}/my-photo.png?ex=67abc123"
    assert find_file_name(IMAGE_NAME_PATTERN, url) == "my-photo.png"


@pytest.mark.xfail(
    reason=r"[\w]+ drops spaces before replace() can map them to underscores;"
    " fixed in Phase 3",
    strict=True,
)
def test_spaced_name_is_preserved():
    url = f"{CDN_BASE}/my photo.png?ex=67abc123"
    assert find_file_name(IMAGE_NAME_PATTERN, url) == "my_photo.png"


def test_uppercase_extensions_require_lowercased_url():
    r"""Contract pin: queue_file_downloads passes url_lower, so callers that
    hand over the raw URL get None because the patterns hardcode lowercase
    extensions."""
    assert find_file_name(IMAGE_NAME_PATTERN, f"{CDN_BASE}/Photo.PNG?ex=1") is None
    assert find_file_name(VIDEO_NAME_PATTERN, f"{CDN_BASE}/Clip.MP4?ex=1") is None
    assert (
        find_file_name(IMAGE_NAME_PATTERN, f"{CDN_BASE}/Photo.PNG?ex=1".lower())
        == "photo.png"
    )


def test_no_match_returns_none():
    assert find_file_name(IMAGE_NAME_PATTERN, f"{CDN_BASE}/no-extension-here") is None
    assert (
        find_file_name(VIDEO_NAME_PATTERN, "https://discord.com/channels/1/2") is None
    )


def test_quote_in_url_does_not_break_matching():
    r"""The captured group is always a ``[\w]+`` segment, so an apostrophe in
    the URL can never appear inside the match and the trailing
    ``replace("'", ...)`` is a no-op today."""
    url = f"{CDN_BASE}/photo.png?ex=67abc&hm=x'y"
    assert find_file_name(IMAGE_NAME_PATTERN, url) == "photo.png"
