import numpy as np

from eq_helper.dsp.balance_smoothing import (
    detect_low_frequency_noise_start,
    smooth_low_frequency_noise,
)


def _measurement(seed: int, noisy_below: float = 700.0):
    rng = np.random.default_rng(seed)
    f = np.geomspace(20.0, 20000.0, 3000)
    lf = np.log10(f)
    real = (
        82.0
        + 4.0 * np.exp(-((lf - np.log10(90.0)) ** 2) / (2 * 0.22**2))
        - 3.0 * np.exp(-((lf - np.log10(250.0)) ** 2) / (2 * 0.14**2))
    )
    jagged = rng.normal(0.0, 1.0, len(f))
    jagged = jagged - np.convolve(jagged, np.ones(31) / 31.0, mode="same")
    amp = np.clip((noisy_below - f) / noisy_below, 0.0, 1.0) * 1.8
    return f, real + amp * jagged


def test_detect_low_frequency_noise_start_from_repeat_measurements():
    measurements = [_measurement(i) for i in range(5)]

    cutoff = detect_low_frequency_noise_start(measurements)

    assert 450.0 <= cutoff <= 800.0


def test_smooth_low_frequency_noise_only_changes_at_or_below_cutoff():
    f, db = _measurement(12, noisy_below=800.0)
    out = smooth_low_frequency_noise(f, db, 700.0)

    assert np.allclose(out[f > 700.0], db[f > 700.0])
    assert np.std(np.diff(out[f <= 700.0])) < np.std(np.diff(db[f <= 700.0])) * 0.4
