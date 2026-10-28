"""Grey noise: it plays, at about pink noise's level, and at the output's sample rate."""

import numpy as np
import pytest
from scipy.signal import welch

from eq_helper.audio.engine import AudioEngine, region_gain
from eq_helper.audio.shared_state import Mode


def _record(engine, frames=48000 * 4, block=4800):
    return np.concatenate([engine.process_block(block)[:, 0] for _ in range(frames // block)])


def _band_db(x, rate, lo, hi):
    f, s = welch(x, rate, nperseg=8192)
    return 10 * np.log10(np.sum(s[(f >= lo) & (f < hi)]))


def test_grey_noise_plays_at_about_pink_noises_level():
    engine = AudioEngine(sample_rate=48000)
    assert engine.has_grey
    engine.set_volume(0.05)
    engine.start_pink()
    pink = _record(engine)
    engine.start_grey()
    assert engine.state.snapshot().mode == Mode.GREY
    grey = _record(engine)
    assert abs(_band_db(grey, 48000, 1000, 4000) - _band_db(pink, 48000, 1000, 4000)) < 2.0
    assert _band_db(grey, 48000, 8000, 16000) > _band_db(pink, 48000, 8000, 16000) + 2  # treble lifted
    assert np.abs(grey).max() < 3 * np.abs(pink).max()  # deep bass taken out: no huge peaks


def test_grey_noise_follows_the_output_rate():
    engine = AudioEngine(sample_rate=44100)
    engine.set_volume(0.05)
    engine.start_grey()
    assert engine._grey_buf_rate == 44100
    assert engine._grey_buf.shape[0] == round(engine._grey_source.shape[0] * 44100 / engine._grey_source_rate)
    assert np.abs(_record(engine, 44100, 4410)).max() > 0


def test_focused_grey_noise_keeps_only_the_region_at_the_same_level():
    engine = AudioEngine(sample_rate=48000)
    engine.set_volume(0.05)
    engine.start_grey()
    full = _record(engine)
    engine.start_grey(region=(7500.0, 1.5))
    assert engine.state.snapshot().mode == Mode.GREY
    focused = _record(engine)
    assert abs(_band_db(focused, 48000, 6000, 9000) - _band_db(full, 48000, 6000, 9000)) < 0.5
    assert _band_db(focused, 48000, 500, 2000) < _band_db(full, 48000, 500, 2000) - 30
    assert engine._grey_buf.shape == engine._grey_full.shape  # still loops seamlessly
    engine.start_grey()
    assert engine._grey_buf is engine._grey_full


def test_region_gain_fades_gently_at_the_edges():
    f = np.array([7500.0, 7500 * 2 ** 0.75, 7500 * 2 ** 1.0, 7500 * 2 ** 1.25, 7500 * 2 ** 1.5])
    g = region_gain(f, 7500.0, 1.5)
    assert g == pytest.approx([1.0, 1.0, 0.5, 0.0, 0.0], abs=1e-9)  # half way through the fade: half
