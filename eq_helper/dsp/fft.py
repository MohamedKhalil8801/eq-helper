"""Windowed rFFT -> dB magnitude for the live spectrum view."""

from __future__ import annotations

from typing import Tuple

import numpy as np

_EPSILON = 1e-12


def compute_spectrum(
    samples: np.ndarray,
    sample_rate: int,
    *,
    fmin: float = 20.0,
    fmax: float = 20000.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(freqs_hz, magnitude_db)`` for the given mono block.

    Applies a Hann window, computes the real FFT, converts magnitude to dBFS
    assuming a unit-amplitude sine would reach 0 dB, and clips the result to
    the audible range ``[fmin, fmax]``.
    """
    if samples.ndim != 1:
        raise ValueError("samples must be 1-D mono")

    n = samples.shape[0]
    if n < 4:
        return np.array([]), np.array([])

    window = np.hanning(n)
    windowed = samples.astype(np.float64) * window

    spectrum = np.fft.rfft(windowed)
    # Scale: the Hann window halves the coherent gain, so divide by n/2.
    magnitude = np.abs(spectrum) / (n / 2.0) * 2.0
    magnitude_db = 20.0 * np.log10(magnitude + _EPSILON)

    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    mask = (freqs >= fmin) & (freqs <= fmax)
    return freqs[mask], magnitude_db[mask]


def dominant_frequency(samples: np.ndarray, sample_rate: int) -> float:
    """Return the frequency of the largest FFT bin (parabolic-interpolated)."""
    if samples.ndim != 1 or samples.shape[0] < 4:
        return 0.0
    window = np.hanning(samples.shape[0])
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64) * window))
    if spectrum.size == 0:
        return 0.0
    peak = int(np.argmax(spectrum))
    if 0 < peak < spectrum.size - 1:
        # Parabolic interpolation around the peak for sub-bin accuracy.
        alpha = spectrum[peak - 1]
        beta = spectrum[peak]
        gamma = spectrum[peak + 1]
        denom = alpha - 2.0 * beta + gamma
        offset = 0.5 * (alpha - gamma) / denom if denom != 0 else 0.0
    else:
        offset = 0.0
    freqs = np.fft.rfftfreq(samples.shape[0], d=1.0 / sample_rate)
    if peak >= freqs.size:
        return 0.0
    bin_width = freqs[1] - freqs[0] if freqs.size > 1 else 0.0
    return float(freqs[peak] + offset * bin_width)
