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
from scipy.optimize import linear_sum_assignment
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

    # Pad the acceptance window slightly (~0.15 oct) so a resonance present on
    # both sides but shifted just past the edge on one side (insertion depth is
    # exactly what we measure) is still detected and matched rather than being
    # silently treated as one-sided.
    pad = 2.0 ** 0.15
    keep_lo, keep_hi = lo / pad, hi * pad
    out: List[Feature] = []
    for sign in (1, -1):
        idx, props = find_peaks(sign * ys, prominence=prominence_db)
        for i, prom in zip(idx, props["prominences"]):
            f = float(grid[i])
            if keep_lo <= f <= keep_hi:
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

    Same-type features are matched by **optimal** (minimum total frequency
    deviation) 1-to-1 assignment; matches beyond ``max_tol_oct`` octaves are
    dropped, so **unmatched features are excluded**. Each kept match contributes
    ``exp(−(Δ_oct/σ)²)``, aggregated as a prominence-weighted mean (weight
    ``sqrt(prom_a·prom_b)``). The optimal assignment makes the score symmetric
    and order-independent. Gain never enters. ``0.0`` when nothing matches.
    """
    weights: List[float] = []
    scores: List[float] = []
    for sign in (1, -1):
        a = [(f, p) for f, p, s in feats_a if s == sign]
        b = [(f, p) for f, p, s in feats_b if s == sign]
        if not a or not b:
            continue
        dev = np.array(
            [[abs(math.log10(fa / fb)) / _LOG2 for fb, _ in b] for fa, _ in a]
        )
        per = np.exp(-((dev / sigma_oct) ** 2))  # per-pair alignment score
        # Maximize the total alignment score (minimize -per), so a perfect
        # shared-resonance match is preferred over two mediocre ones.
        for i, j in zip(*linear_sum_assignment(-per)):
            if float(dev[i, j]) > max_tol_oct:
                continue  # not the same feature — excluded
            weights.append(math.sqrt(max(a[i][1], 0.0) * max(b[j][1], 0.0)))
            scores.append(float(per[i, j]))
    if not weights or sum(weights) <= 0:
        return 0.0
    w = np.asarray(weights)
    s = np.asarray(scores)
    return float(np.sum(w * s) / np.sum(w))


def alignment_score_from_measurements(
    ref_freqs: np.ndarray,
    ref_db: np.ndarray,
    mic_freqs: np.ndarray,
    mic_db: np.ndarray,
    **kwargs,
) -> float:
    """Compute the same 0–1 alignment score used by balance generation."""
    ref_feats = detect_features(ref_freqs, ref_db)
    mic_feats = detect_features(mic_freqs, mic_db)
    return alignment_score(ref_feats, mic_feats, **kwargs)


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
