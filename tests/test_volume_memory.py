"""Unit tests for the per-context volume memory (Qt-free)."""

from __future__ import annotations

from eq_helper.ui.volume_memory import VolumeMemory


def test_current_returns_the_right_bucket() -> None:
    vm = VolumeMemory(regular=0.1, grey=0.4)
    assert vm.current(is_grey=False) == 0.1
    assert vm.current(is_grey=True) == 0.4


def test_remember_grey_does_not_touch_regular() -> None:
    vm = VolumeMemory(regular=0.1, grey=0.4)
    vm.remember(is_grey=True, value=0.6)
    assert vm.grey == 0.6
    assert vm.regular == 0.1
    assert vm.current(is_grey=True) == 0.6


def test_remember_regular_does_not_touch_grey() -> None:
    vm = VolumeMemory(regular=0.1, grey=0.4)
    vm.remember(is_grey=False, value=0.2)
    assert vm.regular == 0.2
    assert vm.grey == 0.4
    assert vm.current(is_grey=False) == 0.2
