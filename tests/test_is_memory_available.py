"""Tests for main.is_memory_available.

The guard hardcodes a 20000-byte safety margin: a file fits only while
``available_memory - 20000 > file_size``. The threshold is exclusive, so
a file of exactly ``available - 20000`` bytes is rejected. Phase 4 makes
the margin configurable.
"""

from unittest.mock import MagicMock

import main
from main import is_memory_available

AVAILABLE_MEMORY = 1_000_000


def _mock_vm(monkeypatch):
    vm = MagicMock()
    vm.available = AVAILABLE_MEMORY
    vm_mock = MagicMock(return_value=vm)
    monkeypatch.setattr(main, "virtual_memory", vm_mock)
    return vm_mock


def test_fits_just_below_margin(monkeypatch):
    _mock_vm(monkeypatch)
    assert is_memory_available(AVAILABLE_MEMORY - 20_001) is True


def test_exact_threshold_is_rejected(monkeypatch):
    _mock_vm(monkeypatch)
    assert is_memory_available(AVAILABLE_MEMORY - 20_000) is False


def test_fails_just_above_margin(monkeypatch):
    _mock_vm(monkeypatch)
    assert is_memory_available(AVAILABLE_MEMORY - 19_999) is False


def test_small_file_fits_large_file_does_not(monkeypatch):
    _mock_vm(monkeypatch)
    assert is_memory_available(1) is True
    assert is_memory_available(AVAILABLE_MEMORY) is False
