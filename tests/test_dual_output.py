"""Tests for dual-output device routing."""

from __future__ import annotations

import numpy as np

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import ChannelMode, DualRouting

SR = 48000


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


# ------------------------------------------------------------ mirror mode


def test_mirror_both_devices_identical() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.MIRROR)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    np.testing.assert_array_equal(out_a, out_b)
    assert _rms(out_a[:, 0]) > 0.05


def test_mirror_channel_left_mutes_right_ear_on_both() -> None:
    """Channel=L in mirror → both devices play left ear only."""
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.MIRROR, channel_mode=ChannelMode.LEFT)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    # Both devices identical.
    np.testing.assert_array_equal(out_a, out_b)
    # Left ear plays, right ear silent on both.
    assert _rms(out_a[:, 0]) > 0.05
    assert np.all(out_a[:, 1] == 0.0)


def test_mirror_channel_right_mutes_left_ear_on_both() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.MIRROR, channel_mode=ChannelMode.RIGHT)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    np.testing.assert_array_equal(out_a, out_b)
    assert np.all(out_a[:, 0] == 0.0)
    assert _rms(out_a[:, 1]) > 0.05


# ---------------------------------------------------------- split L/R mode


def test_split_device_a_left_ear_only() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.SPLIT_LR)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    assert _rms(out_a[:, 0]) > 0.05
    assert np.all(out_a[:, 1] == 0.0)


def test_split_device_b_right_ear_only() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.SPLIT_LR)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    assert np.all(out_b[:, 0] == 0.0)
    assert _rms(out_b[:, 1]) > 0.05


def test_split_channel_left_plays_device_a_only() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.SPLIT_LR, channel_mode=ChannelMode.LEFT)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    assert _rms(out_a[:, 0]) > 0.05  # device A left ear plays
    assert np.all(out_b == 0.0)       # device B silent


def test_split_channel_right_plays_device_b_only() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.SPLIT_LR, channel_mode=ChannelMode.RIGHT)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    assert np.all(out_a == 0.0)       # device A silent
    assert _rms(out_b[:, 1]) > 0.05  # device B right ear plays


def test_split_channel_both_plays_both_devices() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.SPLIT_LR, channel_mode=ChannelMode.BOTH)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    assert _rms(out_a[:, 0]) > 0.05
    assert _rms(out_b[:, 1]) > 0.05


def test_split_same_mono_signal_on_both() -> None:
    """Both devices get the same mono content, just in opposite ears."""
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=True, dual_routing=DualRouting.SPLIT_LR)
    engine.start_sine(freq=500.0)
    engine.set_volume(0.5)
    out_a, out_b = engine.process_block_dual(1024)
    np.testing.assert_allclose(out_a[:, 0], out_b[:, 1], atol=1e-6)


# --------------------------------------------------------------- general


def test_dual_off_uses_normal_channel_routing() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.state.update(dual_output=False, channel_mode=ChannelMode.LEFT)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out = engine.process_block(1024)
    assert _rms(out[:, 0]) > 0.05
    assert np.all(out[:, 1] == 0.0)


def test_device_enumeration_returns_list() -> None:
    from eq_helper.audio.engine import list_output_devices
    devices = list_output_devices()
    assert isinstance(devices, list)
    for dev_id, name in devices:
        assert isinstance(dev_id, int)
        assert isinstance(name, str)
