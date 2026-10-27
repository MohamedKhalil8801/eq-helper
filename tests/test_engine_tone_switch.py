"""switch_sine: tones crossfade (no click, no gap); the plain sine path is unchanged."""

import numpy as np
import pytest

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import Mode

FS = 48000
AMP = 0.5


@pytest.fixture
def engine():
    eng = AudioEngine(sample_rate=FS, block_size=480)
    eng.set_volume(AMP)
    return eng


def run(engine, seconds, block=480):
    return np.concatenate([engine.process_block(block)[:, 0] for _ in range(int(seconds * FS / block))])


def click_energy(x):
    """Share of the energy above 14 kHz."""
    spectrum = np.abs(np.fft.rfft(x * np.blackman(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / FS)
    return float(spectrum[freqs > 14000].sum() / spectrum.sum())


def max_step(x):
    return float(np.max(np.abs(np.diff(x))))


def test_switching_tones_has_no_step_beyond_a_smooth_fade(engine):
    engine.switch_sine(6500)
    out = [run(engine, 0.1)]
    for hz in (8800, 6200, 9000, 7000):
        engine.switch_sine(hz)
        out.append(run(engine, 0.06, block=333))  # odd block size: fades span blocks
    x = np.concatenate(out)
    # A click is broadband: the tones are 6–9 kHz, so energy far above them can only come from the
    # switches. A hard switch to a fresh tone puts orders of magnitude more there.
    assert click_energy(x) < 1e-7
    hard = np.concatenate([np.sin(2 * np.pi * 6500 * np.arange(4800) / FS),
                           np.cos(2 * np.pi * 8800 * np.arange(4800) / FS)]) * AMP
    assert click_energy(hard) > 1e-4
    # no gap: the level never drops much during a crossfade (equal-power, -3 dB at worst)
    rms = [np.sqrt(np.mean(x[i:i + 240] ** 2)) for i in range(4800, len(x) - 240, 240)]
    assert min(rms) > AMP / np.sqrt(2) * 0.6


def test_switch_ends_on_the_new_frequency_only(engine):
    engine.switch_sine(6000)
    run(engine, 0.05)
    engine.switch_sine(8000)
    x = run(engine, 0.2)[-4800:]
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / FS)
    assert freqs[np.argmax(spectrum)] == pytest.approx(8000, abs=15)
    assert engine.state.snapshot().freq == 8000 and engine.state.snapshot().mode == Mode.SINE


def test_first_switch_fades_in_from_silence(engine):
    engine.switch_sine(7000)
    x = engine.process_block(48)[:, 0]
    assert abs(x[0]) < 0.02 and max_step(x) < AMP * 2 * np.pi * 7000 / FS * 1.05


def test_stop_then_plain_sine_works_as_before(engine):
    engine.switch_sine(7000)
    run(engine, 0.05)
    engine.stop()
    assert np.all(engine.process_block(480) == 0)
    engine.start_sine(1000)
    x = run(engine, 0.05)
    assert np.max(np.abs(x)) == pytest.approx(AMP, rel=0.01)
    engine.set_freq(2000)
    assert engine._voices == []


def test_set_freq_retunes_a_crossfaded_tone(engine):
    engine.switch_sine(6000)
    run(engine, 0.05)
    engine.set_freq(8500)
    x = run(engine, 0.1)[-2400:]
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    assert np.fft.rfftfreq(len(x), 1 / FS)[np.argmax(spectrum)] == pytest.approx(8500, abs=25)


def band_share(x, lo, hi):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / FS)
    return float(spectrum[(freqs >= lo) & (freqs <= hi)].sum() / spectrum.sum())


@pytest.mark.parametrize("width", [1 / 12, 1 / 6, 1 / 3])
@pytest.mark.parametrize("center", [6200.0, 7500.0, 8800.0, 15000.0])
def test_bands_have_equal_level_and_stay_in_their_band(engine, center, width):
    engine.switch_band(center, width)
    run(engine, 0.1)
    x = run(engine, 1.0)
    rms = float(np.sqrt(np.mean(x ** 2)))
    assert rms == pytest.approx(0.5 * AMP, rel=0.08)  # BAND_NOISE_RMS × volume, whatever the band
    half = 2 ** (width / 2)
    assert band_share(x, center / half / 1.15, center * half * 1.15) > 0.9


def test_switching_bands_crossfades_without_a_gap_or_click(engine):
    engine.switch_band(6500, 1 / 6)
    out = [run(engine, 0.2)]
    for hz in (8800, 7000, 9000):
        engine.switch_band(hz, 1 / 6)
        out.append(run(engine, 0.1, block=333))
    x = np.concatenate(out)
    rms = [np.sqrt(np.mean(x[i:i + 480] ** 2)) for i in range(9600, len(x) - 480, 480)]
    assert min(rms) > 0.5 * AMP * 0.45  # no gap (noise wobbles; equal-power fade keeps the level)
    assert click_energy(x) < 1e-5


def test_bands_and_plain_tones_keep_eq_helper_behaviour(engine):
    engine.switch_band(7000, 1 / 6)
    run(engine, 0.05)
    engine.stop()
    assert np.all(engine.process_block(480) == 0)
    engine.start_sine(1000)
    x = run(engine, 0.05)
    assert np.max(np.abs(x)) == pytest.approx(AMP, rel=0.01)
