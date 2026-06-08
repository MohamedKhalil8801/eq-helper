"""Compute the frequency response of a set of APO parametric EQ filters.

Given an APO profile file (containing ``Preamp:``, ``Filter:``, and nested
``Include:`` lines), computes the combined magnitude response in dB at a given
set of frequencies.  Nested includes are resolved recursively (with cycle
detection) so the displayed EQ curve reflects the full configuration tree.

Supports PK (peaking), LS (low shelf), HS (high shelf), LP (low pass), and
HP (high pass) filter types.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional, Set

import numpy as np

from eq_helper.dsp.graphic_eq import graphic_eq_response, parse_graphic_eq_line

_FILTER_RE = re.compile(
    r"^\s*Filter\s*\d*\s*:\s*(?:ON|OFF)\s+"
    r"(?P<type>[A-Za-z]+)\s+"
    r"Fc\s+(?P<fc>[-+0-9.eE]+)\s*Hz"
    r"(?:\s+Gain\s+(?P<gain>[-+0-9.eE]+)\s*dB)?"
    r"(?:\s+Q\s+(?P<q>[-+0-9.eE]+))?",
    re.IGNORECASE,
)
_PREAMP_RE = re.compile(
    r"^\s*Preamp\s*:\s*(?P<gain>[-+0-9.eE]+)\s*dB",
    re.IGNORECASE,
)
_FILTER_OFF_RE = re.compile(
    r"^\s*Filter\s*\d*\s*:\s*OFF\s+",
    re.IGNORECASE,
)
# Matches active (uncommented) Include lines inside a profile file.
# Commented-out includes (starting with #) are intentionally skipped.
_INCLUDE_RE = re.compile(
    r"^\s*Include\s*:\s*(?P<path>.+?)\s*$",
    re.IGNORECASE,
)


def _peaking_response(
    freqs: np.ndarray, fc: float, gain_db: float, q: float
) -> np.ndarray:
    """Biquad peaking EQ magnitude response in dB."""
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * fc
    w = 2.0 * math.pi * freqs
    # Analog transfer function magnitude squared for peaking EQ.
    jw_over_w0 = 1j * w / w0
    num = 1.0 + jw_over_w0 * (A / q) + jw_over_w0 ** 2
    den = 1.0 + jw_over_w0 * (1.0 / (A * q)) + jw_over_w0 ** 2
    return 20.0 * np.log10(np.abs(num / den) + 1e-30)


def _low_shelf_response(
    freqs: np.ndarray, fc: float, gain_db: float, q: float
) -> np.ndarray:
    """Low shelf magnitude response in dB (analog approximation)."""
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * fc
    w = 2.0 * math.pi * freqs
    s = 1j * w / w0
    sq_A = math.sqrt(max(A, 1e-30))
    num = A * (s ** 2 + s * sq_A / q + A)
    den = A * s ** 2 + s * sq_A / q + 1.0
    return 20.0 * np.log10(np.abs(num / den) + 1e-30)


def _high_shelf_response(
    freqs: np.ndarray, fc: float, gain_db: float, q: float
) -> np.ndarray:
    """High shelf magnitude response in dB (analog approximation)."""
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * fc
    w = 2.0 * math.pi * freqs
    s = 1j * w / w0
    sq_A = math.sqrt(max(A, 1e-30))
    num = A * (A * s ** 2 + s * sq_A / q + 1.0)
    den = s ** 2 + s * sq_A / q + A
    return 20.0 * np.log10(np.abs(num / den) + 1e-30)


def _lowpass_response(
    freqs: np.ndarray, fc: float, q: float
) -> np.ndarray:
    """2nd-order low-pass magnitude response in dB."""
    w0 = 2.0 * math.pi * fc
    w = 2.0 * math.pi * freqs
    s = 1j * w / w0
    H = 1.0 / (s ** 2 + s / q + 1.0)
    return 20.0 * np.log10(np.abs(H) + 1e-30)


def _highpass_response(
    freqs: np.ndarray, fc: float, q: float
) -> np.ndarray:
    """2nd-order high-pass magnitude response in dB."""
    w0 = 2.0 * math.pi * fc
    w = 2.0 * math.pi * freqs
    s = 1j * w / w0
    H = s ** 2 / (s ** 2 + s / q + 1.0)
    return 20.0 * np.log10(np.abs(H) + 1e-30)


def compute_eq_response(
    profile_text: str,
    freqs: np.ndarray,
    *,
    base_dir: Optional[Path] = None,
    _visited: Optional[Set[str]] = None,
) -> np.ndarray:
    """Return the combined EQ magnitude in dB at each frequency.

    Parses ``Preamp:``, ``Filter N: ON ...``, and ``Include:`` lines from
    ``profile_text`` and sums their responses.  ``Include:`` lines are
    resolved recursively (relative paths resolved against ``base_dir``).
    A ``_visited`` set prevents infinite cycles.

    Disabled filters (``OFF``) and commented-out includes are skipped.
    """
    if _visited is None:
        _visited = set()

    response = np.zeros_like(freqs, dtype=np.float64)

    for line in profile_text.splitlines():
        # --- Preamp ---------------------------------------------------------
        pm = _PREAMP_RE.match(line)
        if pm:
            response += float(pm.group("gain"))
            continue

        # --- Include (recursive) --------------------------------------------
        inc = _INCLUDE_RE.match(line)
        if inc and base_dir is not None:
            inc_path_str = inc.group("path").strip()
            inc_path = Path(inc_path_str)
            if not inc_path.is_absolute():
                inc_path = base_dir / inc_path
            try:
                resolved = str(inc_path.resolve())
            except OSError:
                continue
            if resolved in _visited:
                continue  # cycle detected
            _visited.add(resolved)
            try:
                inc_text = inc_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            response += compute_eq_response(
                inc_text,
                freqs,
                base_dir=inc_path.parent,
                _visited=_visited,
            )
            continue

        # --- Graphic EQ -----------------------------------------------------
        if line.lstrip().lower().startswith("graphiceq"):
            geq = parse_graphic_eq_line(line)
            if geq is not None:
                response += graphic_eq_response(geq.bands, freqs)
            continue

        # --- Skip disabled filters ------------------------------------------
        if _FILTER_OFF_RE.match(line):
            continue

        # --- Active filter --------------------------------------------------
        m = _FILTER_RE.match(line)
        if not m:
            continue

        ftype = m.group("type").upper()
        fc = float(m.group("fc"))
        gain_str = m.group("gain")
        q_str = m.group("q")
        gain = float(gain_str) if gain_str else 0.0
        q = float(q_str) if q_str else 0.707

        if ftype == "PK":
            response += _peaking_response(freqs, fc, gain, q)
        elif ftype in ("LS", "LSC"):
            response += _low_shelf_response(freqs, fc, gain, q)
        elif ftype in ("HS", "HSC"):
            response += _high_shelf_response(freqs, fc, gain, q)
        elif ftype in ("LP", "LPQ"):
            response += _lowpass_response(freqs, fc, q)
        elif ftype in ("HP", "HPQ"):
            response += _highpass_response(freqs, fc, q)

    return response


def load_profile_eq_response(
    profile_path: Path,
    freqs: np.ndarray,
) -> Optional[np.ndarray]:
    """Load a profile file and compute its EQ frequency response.

    Recursively resolves any ``Include:`` directives inside the profile,
    so the returned response reflects the full configuration tree.

    Returns ``None`` if the file can't be read.
    """
    try:
        text = profile_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    visited: Set[str] = set()
    try:
        visited.add(str(profile_path.resolve()))
    except OSError:
        pass
    return compute_eq_response(
        text,
        freqs,
        base_dir=profile_path.parent,
        _visited=visited,
    )
