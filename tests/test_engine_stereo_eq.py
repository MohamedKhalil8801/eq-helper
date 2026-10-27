"""Per-channel FIR preview path: separate L/R filters, click-free switching, cheap."""

import time

import numpy as np
import pytest

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import ChannelMode
from eq_helper.audio.stereo_fir import PartitionedConvolver, StereoFIR, channel_firs, gain_fir
from eq_helper.dsp.convolution import design_channel
from eq_helper.dsp.resonance import Measurements, match_channels

FS = 48000


def _gain_at(fir, hz, fs=FS):
    w = np.exp(-2j * np.pi * hz / fs * np.arange(len(fir)))
    return abs(np.dot(fir, w))


def _tone_amp(x, hz, fs=FS):
    t = np.arange(len(x)) / fs
    basis = np.c_[np.sin(2 * np.pi * hz * t), np.cos(2 * np.pi * hz * t)]
    coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
    return float(np.hypot(*coef))


def test_partitioned_convolver_matches_direct_convolution_any_block_sizes():
    rng = np.random.default_rng(1)
    h = rng.standard_normal(1000)
    x = rng.standard_normal(5000)
    conv = PartitionedConvolver(h, partition=128)
    out, pos = [], 0
    for n in [100, 7, 512, 1000, 512, 2869]:  # any delay settles in the first blocks
        out.append(conv.process(x[pos:pos + n]))
        pos += n
    y = np.concatenate(out)
    ref = np.convolve(x, h)[:len(x)]
    # Blocks that are not multiples of the partition add one partition of delay.
    delay = conv.latency
    assert 0 <= delay < 128
    np.testing.assert_allclose(y[delay:], ref[:len(x) - delay], atol=1e-9)


def test_zero_latency_for_partition_multiples():
    h = np.r_[0.5, np.zeros(300), 0.25]
    conv = PartitionedConvolver(h, partition=256)
    x = np.random.default_rng(2).standard_normal(2048)
    y = np.concatenate([conv.process(x[i:i + 512]) for i in range(0, 2048, 512)])
    assert conv.latency == 0
    np.testing.assert_allclose(y, np.convolve(x, h)[:2048], atol=1e-9)


@pytest.fixture
def engine():
    eng = AudioEngine(sample_rate=FS, block_size=512)
    eng.set_volume(0.5)
    return eng


def _run(engine, hz, blocks):
    engine.start_sine(hz)
    return np.concatenate([engine.process_block(512) for _ in range(blocks)])


def test_left_and_right_follow_their_own_filters(engine):
    f = np.geomspace(20, 20000, 300)
    fir_l = design_channel(f, -6 * np.exp(-0.5 * (np.log(f / 3000) / .3) ** 2), FS)
    fir_r = design_channel(f, +4 * np.exp(-0.5 * (np.log(f / 3000) / .3) ** 2) - 2, FS)
    engine.set_channel_mode(ChannelMode.BOTH)
    engine.set_channel_firs({"L": fir_l, "R": fir_r})
    for hz in (300, 3000, 9000):
        out = _run(engine, hz, 60)[-8192:]
        engine.set_channel_firs(None)
        ref = _run(engine, hz, 60)[-8192:]
        engine.set_channel_firs({"L": fir_l, "R": fir_r})
        for ch, fir in ((0, fir_l), (1, fir_r)):
            ratio = _tone_amp(out[:, ch], hz) / _tone_amp(ref[:, ch], hz)
            assert ratio == pytest.approx(_gain_at(fir, hz), rel=0.02)


def test_none_is_bypass_and_mono_fir_path_untouched(engine):
    engine.set_channel_firs(None)
    a = _run(engine, 1000, 20)
    fresh = AudioEngine(sample_rate=FS, block_size=512)
    fresh.set_volume(0.5)
    b = _run(fresh, 1000, 20)
    np.testing.assert_array_equal(a, b)
    assert engine._geq_fir is None


def test_switching_is_click_free(engine):
    engine.set_channel_mode(ChannelMode.BOTH)
    engine.start_sine(200)
    blocks = [engine.process_block(512) for _ in range(20)]
    engine.set_channel_firs({"L": gain_fir(-12), "R": gain_fir(-12)})
    blocks += [engine.process_block(512) for _ in range(20)]
    engine.set_channel_firs(None)
    blocks += [engine.process_block(512) for _ in range(20)]
    y = np.concatenate(blocks)[:, 0]
    # A 200 Hz sine at amplitude <= 0.5 changes by at most 2*pi*200/48000*0.5 per sample.
    max_step = 2 * np.pi * 200 / FS * 0.5
    assert np.max(np.abs(np.diff(y))) <= max_step * 1.05


def test_cpu_per_block_under_budget():
    f = np.geomspace(20, 20000, 300)
    fir = design_channel(f, 3 * np.sin(np.log(f)), FS)  # 8193 taps at 48 kHz
    stereo = StereoFIR(partition=256)
    stereo.set_firs({"L": fir, "R": fir[::-1].copy()})
    block = np.random.default_rng(3).standard_normal((512, 2)).astype(np.float32)
    stereo.process(block)  # picks up and primes the filters
    start = time.perf_counter()
    for _ in range(50):
        stereo.process(block)
    per_block = (time.perf_counter() - start) / 50
    assert per_block < 0.25 * 512 / FS  # under a quarter of the block's real-time budget


def test_channel_firs_from_channel_eq():
    f = np.geomspace(20, 20000, 384)
    eq = match_channels(Measurements.bundled(), {"L": 7600, "R": 8100}, f, -2 * np.log10(f / 1000), "D")
    firs = channel_firs(eq, FS)
    assert set(firs) == {"L", "R"}
    ch = eq.channels["L"]
    k = np.argmin(abs(ch.freqs - 1000))
    want = 10 ** ((ch.correction_db[k] + eq.preamp_db) / 20)
    assert _gain_at(firs["L"], ch.freqs[k]) == pytest.approx(want, rel=0.03)
    bypass = channel_firs(eq, FS, enabled=False)
    assert _gain_at(bypass["L"], 1000) == pytest.approx(10 ** (eq.preamp_db / 20), rel=1e-9)
    single = channel_firs(type(eq)({"M": ch}, "D", eq.preamp_db), FS)
    assert set(single) == {"L", "R"} and single["L"] is single["R"]


def test_bypass_is_exact_for_any_block_size():
    stereo = StereoFIR(partition=256)
    block = np.random.default_rng(4).standard_normal((1000, 2)).astype(np.float32)
    np.testing.assert_array_equal(stereo.process(block), block)
