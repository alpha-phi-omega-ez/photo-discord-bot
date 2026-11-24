"""Tests for deterministic thread-name sanitization."""

from main import sanitize_folder_name


def test_benign_name_is_unchanged():
    assert sanitize_folder_name("Summer 2024") == "Summer 2024"


def test_apostrophe_and_punctuation_are_replaced():
    assert sanitize_folder_name("O'Brien's Photos!") == "O_Brien_s Photos"


def test_unsafe_runs_collapse_to_one_underscore():
    assert sanitize_folder_name("a&&&b") == "a_b"


def test_input_is_deterministic():
    name = "Trip: 2024/2025 (final)"
    assert sanitize_folder_name(name) == sanitize_folder_name(name)


def test_emoji_only_name_falls_back():
    assert sanitize_folder_name("📷📷") == "attachments"


def test_name_is_truncated_to_100_chars():
    assert sanitize_folder_name("a" * 200) == "a" * 100
