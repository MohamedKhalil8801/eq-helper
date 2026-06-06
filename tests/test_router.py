"""Tests for the mono->stereo channel router."""

from __future__ import annotations

import numpy as np

from eq_helper.audio.router import route
from eq_helper.audio.shared_state import ChannelMode


def test_left_mode_mutes_right() -> None:
    block = np.linspace(-1, 1, 256, dtype=np.float32)
    out = route(block, ChannelMode.LEFT, force_mono=True)
    np.testing.assert_array_equal(out[:, 0], block)
    assert np.all(out[:, 1] == 0.0)


def test_right_mode_mutes_left() -> None:
    block = np.linspace(-1, 1, 256, dtype=np.float32)
    out = route(block, ChannelMode.RIGHT, force_mono=True)
    assert np.all(out[:, 0] == 0.0)
    np.testing.assert_array_equal(out[:, 1], block)


def test_both_mode_mono_duplicates() -> None:
    block = np.random.default_rng(0).standard_normal(256).astype(np.float32)
    out = route(block, ChannelMode.BOTH, force_mono=True)
    np.testing.assert_array_equal(out[:, 0], out[:, 1])


def test_both_mode_stereo_decorrelated() -> None:
    rng = np.random.default_rng(1)
    left = rng.standard_normal(1024).astype(np.float32)
    right = rng.standard_normal(1024).astype(np.float32)
    out = route(left, ChannelMode.BOTH, force_mono=False, right_block=right)
    np.testing.assert_array_equal(out[:, 0], left)
    np.testing.assert_array_equal(out[:, 1], right)
    # Sanity: independent randoms should not be identical.
    assert not np.array_equal(out[:, 0], out[:, 1])


def test_output_dtype_and_shape() -> None:
    block = np.zeros(128, dtype=np.float32)
    out = route(block, ChannelMode.BOTH, force_mono=True)
    assert out.shape == (128, 2)
    assert out.dtype == np.float32
