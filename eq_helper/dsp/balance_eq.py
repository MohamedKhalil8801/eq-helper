"""Balance EQ: make one earpiece's response identical to the other.

A balance correction is a *relative* measurement: the EQ applied to the
non-reference side is ``reference(f) - other(f)``. Because both sides are
measured with the same mic in the same coupler, the mic/coupler coloration is
common to both captures and cancels in the subtraction — so no calibrated mic is
needed.

We reuse the exact-fit engine from :mod:`eq_helper.dsp.eq_optimize`: the target
*is* the measured difference at every frequency (no relaxed target), fitted to a
GraphicEQ and verified against APO's min-phase-FIR realization — so every
measured peak and dip is chased, not approximated.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from eq_helper.dsp.eq_optimize import (
    ChannelEQ,
    fit_graphic_eq,
    realized_response_db,
)
from eq_helper.dsp.graphic_eq import GraphicEQ

_F_MIN, _F_MAX = 20.0, 20000.0


def _interp_db_loglinear(freqs: np.ndarray, db: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Interpolate a measured response onto ``grid`` (linear dB over log-freq)."""
    order = np.argsort(freqs)
    lf = np.log10(np.maximum(freqs[order], 1e-9))
    lg = np.log10(np.maximum(grid, 1e-9))
    return np.interp(lg, lf, np.asarray(db, dtype=float)[order])


def _common_grid(ref_f: np.ndarray, oth_f: np.ndarray, n: int) -> np.ndarray:
    """A dense log grid over the frequency range covered by BOTH measurements,
    unioned with each file's native points (in range) so no measured feature is
    smoothed away. Raises ``ValueError`` if the ranges don't overlap."""
    lo = max(float(np.min(ref_f)), float(np.min(oth_f)), _F_MIN)
    hi = min(float(np.max(ref_f)), float(np.max(oth_f)), _F_MAX)
    if hi <= lo:
        raise ValueError(
            "the two measurements do not overlap in frequency "
            f"(reference {np.min(ref_f):.0f}-{np.max(ref_f):.0f} Hz, "
            f"other {np.min(oth_f):.0f}-{np.max(oth_f):.0f} Hz)"
        )
    base = np.geomspace(lo, hi, n)
    native = np.concatenate([np.asarray(ref_f, float), np.asarray(oth_f, float)])
    native = native[(native >= lo) & (native <= hi)]
    return np.unique(np.concatenate([base, native]))


def balance_correction(
    ref_f: np.ndarray,
    ref_db: np.ndarray,
    oth_f: np.ndarray,
    oth_db: np.ndarray,
    *,
    match_level: bool = True,
    n: int = 8000,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(grid, correction_db)`` where ``correction = reference - other``.

    Both measurements are interpolated onto a common dense grid over their
    overlapping range. ``match_level=True`` (default) keeps the overall level
    offset so the corrected side matches the reference in both level and shape;
    ``False`` subtracts the mean, correcting only the frequency-response shape.
    """
    ref_f = np.asarray(ref_f, dtype=float)
    oth_f = np.asarray(oth_f, dtype=float)
    grid = _common_grid(ref_f, oth_f, n)
    r = _interp_db_loglinear(ref_f, ref_db, grid)
    o = _interp_db_loglinear(oth_f, oth_db, grid)
    corr = r - o
    if not match_level:
        corr = corr - float(np.mean(corr))
    return grid, corr


def generate_balance_eq(
    ref_f: np.ndarray,
    ref_db: np.ndarray,
    oth_f: np.ndarray,
    oth_db: np.ndarray,
    other_channel: str,
    sample_rate: int,
    *,
    match_level: bool = True,
    tol_db: float = 0.02,
) -> Tuple[ChannelEQ, float]:
    """Build a ``Channel:``-scoped GraphicEQ that makes ``other_channel`` match
    the reference, plus the measured max deviation of APO's exact min-phase-FIR
    realization from the intended correction.
    """
    grid, corr = balance_correction(
        ref_f, ref_db, oth_f, oth_db, match_level=match_level
    )
    bands = fit_graphic_eq(grid, corr, tol_db=tol_db)
    geq = GraphicEQ(name=f"balance {other_channel}", bands=bands)
    realized = realized_response_db(bands, grid, sample_rate)
    max_dev = float(np.max(np.abs(realized - corr)))
    return ChannelEQ(channels=(other_channel,), geq=geq), max_dev
