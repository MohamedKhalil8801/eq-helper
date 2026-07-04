"""Low-frequency measurement-noise smoothing for balance EQ.

IEC711-style coupler measurements often show jagged repeat-to-repeat artifacts
in the bass.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import uniform_filter1d


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
