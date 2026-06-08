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
from typing import List, Optional, Set

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
# Active (uncommented) Channel: line — selects which channels following filters
# apply to (stateful in APO). A leading '#' prevents a match (comment).
_CHANNEL_RE = re.compile(
    r"^\s*Channel\s*:\s*(?P<chans>.+?)\s*$",
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


def _line_contribution(line: str, freqs: np.ndarray):
    """dB contribution of a single ``Preamp:`` / ``GraphicEQ:`` / active
    ``Filter:`` line — a scalar or an ndarray broadcastable over ``freqs`` — or
    ``None`` for lines that contribute nothing (``Include:``/``Channel:``
    directives, ``OFF`` filters, comments, unrecognised lines).

    Includes are handled by the caller (they need recursion); everything else
    routes through here so the single-response and per-channel paths can't drift.
    """
    pm = _PREAMP_RE.match(line)
    if pm:
        return float(pm.group("gain"))

    if line.lstrip().lower().startswith("graphiceq"):
        geq = parse_graphic_eq_line(line)
        if geq is None:
            return None
        return graphic_eq_response(geq.bands, freqs)

    if _FILTER_OFF_RE.match(line):
        return None

    m = _FILTER_RE.match(line)
    if not m:
        return None

    ftype = m.group("type").upper()
    fc = float(m.group("fc"))
    gain_str = m.group("gain")
    q_str = m.group("q")
    gain = float(gain_str) if gain_str else 0.0
    q = float(q_str) if q_str else 0.707
    if q <= 0.0:
        q = 1e-4  # guard degenerate Q (avoids division-by-zero in biquads)

    if ftype == "PK":
        return _peaking_response(freqs, fc, gain, q)
    if ftype in ("LS", "LSC"):
        return _low_shelf_response(freqs, fc, gain, q)
    if ftype in ("HS", "HSC"):
        return _high_shelf_response(freqs, fc, gain, q)
    if ftype in ("LP", "LPQ"):
        return _lowpass_response(freqs, fc, q)
    if ftype in ("HP", "HPQ"):
        return _highpass_response(freqs, fc, q)
    return None


def _resolve_include(line: str, base_dir: Optional[Path], visited: Set[str]):
    """For an active ``Include:`` line, return ``(included_text, child_dir)`` to
    recurse into, or ``None`` if it's not an include / can't be read / is a
    cycle. Mutates ``visited`` with the resolved path."""
    if base_dir is None:
        return None
    inc = _INCLUDE_RE.match(line)
    if not inc:
        return None
    inc_path = Path(inc.group("path").strip())
    if not inc_path.is_absolute():
        inc_path = base_dir / inc_path
    try:
        resolved = str(inc_path.resolve())
    except OSError:
        return None
    if resolved in visited:
        return None  # cycle
    visited.add(resolved)
    try:
        text = inc_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text, inc_path.parent


def compute_eq_response(
    profile_text: str,
    freqs: np.ndarray,
    *,
    base_dir: Optional[Path] = None,
    _visited: Optional[Set[str]] = None,
) -> np.ndarray:
    """Return the combined EQ magnitude in dB at each frequency.

    Parses ``Preamp:``, ``Filter N: ON ...``, ``GraphicEQ:`` and ``Include:``
    lines from ``profile_text`` and sums their responses.  ``Include:`` lines are
    resolved recursively (relative paths resolved against ``base_dir``).  A
    ``_visited`` set prevents infinite cycles.

    Channel selection is intentionally ignored here (every filter is summed) —
    this drives the single combined display curve. Use
    :func:`compute_channel_responses` for channel-aware per-side responses.

    Disabled filters (``OFF``) and commented-out includes are skipped.
    """
    if _visited is None:
        _visited = set()

    response = np.zeros_like(freqs, dtype=np.float64)

    for line in profile_text.splitlines():
        inc = _resolve_include(line, base_dir, _visited)
        if inc is not None:
            response += compute_eq_response(
                inc[0], freqs, base_dir=inc[1], _visited=_visited
            )
            continue
        contrib = _line_contribution(line, freqs)
        if contrib is not None:
            response += contrib

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


def collect_feature_freqs(
    profile_text: str,
    *,
    base_dir: Optional[Path] = None,
    _visited: Optional[Set[str]] = None,
) -> List[float]:
    """Collect the center frequencies of every active parametric filter and the
    point frequencies of every GraphicEQ across the whole include tree.

    Used to build an evaluation grid dense enough to resolve narrow (high-Q)
    features: a peak whose ``Fc`` falls between coarse grid samples would
    otherwise be missed by both the fit and the verification.
    """
    if _visited is None:
        _visited = set()

    out: List[float] = []
    for line in profile_text.splitlines():
        inc = _INCLUDE_RE.match(line)
        if inc and base_dir is not None:
            inc_path = Path(inc.group("path").strip())
            if not inc_path.is_absolute():
                inc_path = base_dir / inc_path
            try:
                resolved = str(inc_path.resolve())
            except OSError:
                continue
            if resolved in _visited:
                continue
            _visited.add(resolved)
            try:
                inc_text = inc_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.extend(
                collect_feature_freqs(
                    inc_text, base_dir=inc_path.parent, _visited=_visited
                )
            )
            continue

        if line.lstrip().lower().startswith("graphiceq"):
            from eq_helper.dsp.graphic_eq import parse_graphic_eq_line

            geq = parse_graphic_eq_line(line)
            if geq is not None:
                out.extend(float(b.freq_hz) for b in geq.bands)
            continue

        if _FILTER_OFF_RE.match(line):
            continue
        m = _FILTER_RE.match(line)
        if m:
            try:
                out.append(float(m.group("fc")))
            except (TypeError, ValueError):
                pass
    return out


def load_profile_feature_freqs(profile_path: Path) -> List[float]:
    """Feature frequencies (filter centers + GraphicEQ points) for a profile
    file, resolving includes. Returns ``[]`` if the file can't be read."""
    try:
        text = profile_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    visited: Set[str] = set()
    try:
        visited.add(str(profile_path.resolve()))
    except OSError:
        pass
    return collect_feature_freqs(
        text, base_dir=profile_path.parent, _visited=visited
    )


# --------------------------------------------------------- channel awareness

# Sentinel key used when a config references no channels at all (so every filter
# applies to every channel — one combined response, exactly as before).
ALL_CHANNELS = "ALL"


def _collect_channels(
    text: str, *, base_dir: Optional[Path], _visited: Set[str]
) -> Set[str]:
    """The set of channel identifiers explicitly referenced by ``Channel:``
    lines across the include tree (upper-cased; the ``all`` keyword excluded)."""
    out: Set[str] = set()
    for line in text.splitlines():
        cm = _CHANNEL_RE.match(line)
        if cm:
            for tok in cm.group("chans").split():
                tu = tok.strip().upper()
                if tu and tu != "ALL":
                    out.add(tu)
            continue
        inc = _resolve_include(line, base_dir, _visited)
        if inc is not None:
            out |= _collect_channels(inc[0], base_dir=inc[1], _visited=_visited)
    return out


def _accumulate_channels(
    text: str,
    freqs: np.ndarray,
    base_dir: Optional[Path],
    channels: Set[str],
    acc: dict,
    state: list,
    visited: Set[str],
) -> None:
    """Walk the tree in document order, honouring stateful ``Channel:``
    selection (``state[0]`` is the live set), adding each line's contribution to
    every currently-selected channel's accumulator."""
    for line in text.splitlines():
        cm = _CHANNEL_RE.match(line)
        if cm:
            toks = [t.strip().upper() for t in cm.group("chans").split() if t.strip()]
            if not toks or any(t == "ALL" for t in toks):
                state[0] = set(channels)
            else:
                state[0] = {t for t in toks if t in channels}
            continue
        inc = _resolve_include(line, base_dir, visited)
        if inc is not None:
            _accumulate_channels(
                inc[0], freqs, inc[1], channels, acc, state, visited
            )
            continue
        contrib = _line_contribution(line, freqs)
        if contrib is not None:
            for ch in state[0]:
                acc[ch] += contrib


def compute_channel_responses(
    profile_text: str,
    freqs: np.ndarray,
    *,
    base_dir: Optional[Path] = None,
) -> dict:
    """Per-channel combined EQ response, honouring stateful ``Channel:``
    selection across the whole include tree (document order; default = all
    referenced channels).

    Returns ``{channel_id: response_db}``. If the config references no channels
    at all, returns ``{ALL_CHANNELS: combined_response}`` — identical to
    :func:`compute_eq_response`. ``Channel:`` state is threaded through
    ``Include:`` boundaries (APO treats includes as textual inclusion).
    """
    channels = _collect_channels(profile_text, base_dir=base_dir, _visited=set())
    if not channels:
        return {
            ALL_CHANNELS: compute_eq_response(
                profile_text, freqs, base_dir=base_dir
            )
        }
    acc = {ch: np.zeros_like(freqs, dtype=np.float64) for ch in channels}
    state = [set(channels)]  # mutable holder so Include recursion shares it
    _accumulate_channels(
        profile_text, freqs, base_dir, channels, acc, state, set()
    )
    return acc


def load_profile_channel_responses(
    profile_path: Path, freqs: np.ndarray
) -> Optional[dict]:
    """Channel-aware combined response for a profile file (resolving includes).
    Returns ``None`` if the file can't be read."""
    try:
        text = profile_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return compute_channel_responses(
        text, freqs, base_dir=profile_path.parent
    )
