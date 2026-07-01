"""Score how well two measurements' treble resonances align, and detect a
lost seal from sub-bass roll-off.

Insertion depth shifts an IEM's treble resonance features (a peak/dip cluster
around 8–13 kHz) in frequency. When matching Left to Right, the pair whose sharp
peaks and dips land at the same frequencies is the truest match — so we score by
frequency alignment (gain is irrelevant). Separately, a broken seal rolls off the
sub-bass; we detect that as a roll-off *shape* so a genuine L/R level difference
doesn't false-positive.

Qt-free and pure so it can be unit-tested in isolation.
"""

from __future__ import annotations

import math
from typing import Dict, List, Set, Tuple

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

# One feature: (frequency Hz, prominence dB, sign) where sign +1 = peak, -1 = dip.
Feature = Tuple[float, float, int]

_F_MIN, _F_MAX = 20.0, 20000.0
_LOG2 = math.log10(2.0)


def detect_features(
    freqs: np.ndarray,
    db: np.ndarray,
    *,
    lo: float = 6000.0,
    hi: float = 14000.0,
    smooth_oct: float = 1.0 / 12.0,
    prominence_db: float = 1.2,
    n_grid: int = 4000,
) -> List[Feature]:
    """Sharp peaks and dips of a measurement within ``[lo, hi]``.

    Resamples onto a log-frequency grid, applies ~``smooth_oct`` smoothing, and
    finds peaks (sign +1) and dips (sign −1) with prominence ≥ ``prominence_db``.
    """
    freqs = np.asarray(freqs, dtype=float)
    db = np.asarray(db, dtype=float)
    grid = np.geomspace(_F_MIN, _F_MAX, n_grid)
    order = np.argsort(freqs)
    y = np.interp(np.log10(grid), np.log10(np.maximum(freqs[order], 1e-9)), db[order])
    ppo = n_grid / math.log2(_F_MAX / _F_MIN)
    ys = uniform_filter1d(y, size=max(3, int(ppo * smooth_oct)))

    out: List[Feature] = []
    for sign in (1, -1):
        idx, props = find_peaks(sign * ys, prominence=prominence_db)
        for i, prom in zip(idx, props["prominences"]):
            f = float(grid[i])
            if lo <= f <= hi:
                out.append((f, float(prom), sign))
    return out


def alignment_score(
    feats_a: List[Feature],
    feats_b: List[Feature],
    *,
    sigma_oct: float = 0.06,
    max_tol_oct: float = 0.5,
) -> float:
    """Frequency-alignment score in ``[0, 1]`` between two feature sets.

    Same-type features are matched greedily by nearest frequency within
    ``max_tol_oct`` octaves; **unmatched features are excluded**. Each match
    contributes ``exp(−(Δ_oct/σ)²)``, aggregated as a prominence-weighted mean
    (weight ``sqrt(prom_a·prom_b)``). Gain never enters. ``0.0`` when nothing
    matches.
    """
    b_used = [False] * len(feats_b)
    weights: List[float] = []
    scores: List[float] = []
    for fa, pa, sa in feats_a:
        best_j = -1
        best_d = max_tol_oct
        for j, (fb, pb, sb) in enumerate(feats_b):
            if b_used[j] or sb != sa:
                continue
            d = abs(math.log10(fa / fb)) / _LOG2
            if d <= best_d:
                best_d = d
                best_j = j
        if best_j < 0:
            continue
        b_used[best_j] = True
        pb = feats_b[best_j][1]
        weights.append(math.sqrt(max(pa, 0.0) * max(pb, 0.0)))
        scores.append(math.exp(-((best_d / sigma_oct) ** 2)))
    if not weights or sum(weights) <= 0:
        return 0.0
    w = np.asarray(weights)
    s = np.asarray(scores)
    return float(np.sum(w * s) / np.sum(w))


def sub_bass_metric(
    freqs: np.ndarray,
    db: np.ndarray,
    *,
    sub: Tuple[float, float] = (20.0, 45.0),
    mid: Tuple[float, float] = (150.0, 400.0),
) -> float:
    """Sub-bass roll-off shape: median level over ``sub`` minus median over
    ``mid``. Near 0 for a sealed IEM; strongly negative when the seal is lost.
    Self-normalizing, so a genuine L/R *level* offset doesn't affect it."""
    freqs = np.asarray(freqs, dtype=float)
    db = np.asarray(db, dtype=float)

    def _median(band: Tuple[float, float]) -> float:
        m = (freqs >= band[0]) & (freqs <= band[1])
        return float(np.median(db[m])) if np.any(m) else float("nan")

    return _median(sub) - _median(mid)


def detect_seal_loss(
    metrics: Dict, *, threshold_db: float = 6.0
) -> Set:
    """Keys whose :func:`sub_bass_metric` is ≥ ``threshold_db`` below the best
    (best-sealed) measurement's — i.e. a uniquely rolled-off measurement. A
    uniform roll-off across all measurements flags nothing."""
    valid = {k: v for k, v in metrics.items() if not math.isnan(v)}
    if len(valid) < 2:
        return set()
    best = max(valid.values())
    return {k for k, v in valid.items() if best - v >= threshold_db}
