"""Loaders for headphone/earphone frequency-response files.

Supports two shapes with one parser:

- **oratory1990 / AutoEQ CSV** — comma-separated ``Frequency (Hz),Raw (dB)``
  with an optional header row.
- **REW "Export measurement as text"** — whitespace-separated
  ``freq SPL phase`` columns with a ``*``-prefixed comment header.

Only the first two numeric columns (frequency, level dB) are used; any phase or
extra columns are ignored.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import numpy as np


class OverlayLoadError(ValueError):
    """Raised when a measurement file can't be parsed."""


def _parse_row(line: str) -> List[float]:
    """Return the leading float-parseable tokens of a data line.

    Splits on comma when the line contains one (CSV), otherwise on any
    whitespace (REW text / space- or tab-delimited). Returns ``[]`` for a line
    whose first two tokens aren't both numeric (e.g. a header row).
    """
    stripped = line.strip()
    if not stripped:
        return []
    tokens = stripped.split(",") if "," in stripped else stripped.split()
    out: List[float] = []
    for tok in tokens:
        tok = tok.strip()
        try:
            out.append(float(tok))
        except ValueError:
            break  # stop at the first non-numeric token
    return out


def load_frequency_response(path: str | Path) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(freq_hz, db)`` from an AutoEQ/oratory CSV or a REW text export.

    Comment lines (``*`` or ``#``) and blank lines are skipped; a non-numeric
    header row is ignored. Frequencies are sorted ascending and rows with a
    non-positive frequency are dropped. Raises :class:`OverlayLoadError` if the
    file is missing or contains no parseable ``freq, level`` rows.
    """
    p = Path(path)
    if not p.exists():
        raise OverlayLoadError(f"measurement file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OverlayLoadError(f"could not open {p}: {exc}") from exc

    freqs: List[float] = []
    dbs: List[float] = []
    for line in text.splitlines():
        s = line.lstrip()
        if not s or s[0] in "*#;":
            continue
        row = _parse_row(line)
        if len(row) < 2:
            continue  # header / malformed line
        freqs.append(row[0])
        dbs.append(row[1])

    if not freqs:
        raise OverlayLoadError(f"{p}: no parseable 'frequency, level' rows found")

    f = np.asarray(freqs, dtype=np.float64)
    d = np.asarray(dbs, dtype=np.float64)
    mask = f > 0
    f, d = f[mask], d[mask]
    if f.size == 0:
        raise OverlayLoadError(f"{p}: no rows with a positive frequency")
    order = np.argsort(f)
    f, d = f[order], d[order]
    # Drop duplicate frequencies (keep the first): downstream np.interp needs a
    # strictly-increasing x, and some exporters repeat a frequency.
    keep = np.concatenate(([True], np.diff(f) > 0))
    return f[keep], d[keep]


def load_measurement_csv(path: str | Path) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(freq_hz, db)`` from an oratory1990/AutoEQ CSV (or any format
    :func:`load_frequency_response` accepts)."""
    return load_frequency_response(path)


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
