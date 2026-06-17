"""Pure text transforms over Equalizer APO ``Include:`` lists.

Every function takes the full file text and returns new text (or a parsed
list) — no file IO, no Qt — so the rules can be unit-tested in isolation and
reused for both ``config.txt`` (device-scoped preset switching) and preset
files (their stacked EQ-file includes).

Invariants honoured by all transforms:
- Lines that are not the targeted ``Include:`` lines are preserved exactly,
  including their line endings (CRLF/LF).
- Comment state is ``# `` prefix on/off; matching is case- and slash-insensitive.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from eq_helper.apo.scanner import GLOBAL_DEVICE, _short_device_name

# prefix = optional leading "# "; body = "Include: <path>"; path = the target.
_INCLUDE_RE = re.compile(
    r"^(?P<prefix>\s*#\s*)?(?P<body>Include\s*:\s*(?P<path>.+?))\s*$",
    re.IGNORECASE,
)
# Named Device: line (has a value). Used to identify which scope we're entering.
_DEVICE_RE = re.compile(r"^\s*Device\s*:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
# Any Device: line — named OR a bare "Device:" reset (APO = all devices). Used
# purely as a scope boundary.
_ANY_DEVICE_RE = re.compile(r"^\s*Device\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class IncludeRef:
    """One ``Include:`` line (commented or not) found in a file."""

    path: str
    enabled: bool
    line_no: int  # 0-based index within the file


# ----------------------------------------------------------------- matching


def normalize_include_path(raw: str) -> str:
    """Normalize a path for matching: strip, lower-case, forward-slashes."""
    return raw.strip().lower().replace("\\", "/")


def paths_match(a: str, b: str) -> bool:
    return normalize_include_path(a) == normalize_include_path(b)


def _device_match(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _line_path(line: str) -> Optional[str]:
    m = _INCLUDE_RE.match(line)
    return m.group("path").strip() if m else None


def _ending(line: str) -> str:
    """The trailing newline of a kept-ends line ('' if none)."""
    return line[len(line.rstrip("\r\n")):]


def _dominant_ending(text: str) -> str:
    """The file's newline style ('\\r\\n' if it uses any CRLF, else '\\n')."""
    return "\r\n" if "\r\n" in text else "\n"


def _format_include(path: str, enabled: bool, ending: str = "\n") -> str:
    return f"{'' if enabled else '# '}Include: {path}{ending}"


def _abs_norm(base_dir: str, p: str) -> str:
    """Resolve ``p`` (absolute or relative to ``base_dir``) to a normalized
    absolute path string — purely lexical, never touches the filesystem."""
    pp = Path(p)
    if not pp.is_absolute():
        pp = Path(base_dir) / pp
    return os.path.normpath(str(pp))


# ------------------------------------------------------------------- parse


def parse_includes(text: str) -> List[IncludeRef]:
    refs: List[IncludeRef] = []
    for i, line in enumerate(text.splitlines()):
        m = _INCLUDE_RE.match(line)
        if not m:
            continue
        refs.append(
            IncludeRef(
                path=m.group("path").strip(),
                enabled=m.group("prefix") is None,
                line_no=i,
            )
        )
    return refs


# ---------------------------------------------------------- comment toggles


def _set_comment(line: str, *, commented: bool) -> str:
    """Return ``line`` with the comment prefix set to ``commented``."""
    m = _INCLUDE_RE.match(line)
    if not m:
        return line
    is_commented = m.group("prefix") is not None
    if commented == is_commented:
        return line
    ending = _ending(line)
    if commented:
        return "# " + line.rstrip("\r\n") + ending
    return m.group("body") + ending  # uncomment: drop the prefix


def set_active_include(
    text: str, managed_paths: Set[str], active_path: Optional[str]
) -> str:
    """Radio switch: among ``managed_paths`` include lines, uncomment the one
    matching ``active_path`` and comment the rest. Non-managed lines untouched.
    ``active_path=None`` comments out all managed includes."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        lp = _line_path(line)
        if lp is None or not any(paths_match(lp, mp) for mp in managed_paths):
            continue
        should_active = active_path is not None and paths_match(lp, active_path)
        lines[i] = _set_comment(line, commented=not should_active)
    return "".join(lines)


def set_active_include_in_device(
    text: str,
    device: str,
    base_dir: str,
    active_resolved_path: Optional[str],
) -> str:
    """Radio-switch presets **within one device scope** in ``config.txt``.

    ``device`` is matched against the **full ``Device:`` line value** (including
    its ``{GUID}``), so two physically distinct endpoints that share a friendly
    name are never confused — activating a preset under one can't comment out
    the other device's includes. Only lines under that exact scope are toggled;
    global and other-device includes are never touched. Paths are compared by
    their resolved absolute form (``base_dir`` = the APO config dir), so a
    pre-existing *relative* include still matches an absolute
    ``active_resolved_path``. ``active_resolved_path=None`` comments out every
    include in the scope.
    """
    lines = text.splitlines(keepends=True)
    cur_raw = ""  # full Device: value of the current scope ("" = global)
    active_abs = (
        paths_normalize_abs(base_dir, active_resolved_path)
        if active_resolved_path
        else None
    )
    for i, line in enumerate(lines):
        if _ANY_DEVICE_RE.match(line):
            dm = _DEVICE_RE.match(line)
            cur_raw = dm.group("name").strip() if dm else ""
            continue
        lp = _line_path(line)
        if lp is None or not _device_match(cur_raw, device):
            continue
        line_abs = paths_normalize_abs(base_dir, lp)
        should_active = active_abs is not None and paths_match(line_abs, active_abs)
        lines[i] = _set_comment(line, commented=not should_active)
    return "".join(lines)


def paths_normalize_abs(base_dir: str, p: str) -> str:
    """Public helper: resolved-absolute, normalized path for matching."""
    return _abs_norm(base_dir, p)


def set_include_enabled(text: str, path: str, enabled: bool) -> str:
    """Comment/uncomment only the include line matching ``path``."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        lp = _line_path(line)
        if lp is not None and paths_match(lp, path):
            lines[i] = _set_comment(line, commented=not enabled)
    return "".join(lines)


# ------------------------------------------------------------ add / remove


def _ensure_trailing_newline(lines: List[str], index: int, ending: str) -> None:
    """Make sure the line before ``index`` ends with a newline (so an inserted
    line doesn't get glued onto a file that lacks a final newline)."""
    if 0 < index <= len(lines):
        prev = lines[index - 1]
        if prev and not prev.endswith(("\n", "\r")):
            lines[index - 1] = prev + ending


def add_include(text: str, path: str, *, enabled: bool = True) -> str:
    """Append an include line at the end of the text (used for preset files)."""
    ending = _dominant_ending(text)
    lines = text.splitlines(keepends=True)
    _ensure_trailing_newline(lines, len(lines), ending)
    lines.append(_format_include(path, enabled, ending))
    return "".join(lines)


def add_include_under_device(
    text: str, device_short: str, path: str, *, enabled: bool = True
) -> str:
    """Insert an include under the matching ``Device:`` scope in ``config.txt``.

    Placed immediately after the last existing include in that scope (or right
    after the ``Device:`` line if it has none). Any ``Device:`` line — named or a
    bare reset — ends the scope. Raises ``ValueError`` if no matching device
    scope exists.
    """
    ending = _dominant_ending(text)
    lines = text.splitlines(keepends=True)
    cur = GLOBAL_DEVICE
    in_target = False
    insert_at: Optional[int] = None
    for i, line in enumerate(lines):
        if _ANY_DEVICE_RE.match(line):
            if in_target:
                break  # any Device: line (named or bare reset) ends the scope
            dm = _DEVICE_RE.match(line)
            cur = _short_device_name(dm.group("name")) if dm else GLOBAL_DEVICE
            if dm and _device_match(cur, device_short):
                in_target = True
                insert_at = i + 1
            continue
        if in_target and _INCLUDE_RE.match(line):
            insert_at = i + 1
    if insert_at is None:
        raise ValueError(f"device scope not found: {device_short!r}")
    _ensure_trailing_newline(lines, insert_at, ending)
    lines.insert(insert_at, _format_include(path, enabled, ending))
    return "".join(lines)


def reorder_includes_in_device(
    text: str, device: str, base_dir: str, ordered_paths: List[str]
) -> str:
    """Reorder the include lines **within one device scope** in ``config.txt`` to
    follow ``ordered_paths`` (matched by resolved absolute path).

    ``device`` is the full ``Device:`` value (incl. ``{GUID}``), matched exactly
    so a same-named device elsewhere is never touched. Non-include lines and
    other scopes stay put; each include slot in the scope is refilled by the next
    listed path; unlisted includes keep their relative order and follow.
    """
    lines = text.splitlines(keepends=True)
    cur_raw = ""
    positions: List[int] = []
    inc: List[tuple[str, str]] = []  # (path, full line)
    for i, line in enumerate(lines):
        if _ANY_DEVICE_RE.match(line):
            dm = _DEVICE_RE.match(line)
            cur_raw = dm.group("name").strip() if dm else ""
            continue
        lp = _line_path(line)
        if lp is not None and _device_match(cur_raw, device):
            positions.append(i)
            inc.append((lp, line))

    used = [False] * len(inc)
    reordered: List[str] = []
    for p in ordered_paths:
        pa = _abs_norm(base_dir, p)
        for j, (ip, il) in enumerate(inc):
            if not used[j] and paths_match(_abs_norm(base_dir, ip), pa):
                reordered.append(il)
                used[j] = True
                break
    for j, (_ip, il) in enumerate(inc):
        if not used[j]:
            reordered.append(il)

    for pos, new_line in zip(positions, reordered):
        lines[pos] = new_line
    return "".join(lines)


def remove_include(text: str, path: str) -> str:
    """Remove the include line (commented or not) matching ``path``."""
    lines = text.splitlines(keepends=True)
    kept = [
        line
        for line in lines
        if not (
            (lp := _line_path(line)) is not None and paths_match(lp, path)
        )
    ]
    return "".join(kept)


def reorder_includes(text: str, ordered_paths: List[str]) -> str:
    """Reorder include lines to follow ``ordered_paths``, in place.

    Non-include lines stay at their positions; each include slot is filled by
    the next path in ``ordered_paths``. Paths not listed keep their original
    relative order and follow the listed ones. Each line keeps its own
    enabled/commented state.
    """
    lines = text.splitlines(keepends=True)
    positions: List[int] = []
    inc: List[tuple[str, str]] = []  # (path, full line)
    for i, line in enumerate(lines):
        lp = _line_path(line)
        if lp is not None:
            positions.append(i)
            inc.append((lp, line))

    used = [False] * len(inc)
    reordered: List[str] = []
    for p in ordered_paths:
        for j, (ip, il) in enumerate(inc):
            if not used[j] and paths_match(ip, p):
                reordered.append(il)
                used[j] = True
                break
    for j, (_ip, il) in enumerate(inc):
        if not used[j]:
            reordered.append(il)

    for pos, new_line in zip(positions, reordered):
        lines[pos] = new_line
    return "".join(lines)
