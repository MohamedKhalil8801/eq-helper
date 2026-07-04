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
from eq_helper.dsp.balance_score import (
    alignment_score,
    detect_features,
    detect_seal_loss,
    sub_bass_metric,
)
from eq_helper.dsp.balance_smoothing import smooth_low_frequency_noise
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


def generate_midpoint_eqs(
    l_f: np.ndarray,
    l_db: np.ndarray,
    r_f: np.ndarray,
    r_db: np.ndarray,
    sample_rate: int,
    *,
    invert: bool = False,
    match_level: bool = True,
    tol_db: float = 0.02,
) -> Tuple[List[ChannelEQ], float]:
    """Meet-in-the-middle balance: move BOTH sides to the average ``(L+R)/2``.

    With ``D = L − R``, apply ``−D/2`` to physical L and ``+D/2`` to physical R,
    each as an exact GraphicEQ. Returns ``([ChannelEQ_L, ChannelEQ_R], max_dev)``
    (L block first) plus the worse of the two channels' realization deviations.
    Each side changes half as much as the reference method. ``invert`` swaps
    which APO channel label each physical curve is written under (a config that
    swaps L/R via ``Copy:``)."""
    fit_grid, d = balance_correction(
        l_f, l_db, r_f, r_db, match_level=match_level, n=8000
    )  # d = L - R
    ver_grid, ver_d = balance_correction(
        l_f, l_db, r_f, r_db, match_level=match_level, n=40000
    )
    # Physical corrections: L moves down toward R by half, R moves up by half.
    phys = {"L": -0.5 * d, "R": 0.5 * d}
    phys_ver = {"L": -0.5 * ver_d, "R": 0.5 * ver_d}
    # Map each physical curve to its APO channel label (swapped when inverted).
    channel_for = {"L": "R", "R": "L"} if invert else {"L": "L", "R": "R"}

    eqs: List[ChannelEQ] = []
    max_dev = 0.0
    for phys_side in ("L", "R"):
        chan = channel_for[phys_side]
        bands = fit_graphic_eq(fit_grid, phys[phys_side], tol_db=tol_db)
        realized = realized_response_db(bands, ver_grid, sample_rate)
        max_dev = max(max_dev, float(np.max(np.abs(realized - phys_ver[phys_side]))))
        eqs.append(ChannelEQ(channels=(chan,), geq=GraphicEQ(name=f"midpoint {chan}", bands=bands)))
    # Emit the Channel: L block first (cosmetic; matches the reference example).
    eqs.sort(key=lambda e: e.channels)
    return eqs, max_dev


def _fallback_labels(side_set: List[Tuple[Optional[int], Path]]) -> List[int]:
    """Per-entry display index, **unique within the side**: the parsed index
    when present and not already taken, else the next free integer above the max
    explicit index. Uniqueness matters because labels key the seal-marker/metrics
    map — two files parsing to the same (side, index) must not collide."""
    explicit = [i for i, _ in side_set if i is not None]
    nxt = (max(explicit) + 1) if explicit else 1
    used: set = set()
    labels: List[int] = []
    for i, _ in side_set:
        label = i if (i is not None and i not in used) else None
        if label is None:
            while nxt in used:
                nxt += 1
            label = nxt
            nxt += 1
        used.add(label)
        labels.append(label)
    return labels


def find_best_balance_pair(
    left: List[Tuple[Optional[int], Path]],
    right: List[Tuple[Optional[int], Path]],
    reference: str,
    *,
    invert: bool = False,
    method: str = "reference",
) -> Tuple[
    Optional[Tuple[str, float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    List[Tuple[str, str]]
]:
    """Find the best-scoring Left/Right measurement pair.
    
    Returns ``(best_match, skipped)`` where ``best_match`` is 
    ``(name, score, l_f, l_db, r_f, r_db)`` or None if no valid pairs exist."""
    if not left or not right:
        raise ValueError("need at least one Left and one Right measurement")
    ref_side = reference.upper()
    left_labels = _fallback_labels(left)
    right_labels = _fallback_labels(right)

    loaded: dict = {}
    feats: dict = {}
    metrics: dict = {}

    def _prep(path: Path, side: str, label: int):
        key = str(path)
        if key not in loaded:
            try:
                fd = load_frequency_response(path)
            except (ValueError, OSError):
                fd = None
            loaded[key] = fd
            if fd is not None:
                feats[key] = detect_features(fd[0], fd[1])
                metrics[(side, label)] = sub_bass_metric(fd[0], fd[1])
        return loaded[key]

    for i, (_idx, p) in enumerate(left):
        _prep(p, "L", left_labels[i])
    for j, (_idx, p) in enumerate(right):
        _prep(p, "R", right_labels[j])

    flagged: set = set()
    for side in ("L", "R"):
        side_metrics = {k: v for k, v in metrics.items() if k[0] == side}
        flagged |= detect_seal_loss(side_metrics)

    def _mark(side: str, label: int) -> str:
        return f"{side}{label}{'!!!' if (side, label) in flagged else ''}"

    scored: List[Tuple[bool, float, str, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    skipped: List[Tuple[str, str]] = []
    
    for i, (_l, lpath) in enumerate(left):
        for j, (_r, rpath) in enumerate(right):
            li, rj = left_labels[i], right_labels[j]
            if method == "midpoint":
                base = f"{_mark('L', li)} {_mark('R', rj)}"
            elif ref_side == "L":
                base = f"{_mark('R', rj)} {_mark('L', li)}"
            else:
                base = f"{_mark('L', li)} {_mark('R', rj)}"
                
            l_fd, r_fd = loaded[str(lpath)], loaded[str(rpath)]
            if l_fd is None or r_fd is None:
                skipped.append((base, "unreadable measurement"))
                continue
                
            # Verify overlap
            try:
                _common_grid(l_fd[0], r_fd[0], 100)
            except ValueError as exc:
                skipped.append((base, str(exc)))
                continue
                
            score = alignment_score(feats[str(lpath)], feats[str(rpath)])
            any_seal = (("L", li) in flagged) or (("R", rj) in flagged)
            scored.append((any_seal, score, f"{base} ({score:.2f})", l_fd[0], l_fd[1], r_fd[0], r_fd[1]))

    scored.sort(key=lambda t: (t[0], -t[1], t[2]))
    
    if not scored:
        return None, skipped
        
    _seal, score, name, l_f, l_db, r_f, r_db = scored[0]
    return (name, score, l_f, l_db, r_f, r_db), skipped


def generate_balance_for_pair(
    name: str,
    l_f: np.ndarray,
    l_db: np.ndarray,
    r_f: np.ndarray,
    r_db: np.ndarray,
    reference: str,
    sample_rate: int,
    *,
    invert: bool = False,
    method: str = "reference",
    match_level: bool = True,
    tol_db: float = 0.02,
) -> Tuple[str, List[ChannelEQ], float]:
    """Generate the balance EQ for a specific prepared pair."""
    ref_side = reference.upper()
    if method == "midpoint":
        eqs, max_dev = generate_midpoint_eqs(
            l_f, l_db, r_f, r_db, sample_rate,
            invert=invert, match_level=match_level, tol_db=tol_db,
        )
    else:
        other = effective_other_channel(reference, invert)
        ref_fd, oth_fd = (l_f, l_db), (r_f, r_db)
        if ref_side == "R":
            ref_fd, oth_fd = (r_f, r_db), (l_f, l_db)
        ceq, max_dev = generate_balance_eq(
            ref_fd[0], ref_fd[1], oth_fd[0], oth_fd[1], other,
            sample_rate, match_level=match_level, tol_db=tol_db,
        )
        eqs = [ceq]
    
    return name, eqs, max_dev
