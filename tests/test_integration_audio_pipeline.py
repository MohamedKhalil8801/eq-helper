"""End-to-end integration test for the audio pipeline.

Drives ``AudioEngine._callback`` directly — no PortAudio device required —
and asserts the output buffer matches expectations for each mode and
configuration.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import ChannelMode, Mode
from eq_helper.dsp.fft import dominant_frequency


SR = 48000


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def test_engine_sine_output_has_expected_peak_frequency() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=4096)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    out = engine.process_block(4096)
    assert out.shape == (4096, 2)
    # Peak freq check on the left channel.
    peak = dominant_frequency(out[:, 0], SR)
    assert abs(peak - 1000.0) < 15.0, peak


def test_engine_pink_output_is_broadband() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=8192)
    engine.start_pink()
    engine.set_volume(0.3)
    out = engine.process_block(8192)
    assert out.shape == (8192, 2)
    # Pink noise should have nontrivial energy.
    assert _rms(out[:, 0]) > 0.01


def test_engine_sweep_sweeps_over_time() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=SR)
    engine.start_sweep(f_start=100.0, f_end=10000.0, duration_s=1.0, loop=False)
    engine.set_volume(0.5)

    # First second: the sweep should cover 100 Hz -> 10 kHz. Pull four quarters
    # of the sweep and check the dominant frequency rises monotonically.
    q = SR // 4
    peaks = []
    for _ in range(4):
        out = engine.process_block(q)
        peaks.append(dominant_frequency(out[:, 0], SR))

    # Each successive quarter should have a higher dominant frequency.
    assert peaks[0] < peaks[1] < peaks[2] < peaks[3], peaks
    assert peaks[0] < 500.0
    assert peaks[-1] > 4000.0


def test_engine_focused_sweep_is_centred_on_target() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=SR)
    engine.start_focused_sweep(
        center_hz=1000.0, width_octaves=1.0, seconds_per_octave=3.0
    )
    engine.set_volume(0.5)
    assert engine.state.snapshot().mode == Mode.SWEEP
    out = engine.process_block(SR // 2)  # half a second of the focused sweep
    assert out.shape == (SR // 2, 2)
    assert np.isfinite(out).all()
    peak = dominant_frequency(out[:, 0], SR)
    # Energy must sit inside the one-octave band (~707..1414 Hz), with margin.
    assert 636.0 <= peak <= 1556.0, peak


def test_full_sweep_controls_do_not_disturb_a_running_focused_sweep() -> None:
    from eq_helper.audio.generators import PingPongSweepGenerator

    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.start_focused_sweep(
        center_hz=1000.0, width_octaves=1.0, seconds_per_octave=3.0
    )
    engine.set_volume(0.5)
    assert isinstance(engine._sweep, PingPongSweepGenerator)
    # The *full*-sweep duration/loop controls must not touch the ping-pong sweep.
    engine.set_sweep_duration(45.0)
    engine.set_sweep_loop(False)
    assert isinstance(engine._sweep, PingPongSweepGenerator)
    out = engine.process_block(1024)
    assert np.isfinite(out).all()
    assert _rms(out[:, 0]) > 0.0


def test_update_focused_sweep_changes_band_in_place() -> None:
    from eq_helper.audio.generators import PingPongSweepGenerator

    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.start_focused_sweep(
        center_hz=1000.0, width_octaves=1.0, seconds_per_octave=3.0
    )
    gen_before = engine._sweep

    # Widen to 2 octaves and slow to 4 s/oct while it's running.
    engine.update_focused_sweep(width_octaves=2.0, seconds_per_octave=4.0)

    snap = engine.state.snapshot()
    # Same generator object — updated in place, no restart.
    assert engine._sweep is gen_before
    assert isinstance(engine._sweep, PingPongSweepGenerator)
    assert snap.sweep_f_start == pytest.approx(500.0, rel=1e-3)
    assert snap.sweep_f_end == pytest.approx(2000.0, rel=1e-3)
    assert snap.sweep_duration_s == pytest.approx(8.0)  # 2 oct * 4 s/oct
    # Centre is preserved across the width change (geometric mean unchanged).
    assert math.sqrt(snap.sweep_f_start * snap.sweep_f_end) == pytest.approx(1000.0, rel=1e-3)


def test_update_focused_sweep_ignored_for_full_sweep() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.start_sweep(f_start=20.0, f_end=20000.0, duration_s=10.0, loop=True)
    # The focus controls must not disturb a full-range sweep.
    engine.update_focused_sweep(width_octaves=2.0, seconds_per_octave=4.0)
    snap = engine.state.snapshot()
    assert snap.sweep_f_start == pytest.approx(20.0)
    assert snap.sweep_f_end == pytest.approx(20000.0)


def test_engine_channel_mode_left_mutes_right() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    engine.set_channel_mode(ChannelMode.LEFT)
    out = engine.process_block(1024)
    assert _rms(out[:, 0]) > 0.05
    assert np.all(out[:, 1] == 0.0)


def test_engine_channel_mode_right_mutes_left() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    engine.set_channel_mode(ChannelMode.RIGHT)
    out = engine.process_block(1024)
    assert np.all(out[:, 0] == 0.0)
    assert _rms(out[:, 1]) > 0.05


def test_engine_channel_mode_both_plays_on_both() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1024)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    engine.set_channel_mode(ChannelMode.BOTH)
    out = engine.process_block(1024)
    assert _rms(out[:, 0]) > 0.05
    assert _rms(out[:, 1]) > 0.05


def test_engine_band_filter_passes_in_band_sine() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=8192)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    engine.set_band_filter(fc=1000.0, bw=400.0, enabled=True)
    out = engine.process_block(8192)
    rms_in_band = _rms(out[:, 0])
    # In-band: filter shouldn't kill the signal.
    assert rms_in_band > 0.05


def test_engine_band_filter_rejects_out_of_band_sine() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=8192)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    # A very narrow band far from 1 kHz -> signal should be heavily attenuated.
    engine.set_band_filter(fc=100.0, bw=30.0, enabled=True)
    # Let the filter settle: process several blocks and check the last one.
    for _ in range(4):
        out = engine.process_block(8192)
    rms_rejected = _rms(out[:, 0])

    # Reference: the same sine without filtering.
    engine2 = AudioEngine(sample_rate=SR, block_size=8192)
    engine2.start_sine(freq=1000.0)
    engine2.set_volume(0.5)
    ref = engine2.process_block(8192)
    rms_ref = _rms(ref[:, 0])

    # At least 20 dB of attenuation (factor 10).
    assert rms_rejected < rms_ref / 10.0, (rms_rejected, rms_ref)


def test_engine_idle_outputs_silence() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=512)
    out = engine.process_block(512)
    assert np.all(out == 0.0)


def test_engine_ring_buffer_captures_recent_samples() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=2048)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    # Push several blocks so the buffer fills.
    for _ in range(4):
        engine.process_block(2048)
    recent = engine.pop_recent_samples(2048)
    assert recent is not None
    assert recent.shape == (2048,)
    peak = dominant_frequency(recent, SR)
    assert abs(peak - 1000.0) < 15.0


def test_engine_sweep_loops_when_configured() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=SR)
    engine.start_sweep(f_start=500.0, f_end=4000.0, duration_s=0.25, loop=True)
    engine.set_volume(0.5)
    # Consume multiple full sweeps worth of samples.
    total = SR  # 1 second = 4 full sweeps
    acc = []
    remaining = total
    while remaining > 0:
        take = min(remaining, 4096)
        acc.append(engine.process_block(take))
        remaining -= take
    full = np.concatenate(acc)
    # No NaN/Inf and nontrivial energy throughout.
    assert np.isfinite(full).all()
    assert _rms(full[:, 0]) > 0.05
