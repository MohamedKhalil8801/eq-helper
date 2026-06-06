"""Loader for oratory1990 / AutoEQ style headphone measurement CSVs.

Expected format (two columns, comma-separated, with a header row)::

    Frequency (Hz),Raw (dB)
    20.00,85.21
    20.18,85.18
    ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np


class OverlayLoadError(ValueError):
    """Raised when the overlay CSV can't be parsed."""


def load_measurement_csv(path: str | Path) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(freq_hz, db)`` arrays from an oratory1990/AutoEQ CSV."""
    p = Path(path)
    if not p.exists():
        raise OverlayLoadError(f"CSV not found: {p}")

    # Peek at the first line: if it parses cleanly as floats, there's no
    # header; otherwise skip one row. This avoids silently dropping a data
    # row from a headerless file that happens to contain only numeric text.
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
    except OSError as exc:
        raise OverlayLoadError(f"could not open {p}: {exc}") from exc

    def _is_numeric_row(line: str) -> bool:
        parts = [s.strip() for s in line.strip().split(",")]
        if len(parts) < 2:
            return False
        try:
            for s in parts[:2]:
                float(s)
        except ValueError:
            return False
        return True

    skip = 0 if _is_numeric_row(first_line) else 1
    try:
        data = np.loadtxt(p, delimiter=",", skiprows=skip, dtype=np.float64)
    except ValueError as exc:
        raise OverlayLoadError(f"could not parse {p}: {exc}") from exc

    if data.ndim != 2 or data.shape[1] < 2:
        raise OverlayLoadError(
            f"{p}: expected a 2-column CSV, got shape {data.shape}"
        )

    freqs = data[:, 0]
    db = data[:, 1]

    # Sanity: strip rows with non-positive frequencies.
    mask = freqs > 0
    return freqs[mask], db[mask]


def normalize_at_1khz(freqs: np.ndarray, db: np.ndarray) -> np.ndarray:
    """Return ``db`` shifted so the value at ~1 kHz is zero.

    Uses linear interpolation of the nearest points; leaves ``db`` unchanged
    if 1 kHz is out of range.
    """
    if freqs.size == 0:
        return db
    if 1000.0 < freqs[0] or 1000.0 > freqs[-1]:
        return db
    ref = float(np.interp(1000.0, freqs, db))
    return db - ref
