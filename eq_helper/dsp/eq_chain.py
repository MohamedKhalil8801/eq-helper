"""EQ filter chain: dataclass, SOS builder, persistence, and APO export.

Persistence has two stages:

1. **JSON-only** (before first Apply to APO): filters are stored inline in
   ``~/.eq_helper/eq_filters_<device>.json``. The app applies EQ via sosfilt.
2. **APO-backed** (after Apply to APO): the JSON stores only a reference to
   the APO ``.txt`` file. All edits read/write the APO file directly.
   Equalizer APO applies the EQ; the app disables its own sosfilt chain.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from eq_helper.dsp.graphic_eq import (
    GraphicEQ,
    GraphicEQBand,
    graphic_eq_to_apo_text,
    parse_graphic_eq_line,
)
from eq_helper.dsp.biquad import (
    high_shelf_sos,
    highpass_sos,
    low_shelf_sos,
    lowpass_sos,
    peaking_sos,
    sos_magnitude_response,
)

_FILTER_BUILDERS = {
    "PK": lambda f, fs: peaking_sos(f.fc_hz, f.gain_db, f.q, fs),
    "LS": lambda f, fs: low_shelf_sos(f.fc_hz, f.gain_db, f.q, fs),
    "HS": lambda f, fs: high_shelf_sos(f.fc_hz, f.gain_db, f.q, fs),
    "LP": lambda f, fs: lowpass_sos(f.fc_hz, f.q, fs),
    "HP": lambda f, fs: highpass_sos(f.fc_hz, f.q, fs),
}

_FILTER_RE = re.compile(
    r"^\s*Filter\s*\d*\s*:\s*(?P<on>ON|OFF)\s+"
    r"(?P<type>[A-Za-z]+)\s+"
    r"Fc\s+(?P<fc>[-+0-9.eE]+)\s*Hz"
    r"(?:\s+Gain\s+(?P<gain>[-+0-9.eE]+)\s*dB)?"
    r"(?:\s+Q\s+(?P<q>[-+0-9.eE]+))?",
    re.IGNORECASE,
)
_PREAMP_RE = re.compile(
    r"^\s*Preamp\s*:\s*(?P<gain>[-+0-9.eE]+)\s*dB", re.IGNORECASE
)


def parse_preamp(text: str) -> float:
    """First ``Preamp:`` value in the text, or 0.0 if none."""
    for line in text.splitlines():
        m = _PREAMP_RE.match(line)
        if m:
            return float(m.group("gain"))
    return 0.0


@dataclass
class EQFilter:
    """One user-defined parametric EQ filter."""

    filter_type: str = "PK"
    fc_hz: float = 1000.0
    gain_db: float = 0.0
    q: float = 1.0
    enabled: bool = True


# --------------------------------------------------------------- SOS building


def build_chain_sos(
    filters: List[EQFilter], sample_rate: int
) -> Optional[np.ndarray]:
    """Stack all enabled filters into one SOS matrix for sosfilt."""
    sections = []
    for f in filters:
        if not f.enabled:
            continue
        builder = _FILTER_BUILDERS.get(f.filter_type)
        if builder is None:
            continue
        sections.append(builder(f, sample_rate))
    if not sections:
        return None
    return np.vstack(sections)


def compute_chain_response(
    filters: List[EQFilter], freqs: np.ndarray, sample_rate: int
) -> np.ndarray:
    """Compute the combined magnitude response (dB) for graph display."""
    sos = build_chain_sos(filters, sample_rate)
    if sos is None:
        return np.zeros_like(freqs, dtype=np.float64)
    return sos_magnitude_response(sos, freqs, sample_rate)


# ------------------------------------------------------------- APO format I/O


def filters_to_apo_text(
    filters: List[EQFilter],
    preamp_db: float = 0.0,
    graphic_eqs: Optional[List[GraphicEQ]] = None,
) -> str:
    """Render parametric filters + graphic EQs as APO-format text."""
    lines = [f"Preamp: {preamp_db} dB"]
    for i, f in enumerate(filters, 1):
        on = "ON" if f.enabled else "OFF"
        if f.filter_type in ("LP", "HP"):
            lines.append(
                f"Filter {i}: {on} {f.filter_type} Fc {f.fc_hz} Hz Q {f.q}"
            )
        else:
            lines.append(
                f"Filter {i}: {on} {f.filter_type} "
                f"Fc {f.fc_hz} Hz Gain {f.gain_db} dB Q {f.q}"
            )
    if graphic_eqs:
        for geq in graphic_eqs:
            if geq.bands:
                lines.append(graphic_eq_to_apo_text(geq))
    return "\n".join(lines) + "\n"


def parse_apo_filters(text: str) -> List[EQFilter]:
    """Parse APO-format text into a list of EQFilter objects."""
    filters = []
    for line in text.splitlines():
        m = _FILTER_RE.match(line)
        if not m:
            continue
        gain_str = m.group("gain")
        q_str = m.group("q")
        filters.append(
            EQFilter(
                filter_type=m.group("type").upper(),
                fc_hz=float(m.group("fc")),
                gain_db=float(gain_str) if gain_str else 0.0,
                q=float(q_str) if q_str else 0.707,
                enabled=m.group("on").upper() == "ON",
            )
        )
    return filters


def write_apo_file(
    path: Path,
    filters: List[EQFilter],
    preamp_db: float = 0.0,
    graphic_eqs: Optional[List[GraphicEQ]] = None,
) -> None:
    """Write parametric filters + graphic EQs to an APO .txt file."""
    path.write_text(
        filters_to_apo_text(filters, preamp_db, graphic_eqs), encoding="utf-8"
    )


def read_apo_file(path: Path) -> tuple[List[EQFilter], List[GraphicEQ]]:
    """Read parametric filters and graphic EQs from an APO .txt file.

    Returns ``(filters, graphic_eqs)``.
    """
    filters, geqs, _preamp = read_apo_file_full(path)
    return filters, geqs


def read_apo_file_full(
    path: Path,
) -> tuple[List[EQFilter], List[GraphicEQ], float]:
    """Read filters, graphic EQs, AND the preamp from an APO .txt file.

    Returns ``(filters, graphic_eqs, preamp_db)``. ``preamp_db`` is 0.0 if the
    file has no ``Preamp:`` line. Used by the editor so an imported/AutoEQ
    preamp survives a load -> edit -> save round-trip.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], [], 0.0
    filters = parse_apo_filters(text)
    geqs: List[GraphicEQ] = []
    for i, line in enumerate(text.splitlines()):
        geq = parse_graphic_eq_line(line, name=f"GEQ {i + 1}")
        if geq is not None:
            geqs.append(geq)
    return filters, geqs, parse_preamp(text)


# ---------------------------------------------------------------- persistence


def _sanitize_device_name(device: str) -> str:
    return re.sub(r"[^\w\s-]", "", device).strip().replace(" ", "_")[:60]


def _eq_filters_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".eq_helper"


def _json_path(device: str) -> Path:
    return _eq_filters_dir() / f"eq_filters_{_sanitize_device_name(device)}.json"


def _read_json(device: str) -> dict:
    path = _json_path(device)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(device: str, data: dict) -> None:
    d = _eq_filters_dir()
    d.mkdir(parents=True, exist_ok=True)
    _json_path(device).write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_apo_file_path(device: str) -> Optional[str]:
    """Return the APO file path from the JSON, or None if not yet exported."""
    data = _read_json(device)
    return data.get("apo_file")


def save_filters(
    device: str,
    filters: List[EQFilter],
    graphic_eqs: Optional[List[GraphicEQ]] = None,
) -> None:
    """Save filters + graphic EQs. If APO-backed, write directly to the APO
    file. Otherwise save inline in the JSON file."""
    data = _read_json(device)
    apo_path = data.get("apo_file")

    if apo_path and Path(apo_path).exists():
        write_apo_file(Path(apo_path), filters, graphic_eqs=graphic_eqs)
    else:
        data["device"] = device
        data["filters"] = [asdict(f) for f in filters]
        if graphic_eqs:
            data["graphic_eqs"] = [
                {"name": g.name, "enabled": g.enabled, "mode": g.mode,
                 "bands": [asdict(b) for b in g.bands]}
                for g in graphic_eqs
            ]
        else:
            data["graphic_eqs"] = []
        data.pop("apo_file", None)
        _write_json(device, data)


def load_filters(
    device: str,
) -> tuple[List[EQFilter], List[GraphicEQ]]:
    """Load parametric filters and graphic EQs for a device.

    Returns ``(filters, graphic_eqs)``.
    """
    data = _read_json(device)
    apo_path = data.get("apo_file")

    if apo_path and Path(apo_path).exists():
        return read_apo_file(Path(apo_path))

    # JSON inline.
    filters = [
        EQFilter(
            filter_type=f.get("filter_type", "PK"),
            fc_hz=float(f.get("fc_hz", 1000)),
            gain_db=float(f.get("gain_db", 0)),
            q=float(f.get("q", 1)),
            enabled=bool(f.get("enabled", True)),
        )
        for f in data.get("filters", [])
    ]
    geqs: List[GraphicEQ] = []
    for g in data.get("graphic_eqs", []):
        geqs.append(
            GraphicEQ(
                name=g.get("name", "GEQ"),
                enabled=g.get("enabled", True),
                mode=g.get("mode", "variable"),
                bands=[
                    GraphicEQBand(
                        freq_hz=float(b.get("freq_hz", 1000)),
                        gain_db=float(b.get("gain_db", 0)),
                    )
                    for b in g.get("bands", [])
                ],
            )
        )
    return filters, geqs


def is_apo_backed(device: str) -> bool:
    """Return True if this device's filters are stored in an APO file."""
    data = _read_json(device)
    apo_path = data.get("apo_file")
    return bool(apo_path and Path(apo_path).exists())


# ----------------------------------------------------------- APO export


def export_to_apo(
    filters: List[EQFilter],
    apo_config_dir: Path,
    device: str,
    preamp_db: float = 0.0,
    graphic_eqs: Optional[List[GraphicEQ]] = None,
) -> Path:
    """Write filters + graphic EQs to an APO file in the config dir and update
    the JSON reference so all future edits go directly to this file.

    Returns the APO file path.
    """
    data = _read_json(device)
    existing = data.get("apo_file")

    if existing and Path(existing).exists():
        target = Path(existing)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = _sanitize_device_name(device)
        filename = f"EQ_{safe_name}_{timestamp}.txt"
        target = apo_config_dir / filename

    write_apo_file(target, filters, preamp_db, graphic_eqs=graphic_eqs)

    # Update the JSON to reference the APO file (remove inline filters).
    data["device"] = device
    data["apo_file"] = str(target)
    data.pop("filters", None)
    _write_json(device, data)

    return target
