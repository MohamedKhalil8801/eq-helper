"""Parse ``config.txt`` for ``Include:`` lines grouped by ``Device:`` blocks.

Each ``Include:`` line in ``config.txt`` belongs to a device scope — the most
recent ``Device:`` line above it. Lines before any ``Device:`` line belong to
the global scope (device name ``"(Global)"``).

Include paths may be **relative** (resolved against the APO config directory)
or **absolute** (pointing to files anywhere on the filesystem).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_INCLUDE_RE = re.compile(
    r"^(?P<commented>\s*#\s*)?Include\s*:\s*(?P<path>.+?)\s*$",
    re.IGNORECASE,
)
_DEVICE_RE = re.compile(
    r"^\s*Device\s*:\s*(?P<name>.+?)\s*$",
    re.IGNORECASE,
)

GLOBAL_DEVICE = "(Global)"


def _short_device_name(raw: str) -> str:
    """Extract a readable short name from a Device: line value.

    APO device lines look like:
        Speakers DAWN PRO2 {abdbfe13-...}
        Headset USB-C to 3.5mm Headphone Jack Adapter {c22f2abc-...}

    We strip the GUID and any semicolon-separated secondary devices.
    """
    # Take the first device if multiple are separated by semicolons.
    name = raw.split(";")[0].strip()
    # Strip the trailing {GUID}.
    name = re.sub(r"\s*\{[0-9a-fA-F-]+\}\s*$", "", name)
    return name.strip() or raw


@dataclass
class IncludeEntry:
    """One ``Include:`` line found in ``config.txt``."""

    name: str  # display name (filename stem)
    include_path: str  # the path string exactly as written in config.txt
    resolved_path: Path  # absolute path after resolving against config dir
    is_active: bool  # True if the line is NOT commented out
    line_number: int  # 0-based line index in config.txt
    raw_line: str  # the full original line text (with newline stripped)
    device: str  # short device name this include belongs to
    device_raw: str = ""  # full Device: value incl. {GUID}; "" for global scope


def scan_config_includes(config_path: Path) -> List[IncludeEntry]:
    """Return every ``Include:`` line in ``config_path``.

    Results are in file order. Each entry carries the ``device`` name from
    the most recent ``Device:`` line above it.
    """
    if not config_path.exists():
        return []
    config_dir = config_path.parent

    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries: List[IncludeEntry] = []
    current_device = GLOBAL_DEVICE
    current_device_raw = ""

    for line_no, raw in enumerate(text.splitlines()):
        # Track device scope.
        dm = _DEVICE_RE.match(raw)
        if dm:
            current_device = _short_device_name(dm.group("name"))
            current_device_raw = dm.group("name").strip()
            continue

        m = _INCLUDE_RE.match(raw)
        if not m:
            continue
        is_active = m.group("commented") is None
        include_path = m.group("path").strip()

        p = Path(include_path)
        if p.is_absolute():
            resolved = p
        else:
            resolved = config_dir / p

        entries.append(
            IncludeEntry(
                name=resolved.stem,
                include_path=include_path,
                resolved_path=resolved,
                is_active=is_active,
                line_number=line_no,
                raw_line=raw,
                device=current_device,
                device_raw=current_device_raw,
            )
        )
    return entries


def group_by_device(entries: List[IncludeEntry]) -> Dict[str, List[IncludeEntry]]:
    """Group include entries by their device scope. Preserves file order."""
    groups: Dict[str, List[IncludeEntry]] = {}
    for e in entries:
        groups.setdefault(e.device, []).append(e)
    return groups


def scan_devices(config_path: Path) -> List[str]:
    """Short names of all named ``Device:`` lines, in order, de-duplicated.

    Unlike grouping include entries, this lists a device even when it has zero
    ``Include:`` lines — so a freshly-configured device still shows up (and the
    user can create its first preset). Bare ``Device:`` resets are ignored.
    """
    if not config_path.exists():
        return []
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: List[str] = []
    seen = set()
    for raw in text.splitlines():
        dm = _DEVICE_RE.match(raw)
        if not dm:
            continue
        name = _short_device_name(dm.group("name"))
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def device_raw_for_short(config_path: Path, short: str) -> Optional[str]:
    """Full ``Device:`` value (incl. ``{GUID}``) of the first scope whose short
    name matches ``short`` — used to scope-match writes precisely. ``None`` if
    no such device exists."""
    if not config_path.exists():
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    target = short.strip().lower()
    for raw in text.splitlines():
        dm = _DEVICE_RE.match(raw)
        if dm and _short_device_name(dm.group("name")).strip().lower() == target:
            return dm.group("name").strip()
    return None
