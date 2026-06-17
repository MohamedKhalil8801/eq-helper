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

import re
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from eq_helper.dsp.eq_optimize import (
    ChannelEQ,
    fit_graphic_eq,
    realized_response_db,
)
from eq_helper.dsp.graphic_eq import GraphicEQ
from eq_helper.dsp.overlay import load_frequency_response

_F_MIN, _F_MAX = 20.0, 20000.0

# A measurement filename's side+index: a leading L/R/left/right, optional
# separator, optional trailing number — and nothing else (so "reference.txt"
# isn't mistaken for a Right measurement).
_SIDE_RE = re.compile(r"^(left|right|l|r)[\s_\-]*(\d+)?\s*$", re.IGNORECASE)


def parse_side_index(filename: str) -> Optional[Tuple[str, Optional[int]]]:
    """Parse a measurement filename into ``(side, index)`` — side ``"L"``/``"R"``,
    index an int or ``None``. Returns ``None`` if the stem isn't a side label."""
    m = _SIDE_RE.match(Path(filename).stem.strip())
    if not m:
        return None
    side = "L" if m.group(1).lower() in ("l", "left") else "R"
    idx = int(m.group(2)) if m.group(2) else None
    return side, idx


def detect_measurement_sets(
    folder,
) -> Tuple[List[Tuple[Optional[int], Path]], List[Tuple[Optional[int], Path]]]:
    """Scan ``folder`` for ``.txt``/``.csv`` measurement files and group them
    into ``(left, right)`` lists of ``(index, path)``, each sorted by index
    (indexless entries last, then by name)."""
    folder = Path(folder)
    left: List[Tuple[Optional[int], Path]] = []
    right: List[Tuple[Optional[int], Path]] = []
    if not folder.is_dir():
        return left, right
    for p in sorted(folder.iterdir(), key=lambda q: q.name.lower()):
        if not p.is_file() or p.suffix.lower() not in (".txt", ".csv"):
            continue
        si = parse_side_index(p.name)
        if si is None:
            continue
        side, idx = si
        (left if side == "L" else right).append((idx, p))

    def _key(t: Tuple[Optional[int], Path]):
        return (t[0] is None, t[0] if t[0] is not None else 0, t[1].name.lower())

    left.sort(key=_key)
    right.sort(key=_key)
    return left, right


def effective_other_channel(reference: str, invert: bool = False) -> str:
    """The channel a balance EQ should target. ``other`` is the side opposite the
    reference; ``invert`` flips it (for a config that swaps L/R via ``Copy:``)."""
    other = "R" if reference.upper() == "L" else "L"
    if invert:
        other = "L" if other == "R" else "R"
    return other


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
    fit_grid, fit_corr = balance_correction(
        ref_f, ref_db, oth_f, oth_db, match_level=match_level, n=8000
    )
    bands = fit_graphic_eq(fit_grid, fit_corr, tol_db=tol_db)
    geq = GraphicEQ(name=f"balance {other_channel}", bands=bands)
    # Verify on an INDEPENDENT, denser grid (recomputing the correction there) so
    # the reported deviation can't under-report realization error between nodes.
    ver_grid, ver_corr = balance_correction(
        ref_f, ref_db, oth_f, oth_db, match_level=match_level, n=40000
    )
    realized = realized_response_db(bands, ver_grid, sample_rate)
    max_dev = float(np.max(np.abs(realized - ver_corr)))
    return ChannelEQ(channels=(other_channel,), geq=geq), max_dev


def generate_balance_batch(
    left: List[Tuple[Optional[int], Path]],
    right: List[Tuple[Optional[int], Path]],
    reference: str,
    sample_rate: int,
    *,
    invert: bool = False,
    match_level: bool = True,
    tol_db: float = 0.02,
) -> List[Tuple[str, ChannelEQ, float]]:
    """Generate all reference×other balance EQs.

    ``left``/``right`` are ``(index, path)`` lists (from
    :func:`detect_measurement_sets`). The reference side is fixed; every
    reference measurement is paired with every other-side measurement. Returns
    ``[(name, ChannelEQ, max_dev)]`` named ``bal <Ref><i>-<Oth><j>`` by the
    physical sides/indices (the EQ's channel is the effective, possibly
    inverted, one). Each measurement file is loaded once. Raises ``ValueError``
    if either side has no measurements."""
    ref_side = reference.upper()
    oth_side = "R" if ref_side == "L" else "L"
    ref_set = left if ref_side == "L" else right
    oth_set = right if ref_side == "L" else left
    if not ref_set or not oth_set:
        raise ValueError("need at least one Left and one Right measurement")

    other_channel = effective_other_channel(reference, invert)
    cache: dict = {}

    def _load(path: Path):
        key = str(path)
        if key not in cache:
            cache[key] = load_frequency_response(path)
        return cache[key]

    results: List[Tuple[str, ChannelEQ, float]] = []
    for ri, (ridx, rpath) in enumerate(ref_set):
        ref_f, ref_db = _load(rpath)
        for oj, (oidx, opath) in enumerate(oth_set):
            oth_f, oth_db = _load(opath)
            ceq, max_dev = generate_balance_eq(
                ref_f, ref_db, oth_f, oth_db, other_channel, sample_rate,
                match_level=match_level, tol_db=tol_db,
            )
            ri_lbl = ridx if ridx is not None else ri + 1
            oj_lbl = oidx if oidx is not None else oj + 1
            name = f"bal {ref_side}{ri_lbl}-{oth_side}{oj_lbl}"
            results.append((name, ceq, max_dev))
    return results
