"""Tests for microphone input, monitoring, recording, and playback."""

from __future__ import annotations

import numpy as np

from eq_helper.audio.engine import AudioEngine, RingBuffer
from eq_helper.audio.shared_state import Mode
from eq_helper.dsp.fft import dominant_frequency

SR = 48000


# ---------------------------------------------------------------- RingBuffer.pop


def test_ring_buffer_pop_returns_pushed_data() -> None:
    buf = RingBuffer(capacity=1024)
    data = np.arange(100, dtype=np.float32)
    buf.push(data)
    out = buf.pop(100)
    np.testing.assert_array_equal(out, data)


def test_ring_buffer_pop_removes_data() -> None:
    buf = RingBuffer(capacity=1024)
    buf.push(np.ones(50, dtype=np.float32))
    first = buf.pop(50)
    assert first.shape == (50,)
    second = buf.pop(50)
    assert second.shape == (0,)


def test_ring_buffer_pop_partial() -> None:
    buf = RingBuffer(capacity=1024)
    buf.push(np.ones(30, dtype=np.float32))
    out = buf.pop(100)  # request more than available
    assert out.shape == (30,)


def test_ring_buffer_pop_wrapping() -> None:
    buf = RingBuffer(capacity=64)
    # Push 0-49 (50 items), pop 40 → leaves [40-49].
    buf.push(np.arange(50, dtype=np.float32))
    buf.pop(40)
    # Push 50-79 (30 more items) → buffer now has [40-79] = 40 items.
    buf.push(np.arange(50, 80, dtype=np.float32))
    out = buf.pop(40)
    expected = np.arange(40, 80, dtype=np.float32)
    np.testing.assert_array_equal(out, expected)


# ----------------------------------------------------------- mic input callback


def test_mic_input_populates_mic_ring() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    tone = np.sin(2 * np.pi * 440 * np.arange(4096) / SR).astype(np.float32)
    # Simulate multiple mic input blocks.
    for start in range(0, 4096, 480):
        engine.simulate_mic_input(tone[start : start + 480])
    samples = engine.mic_ring.read_latest(2048)
    assert samples is not None
    peak = dominant_frequency(samples, SR)
    assert abs(peak - 440) < 15


def test_mic_monitor_routes_to_output() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    engine.set_mic_monitoring(True)
    # Feed enough blocks to prime the monitor buffer (~3 blocks prefill).
    tone = np.sin(2 * np.pi * 1000 * np.arange(480) / SR).astype(np.float32)
    for _ in range(4):
        engine.simulate_mic_input(tone)
    # Drive the output callback — should contain the monitor signal.
    out = engine.process_block(480)
    rms = float(np.sqrt(np.mean(out[:, 0] ** 2)))
    assert rms > 0.01, "monitor signal should be non-silent"


def test_mic_monitor_off_is_silent() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    engine.set_mic_monitoring(False)
    tone = np.sin(2 * np.pi * 1000 * np.arange(480) / SR).astype(np.float32)
    engine.simulate_mic_input(tone)
    out = engine.process_block(480)
    assert np.all(out == 0.0)


# -------------------------------------------------------------- recording


def test_recording_captures_input() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    engine.start_recording()
    tone = np.sin(2 * np.pi * 500 * np.arange(2400) / SR).astype(np.float32)
    for start in range(0, 2400, 480):
        engine.simulate_mic_input(tone[start : start + 480])
    buf = engine.stop_recording()
    assert buf is not None
    assert buf.shape[0] == 2400
    peak = dominant_frequency(buf, SR)
    assert abs(peak - 500) < 15


def test_recording_empty_returns_none() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    engine.start_recording()
    buf = engine.stop_recording()
    assert buf is None


# -------------------------------------------------------------- playback


def test_playback_outputs_buffer() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=4096)
    tone = np.sin(2 * np.pi * 1000 * np.arange(SR) / SR).astype(np.float32)
    engine.start_playback(tone, loop=True)
    engine.set_volume(1.0)
    out = engine.process_block(4096)
    peak = dominant_frequency(out[:, 0], SR)
    assert abs(peak - 1000) < 15


def test_playback_loops() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=1000)
    # Short buffer that will need to loop.
    buf = np.ones(500, dtype=np.float32) * 0.5
    engine.start_playback(buf, loop=True)
    engine.set_volume(1.0)
    out = engine.process_block(1000)
    # Output should be non-zero throughout (looped).
    assert np.all(out[:, 0] != 0.0)


def test_playback_stop() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    buf = np.ones(1000, dtype=np.float32) * 0.5
    engine.start_playback(buf, loop=True)
    engine.stop_playback()
    assert engine.state.snapshot().mode == Mode.IDLE
    out = engine.process_block(480)
    assert np.all(out == 0.0)


def test_playback_with_monitoring_mixes() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=480)
    # Play a DC signal via playback.
    pb_buf = np.ones(SR, dtype=np.float32) * 0.3
    engine.start_playback(pb_buf, loop=True)
    engine.set_volume(1.0)
    # Also enable monitoring with a different signal.
    engine.set_mic_monitoring(True)
    mic = np.ones(480, dtype=np.float32) * 0.2
    # Prime the monitor buffer (need ~3 blocks).
    for _ in range(4):
        engine.simulate_mic_input(mic)
    # Consume one block to sync, then check the next.
    engine.process_block(480)
    out = engine.process_block(480)
    # Output should have both contributions (0.3 + 0.2 = 0.5).
    mean_val = float(np.mean(out[:, 0]))
    assert abs(mean_val - 0.5) < 0.1
