"""Low-frequency measurement-noise detection and smoothing for balance EQ.

IEC711-style coupler measurements often show jagged repeat-to-repeat artifacts
in the bass. The detector looks for that as *repeat inconsistency* after
removing each capture's broad trend, then smooths only the detected low band.
"""

from __future__ import annotations

import math
from typing import Iterable, Tuple

import numpy as np
from scipy.ndimage import uniform_filter1d

Measurement = Tuple[np.ndarray, np.ndarray]

_F_MIN, _F_MAX = 20.0, 20000.0


def _points_for_octave(freqs: np.ndarray, octaves: float) -> int:
    if len(freqs) < 3:
        return 3
    span = math.log2(float(freqs[-1]) / float(freqs[0]))
    if span <= 0:
        return 3
    ppo = len(freqs) / span
    size = max(3, int(round(ppo * octaves)))
    return size + 1 if size % 2 == 0 else size


def _interp_log(freqs: np.ndarray, db: np.ndarray, grid: np.ndarray) -> np.ndarray:
    order = np.argsort(freqs)
    return np.interp(
        np.log10(grid),
        np.log10(np.maximum(np.asarray(freqs, float)[order], 1e-9)),
        np.asarray(db, float)[order],
    )


def detect_low_frequency_noise_start(
    measurements: Iterable[Measurement],
    *,
    max_freq: float = 1200.0,
    baseline_band: Tuple[float, float] = (1500.0, 5000.0),
    n_grid: int = 2400,
) -> float:
    """Return the highest frequency where low-frequency measurement noise starts.

    ``0.0`` means no reliable noisy low-frequency band was detected. The
    calculation uses only repeat variation within the supplied measurements, so
    left/right bass differences are not treated as seal or noise by themselves.
    """
    ms = [(np.asarray(f, float), np.asarray(db, float)) for f, db in measurements]
    ms = [(f, db) for f, db in ms if len(f) >= 8 and len(db) == len(f)]
    if len(ms) < 2:
        return 0.0

    lo = max(_F_MIN, max(float(np.min(f)) for f, _db in ms))
    hi = min(_F_MAX, min(float(np.max(f)) for f, _db in ms))
    if hi <= max(lo, max_freq):
        return 0.0

    grid = np.geomspace(lo, hi, n_grid)
    rows = []
    for f, db in ms:
        y = _interp_log(f, db, grid)
        norm_band = (grid >= 1500.0) & (grid <= 3000.0)
        if np.any(norm_band):
            y = y - float(np.median(y[norm_band]))
        trend = uniform_filter1d(y, size=_points_for_octave(grid, 1.0), mode="nearest")
        rows.append(y - trend)
    residuals = np.vstack(rows)

    med = np.median(residuals, axis=0)
    mad = np.median(np.abs(residuals - med), axis=0) * 1.4826
    noise = uniform_filter1d(mad, size=_points_for_octave(grid, 1.0 / 6.0), mode="nearest")

    base_mask = (grid >= baseline_band[0]) & (grid <= baseline_band[1])
    baseline = float(np.median(noise[base_mask])) if np.any(base_mask) else 0.0
    threshold = max(0.16, baseline * 5.0)

    low = (grid >= 35.0) & (grid <= max_freq)
    idx = np.where(low & (noise >= threshold))[0]
    if len(idx) == 0:
        return 0.0
    return float(grid[idx[-1]])


def smooth_low_frequency_noise(
    freqs: np.ndarray,
    db: np.ndarray,
    cutoff_hz: float,
    *,
    smooth_oct: float = 1.0,
) -> np.ndarray:
    """Apply octave smoothing at and below ``cutoff_hz``; leave higher bins raw."""
    freqs = np.asarray(freqs, dtype=float)
    db = np.asarray(db, dtype=float)
    if cutoff_hz <= 0 or len(freqs) < 3:
        return db.copy()

    order = np.argsort(freqs)
    sorted_f = freqs[order]
    sorted_db = db[order]

    # Create a dense uniform log grid for smoothing
    lo = max(10.0, float(sorted_f[0]))
    hi = float(sorted_f[-1])
    if lo >= hi:
        return db.copy()

    span_oct = math.log2(hi / lo)
    n_grid = max(100, int(round(span_oct * 200)))
    grid = np.geomspace(lo, hi, n_grid)
    
    # Interpolate to log grid
    grid_db = np.interp(np.log10(grid), np.log10(np.maximum(sorted_f, 1e-9)), sorted_db)
    
    # Apply smoothing on the uniform log grid
    size = max(3, int(round(200 * smooth_oct)))
    if size % 2 == 0:
        size += 1
        
    smoothed_grid_db = uniform_filter1d(grid_db, size=size, mode="nearest")
    
    # Interpolate back to original frequencies
    smoothed_db = np.interp(np.log10(np.maximum(sorted_f, 1e-9)), np.log10(grid), smoothed_grid_db)

    # Crossfade over 1/3 octave below the cutoff to avoid vertical steps
    fade_start = cutoff_hz / (2 ** (1/3))
    fade_end = cutoff_hz
    
    w = np.clip((np.log10(sorted_f) - np.log10(fade_end)) / (np.log10(fade_start) - np.log10(fade_end)), 0.0, 1.0)
    
    out_sorted = w * smoothed_db + (1.0 - w) * sorted_db
    
    out = np.empty_like(out_sorted)
    out[order] = out_sorted
    return out
