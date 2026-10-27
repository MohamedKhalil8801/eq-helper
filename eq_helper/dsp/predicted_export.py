"""Export the calibration's predicted coupler response as REW text.

Files are 1/48-octave from 20 Hz to 20 kHz, resampled log-linearly from the
model's native grid with no smoothing, in REW "Export measurement as text"
form (``* `` header, then ``Freq SPL`` rows) so squig.link and PEQdB studio
accept them. Both sides share one level offset, so the L/R difference survives.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

import numpy as np

from eq_helper.dsp.resonance import ChannelEQ

# Put 1 kHz near the targets' 75 dB so curves overlay sensibly in viewers.
LEVEL_AT_1K_DB = 75.0
_SIDE_ORDER = ("L", "R", "M")


def grid_1_48_octave(lo: float = 20.0, hi: float = 20000.0) -> np.ndarray:
    n = int(round(48 * np.log2(hi / lo))) + 1
    return np.geomspace(lo, hi, n)


def resample_log(freqs, db, grid) -> np.ndarray:
    """Linear interpolation in log frequency; flat beyond the data."""
    return np.interp(np.log(grid), np.log(np.asarray(freqs, dtype=float)), np.asarray(db, dtype=float))


def predicted_curves(eq: ChannelEQ, with_eq: bool = False) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    grid = grid_1_48_octave()
    out = {}
    for side in _SIDE_ORDER:
        ch = eq.channels.get(side)
        if ch is None:
            continue
        curve = ch.predicted_db + (ch.correction_db if with_eq else 0.0)
        out[side] = (grid, resample_log(ch.freqs, curve, grid) + LEVEL_AT_1K_DB)
    return out


def safe_file_part(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", text).strip(" .") or "IEM"


def write_predicted(eq: ChannelEQ, folder, iem_name: str, meta: Mapping[str, str],
                    with_eq: bool = False) -> list[Path]:
    """Write ``<IEM> L.txt`` / ``<IEM> R.txt`` (or ``<IEM>.txt`` for a shared set)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_file_part(iem_name)
    curves = predicted_curves(eq, with_eq)
    written = []
    for side, (f, db) in curves.items():
        name = base if side == "M" else f"{base} {side}"
        if with_eq:
            name += " with EQ"
        ch = eq.channels[side]
        kind = "with EQ applied" if with_eq else "raw (no EQ)"
        lines = [f"* Predicted coupler response, {kind}: {iem_name} {'' if side == 'M' else side}".rstrip()]
        lines += [f"* {key}: {value}" for key, value in meta.items()]
        lines += [f"* Primary resonance: {ch.peak_hz:.0f} Hz",
                  f"* Target: {eq.target_name}" if with_eq else "* Target: none",
                  "* 1/48 octave, no smoothing; shared level offset for both sides",
                  "* Freq(Hz) SPL(dB)"]
        lines += [f"{x:.6f} {y:.4f}" for x, y in zip(f, db)]
        path = folder / f"{name}.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written
