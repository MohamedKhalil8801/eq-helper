"""Graphic EQ: frequency-gain band pairs with FIR generation and APO format.

A graphic EQ is a set of (frequency, gain_dB) pairs. APO interpolates between
them linearly in dB. For in-app preview we convert to an FIR filter via
``scipy.signal.firwin2``.

Two modes:

- **Variable** (default): no bands initially; user places them freely.
- **Fixed**: ISO octave-spaced bands; frequency is locked, only gain is
  adjustable. The "+4 bands" feature interpolates the current curve to add
  finer control without changing the overall shape.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from scipy.signal import firwin2


def region_for_freq(
    freqs: List[float],
    target: float,
    f_min: float = 20.0,
    f_max: float = 20000.0,
) -> tuple[float, float]:
    """The frequency span a graphic-EQ point at ``target`` influences.

    Returns ``(lo, hi)`` = the nearest lower neighbour (or ``f_min`` for the
    lowest point) to the nearest higher neighbour (or ``f_max`` for the highest).
    A lone point spans the full range. Always ``0 < lo < hi``.
    """
    lo = max((f for f in freqs if f < target), default=f_min)
    hi = min((f for f in freqs if f > target), default=f_max)
    lo = max(f_min, min(f_max, lo))
    hi = max(f_min, min(f_max, hi))
    if hi <= lo:
        hi = min(f_max, lo * 1.01)
    return float(lo), float(hi)


@dataclass
class GraphicEQBand:
    """One frequency-gain point in a graphic EQ."""

    freq_hz: float
    gain_db: float = 0.0


@dataclass
class GraphicEQ:
    """A complete graphic EQ (one ``GraphicEQ:`` line in APO)."""

    name: str = "GEQ 1"
    bands: List[GraphicEQBand] = field(default_factory=list)
    enabled: bool = True
    mode: str = "variable"  # "variable" or "fixed"


# ---------------------------------------------------------- fixed band sets

# ISO 1/3-octave center frequencies (20 Hz – 20 kHz range).
_ALL_ISO_THIRDS = [
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160,
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
]

# Presets for common band counts (indices into _ALL_ISO_THIRDS).
_FIXED_PRESETS = {
    8: [63, 125, 250, 500, 1000, 2000, 4000, 8000],
    12: [63, 100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300, 10000],
}


def _generate_fixed_freqs(count: int) -> List[float]:
    """Generate ``count`` log-spaced frequencies from the ISO set.

    For counts ≤12, uses hand-picked presets. For larger counts, picks
    evenly-spaced entries from the full 1/3-octave table.
    """
    if count in _FIXED_PRESETS:
        return list(_FIXED_PRESETS[count])
    # Pick `count` entries evenly from the full ISO list.
    n = len(_ALL_ISO_THIRDS)
    indices = [round(i * (n - 1) / (count - 1)) for i in range(count)]
    # Deduplicate (can happen at high counts).
    seen: set[int] = set()
    result: List[float] = []
    for idx in indices:
        if idx not in seen:
            seen.add(idx)
            result.append(float(_ALL_ISO_THIRDS[idx]))
    return result


def make_fixed_bands(count: int) -> List[GraphicEQBand]:
    """Generate ``count`` ISO-spaced bands at 0 dB."""
    return [GraphicEQBand(freq_hz=f, gain_db=0.0) for f in _generate_fixed_freqs(count)]


def double_bands(current_bands: List[GraphicEQBand]) -> List[GraphicEQBand]:
    """Double the number of bands by interpolating the current response curve.

    Existing band gains are preserved at their exact frequencies. New bands
    are inserted at frequencies chosen from the ISO set, with gains set by
    linear interpolation in log-frequency space.
    """
    if not current_bands:
        return make_fixed_bands(8)

    total = len(current_bands) * 2
    new_freqs = _generate_fixed_freqs(total)

    # Build interpolation from current bands (log-freq → dB).
    old_log = np.array([math.log10(b.freq_hz) for b in current_bands])
    old_db = np.array([b.gain_db for b in current_bands])

    result: List[GraphicEQBand] = []
    for f in new_freqs:
        log_f = math.log10(f)
        # If this frequency matches an existing band, keep its exact gain.
        existing = None
        for b in current_bands:
            if abs(b.freq_hz - f) / f < 0.01:
                existing = b
                break
        if existing is not None:
            result.append(GraphicEQBand(freq_hz=f, gain_db=existing.gain_db))
        else:
            # Interpolate.
            gain = float(np.interp(log_f, old_log, old_db))
            result.append(GraphicEQBand(freq_hz=f, gain_db=round(gain, 2)))
    return result


# ----------------------------------------------------------- FIR generation


def graphic_eq_to_fir(
    bands: List[GraphicEQBand],
    sample_rate: int,
    order: int = 511,
) -> Optional[np.ndarray]:
    """Convert frequency-gain pairs to an FIR filter via ``firwin2``.

    Returns ``None`` if there are fewer than 2 bands.
    """
    if len(bands) < 2:
        return None

    nyquist = sample_rate / 2.0
    sorted_bands = sorted(bands, key=lambda b: b.freq_hz)

    # Build frequency/gain arrays with DC and Nyquist anchors.
    freqs = [0.0]
    gains_db = [sorted_bands[0].gain_db]  # extrapolate DC from lowest band
    for b in sorted_bands:
        if b.freq_hz <= 0 or b.freq_hz >= nyquist:
            continue
        freqs.append(b.freq_hz)
        gains_db.append(b.gain_db)
    freqs.append(nyquist)
    gains_db.append(sorted_bands[-1].gain_db)  # extrapolate Nyquist

    freqs_norm = np.array(freqs) / nyquist
    gains_lin = 10.0 ** (np.array(gains_db) / 20.0)

    if order % 2 == 0:
        order += 1

    fir = firwin2(order, freqs_norm, gains_lin)

    # Normalize so flat (0 dB) input → flat output.
    energy = np.sqrt(np.sum(fir ** 2))
    if energy > 0:
        fir /= energy

    return fir


def combine_graphic_eqs(
    geqs: List[GraphicEQ], sample_rate: int, order: int = 511
) -> Optional[np.ndarray]:
    """Combine all enabled GraphicEQs into one FIR.

    Sums the dB gains at a common set of frequencies, then builds one FIR.
    """
    enabled = [g for g in geqs if g.enabled and g.bands]
    if not enabled:
        return None

    # Merge all bands onto a common frequency grid.
    all_freqs: set[float] = set()
    for g in enabled:
        for b in g.bands:
            all_freqs.add(b.freq_hz)
    freq_list = sorted(all_freqs)
    if len(freq_list) < 2:
        return None

    log_freqs = np.array([math.log10(f) for f in freq_list])
    combined_db = np.zeros(len(freq_list))
    for g in enabled:
        g_log = np.array([math.log10(b.freq_hz) for b in sorted(g.bands, key=lambda b: b.freq_hz)])
        g_db = np.array([b.gain_db for b in sorted(g.bands, key=lambda b: b.freq_hz)])
        combined_db += np.interp(log_freqs, g_log, g_db)

    merged_bands = [
        GraphicEQBand(freq_hz=f, gain_db=float(db))
        for f, db in zip(freq_list, combined_db)
    ]
    return graphic_eq_to_fir(merged_bands, sample_rate, order)


# --------------------------------------------------------- response curve


def graphic_eq_response(
    bands: List[GraphicEQBand], freqs: np.ndarray
) -> np.ndarray:
    """Compute the interpolated magnitude response (dB) for graph display.

    Uses linear interpolation in log-frequency space (matching APO's behavior).
    """
    if len(bands) == 1:
        # APO holds a single GraphicEQ point flat at its gain across the whole
        # spectrum; returning zeros would silently under-count the response.
        return np.full_like(freqs, bands[0].gain_db, dtype=np.float64)
    if len(bands) < 2:
        return np.zeros_like(freqs, dtype=np.float64)
    sorted_bands = sorted(bands, key=lambda b: b.freq_hz)
    log_band_f = np.array([math.log10(b.freq_hz) for b in sorted_bands])
    band_db = np.array([b.gain_db for b in sorted_bands])
    log_freqs = np.log10(np.maximum(freqs, 1.0))
    return np.interp(log_freqs, log_band_f, band_db).astype(np.float64)


# ----------------------------------------------------------- APO format


def graphic_eq_to_apo_text(geq: GraphicEQ) -> str:
    """Render as ``GraphicEQ: f1 g1; f2 g2; ...``."""
    if not geq.bands:
        return ""
    sorted_bands = sorted(geq.bands, key=lambda b: b.freq_hz)
    pairs = []
    for b in sorted_bands:
        # Whole Hz stays integral; anything else keeps full float precision.
        if float(b.freq_hz).is_integer():
            f_str = str(int(b.freq_hz))
        else:
            f_str = repr(float(b.freq_hz))
        pairs.append(f"{f_str} {repr(float(b.gain_db))}")
    return "GraphicEQ: " + "; ".join(pairs)


_GEQ_RE = re.compile(r"^\s*GraphicEQ\s*:\s*(?P<pairs>.+)$", re.IGNORECASE)


def parse_graphic_eq_line(line: str, name: str = "GEQ") -> Optional[GraphicEQ]:
    """Parse a ``GraphicEQ: ...`` line into a GraphicEQ object."""
    m = _GEQ_RE.match(line)
    if not m:
        return None
    bands: List[GraphicEQBand] = []
    for pair in m.group("pairs").split(";"):
        parts = pair.strip().split()
        if len(parts) < 2:
            continue
        try:
            freq = float(parts[0])
            gain = float(parts[1])
            bands.append(GraphicEQBand(freq_hz=freq, gain_db=gain))
        except ValueError:
            continue
    if not bands:
        return None
    return GraphicEQ(name=name, bands=bands)
