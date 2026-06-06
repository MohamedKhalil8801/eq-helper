"""Tests for sine, pink noise, and log sweep generators."""

from __future__ import annotations

import math

import numpy as np
import pytest

from eq_helper.audio.generators import (
    LogSweepGenerator,
    PingPongSweepGenerator,
    PinkNoiseGenerator,
    SineGenerator,
)
from eq_helper.dsp.fft import dominant_frequency


SR = 48000


# --------------------------------------------------------------------- sine


def test_sine_peak_frequency() -> None:
    gen = SineGenerator(SR)
    freq = 1000.0
    # 4096 samples ≈ 85 ms — plenty for a clean FFT peak.
    block = gen.next_block(4096, freq)
    assert block.dtype == np.float32
    assert block.shape == (4096,)
    peak = dominant_frequency(block, SR)
    assert abs(peak - freq) < 10.0


def test_sine_phase_continuity_across_blocks() -> None:
    gen = SineGenerator(SR)
    freq = 440.0
    a = gen.next_block(512, freq)
    b = gen.next_block(512, freq)
    joined = np.concatenate([a, b])
    # The discontinuity test: neighbouring samples in a 440 Hz sine should
    # differ by at most 2*pi*f/SR ≈ 0.0576, but with some margin for phase.
    diffs = np.diff(joined)
    assert np.max(np.abs(diffs)) < 0.2


def test_sine_amplitude_bounds() -> None:
    gen = SineGenerator(SR)
    block = gen.next_block(2048, 500.0)
    assert np.all(block >= -1.0 - 1e-6)
    assert np.all(block <= 1.0 + 1e-6)


# ---------------------------------------------------------------- pink noise


def test_pink_noise_slope_is_roughly_minus_three_db_per_octave() -> None:
    gen = PinkNoiseGenerator(SR, seed=42)
    # ~5.5 seconds of noise
    samples = gen.next_block(262144)
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64) * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / SR)
    mask = (freqs >= 100.0) & (freqs <= 10000.0)
    f = freqs[mask]
    s = spectrum[mask]
    # Linear regression of log10(magnitude) vs log10(freq).
    slope, _ = np.polyfit(np.log10(f), np.log10(s + 1e-18), 1)
    # A true 1/f (pink) spectrum has slope -0.5 in amplitude (-1 in power).
    # Voss-McCartney with 16 rows is approximate, so we allow a wide band.
    assert -1.0 < slope < -0.15, f"slope was {slope}"


def test_pink_noise_is_deterministic_with_seed() -> None:
    a = PinkNoiseGenerator(SR, seed=7).next_block(1024)
    b = PinkNoiseGenerator(SR, seed=7).next_block(1024)
    np.testing.assert_allclose(a, b)


# ------------------------------------------------------------------ sweep


def test_log_sweep_instantaneous_frequency_at_boundaries() -> None:
    f_start, f_end, duration = 20.0, 20000.0, 10.0
    sweep = LogSweepGenerator(f_start, f_end, duration, SR, loop=False)
    assert sweep.current_freq == pytest.approx(f_start, rel=1e-6)
    # Advance to the midpoint; geometric mean should be the instantaneous freq.
    sweep._n = int(duration * SR / 2)
    assert sweep.current_freq == pytest.approx(math.sqrt(f_start * f_end), rel=0.03)
    # End of sweep.
    sweep._n = int(duration * SR)
    assert sweep.current_freq == pytest.approx(f_end, rel=1e-3)


def test_log_sweep_phase_continuity() -> None:
    sweep = LogSweepGenerator(100.0, 10000.0, 2.0, SR, loop=False)
    a = sweep.next_block(1024)
    b = sweep.next_block(1024)
    joined = np.concatenate([a, b])
    # The sweep is a sine of varying frequency — max per-sample delta is bounded
    # by the highest instantaneous frequency's slew rate. At f ≈ 2.5 kHz after
    # 2*1024 samples the delta is ≈ 2*pi*2500/48000 ≈ 0.33; allow 0.6.
    assert np.max(np.abs(np.diff(joined))) < 0.6
    # No NaNs or infs.
    assert np.isfinite(joined).all()


def test_log_sweep_loop_wraps_to_start() -> None:
    duration = 0.2  # short so the test is fast
    sweep = LogSweepGenerator(100.0, 5000.0, duration, SR, loop=True)
    # Consume one full sweep plus a little more.
    total = int(duration * SR) + 256
    sweep.next_block(total)
    # After wrap the instantaneous freq should be back near f_start.
    assert sweep.current_freq < 500.0


def test_log_sweep_no_loop_goes_silent() -> None:
    duration = 0.2  # above the generator's internal minimum duration
    sweep = LogSweepGenerator(100.0, 5000.0, duration, SR, loop=False)
    total = int(round(duration * SR))
    sweep.next_block(total)
    assert sweep.finished
    extra = sweep.next_block(512)
    assert np.all(extra == 0.0)


def test_log_sweep_duration_change_preserves_instantaneous_freq() -> None:
    sweep = LogSweepGenerator(20.0, 20000.0, 10.0, SR, loop=False)
    # Consume 2.5 s of sweep.
    sweep.next_block(int(2.5 * SR))
    freq_before = sweep.current_freq
    sweep.set_duration(30.0)
    freq_after = sweep.current_freq
    # Instantaneous frequency should be nearly unchanged (within integer-sample
    # rounding error on the re-mapped index).
    assert abs(freq_before - freq_after) / freq_before < 0.02


def test_log_sweep_finite_values() -> None:
    sweep = LogSweepGenerator(20.0, 20000.0, 5.0, SR)
    for _ in range(10):
        b = sweep.next_block(1024)
        assert np.isfinite(b).all()
        assert np.max(np.abs(b)) <= 1.0 + 1e-6


# -------------------------------------------------------- ping-pong sweep


def test_ping_pong_block_shape_and_amplitude() -> None:
    g = PingPongSweepGenerator(500.0, 1000.0, duration_s=1.0, sample_rate=SR)
    block = g.next_block(1024)
    assert block.dtype == np.float32
    assert block.shape == (1024,)
    assert np.max(np.abs(block)) <= 1.0 + 1e-6
    assert np.isfinite(block).all()


def test_ping_pong_traverses_up_then_back_down() -> None:
    # One-way duration T = 1.0 s, so the full up-down cycle is 2T.
    g = PingPongSweepGenerator(500.0, 1000.0, duration_s=1.0, sample_rate=SR)
    assert g.current_freq == pytest.approx(500.0, rel=1e-3)      # starts at bottom
    g.next_block(SR // 2)                                        # +T/2 -> log midpoint
    assert g.current_freq == pytest.approx(math.sqrt(500.0 * 1000.0), rel=1e-2)
    g.next_block(SR // 2)                                        # +T -> top (turn-around)
    assert g.current_freq == pytest.approx(1000.0, rel=1e-2)
    g.next_block(SR)                                             # +2T total -> back to bottom
    assert g.current_freq == pytest.approx(500.0, rel=1e-2)


def test_ping_pong_stays_within_band() -> None:
    g = PingPongSweepGenerator(500.0, 1000.0, duration_s=0.25, sample_rate=SR)
    freqs = []
    for _ in range(40):                 # several full cycles
        g.next_block(256)
        freqs.append(g.current_freq)
    assert min(freqs) >= 500.0 - 1.0
    assert max(freqs) <= 1000.0 + 1.0


def test_ping_pong_is_click_free_across_blocks() -> None:
    g = PingPongSweepGenerator(500.0, 1000.0, duration_s=1.0, sample_rate=SR)
    b1 = g.next_block(512)
    b2 = g.next_block(512)
    joined = np.concatenate([b1, b2])
    # A click at the block boundary would show up as a large sample-to-sample
    # jump; for a <=1 kHz sine the step is bounded by ~2*pi*1000/48000 ≈ 0.13.
    assert np.max(np.abs(np.diff(joined))) < 0.2


def test_ping_pong_rejects_invalid_freqs() -> None:
    with pytest.raises(ValueError):
        PingPongSweepGenerator(0.0, 1000.0, 1.0, SR)
    with pytest.raises(ValueError):
        PingPongSweepGenerator(500.0, 500.0, 1.0, SR)
