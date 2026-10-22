"""Safe writes to Equalizer APO files (``config.txt``, preset files, EQ files).

All mutations go through :func:`atomic_write_with_backup`: a one-shot
``<name>.eqhelper.bak`` backup the first time a file is modified, then a
tempfile + ``os.replace`` atomic write with a short retry loop (APO's
file-watcher can briefly hold the target open on Windows → ``WinError 5``).

:func:`edit_include_file` reads a file, applies a pure text transform from
:mod:`eq_helper.apo.include_file`, and writes only if the text changed.
:func:`toggle_includes` is the original ``config.txt`` profile radio, kept for
backward compatibility (global match; scope-aware switching lives in
``include_file.set_active_include_in_device``).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, Set

from eq_helper.apo.errors import ApoWriteError
from eq_helper.apo.include_file import (  # noqa: F401 (re-exported for callers)
    normalize_include_path as _normalize_include_path,
    paths_match as _paths_match,
    set_active_include,
)

_BACKUP_SUFFIX = ".eqhelper.bak"
_RETRY_ATTEMPTS = 5
_RETRY_DELAY_S = 0.15

# Optional observer notified after every successful write, with (path, content).
# The app registers one (ApoBridge) to drive APO live-reload + ignore its own
# writes when watching files. Kept out of the write path's failure handling.
_write_observer: Optional[Callable[[Path, str], None]] = None


def set_write_observer(observer: Optional[Callable[[Path, str], None]]) -> None:
    global _write_observer
    _write_observer = observer


def atomic_write_with_backup(
    path: Path, content: str, *, encoding: str = "utf-8"
) -> None:
    """Write ``content`` to ``path`` atomically, backing up the original once.

    If ``path`` already exists and no ``.eqhelper.bak`` is present yet, copy it
    to the backup first (so the user's pre-edit content is always recoverable).
    New files are written without a backup (nothing to preserve). ``encoding``
    lets callers round-trip files that are not UTF-8 (legacy APO configs are
    often ANSI). Notifies the write observer (if any) after a successful write.
    """
    if path.exists():
        backup = path.with_suffix(path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            try:
                shutil.copy2(path, backup)
            except OSError as exc:
                raise ApoWriteError(f"cannot back up {path}: {exc}") from exc
    _atomic_write_text(path, content, encoding)
    if _write_observer is not None:
        try:
            _write_observer(path, content)
        except Exception:
            pass  # observer must never break a write


def poke(path: Path) -> None:
    """Bump ``path``'s modification time *without touching its content*, so
    Equalizer APO's file-watcher re-reads the whole include tree — picking up
    edits to files APO doesn't directly watch (subfolders / external locations)
    without manually toggling Instant Mode.

    Uses ``os.utime`` (timestamp only): it never reads or rewrites the bytes, so
    it cannot corrupt config.txt — even if another process is mid-write — and it
    can't mangle line endings or non-UTF-8 content. No-op if the file is missing.
    """
    if not path.exists():
        return
    try:
        os.utime(path, None)
    except OSError:
        return


def edit_include_file(path: Path, transform: Callable[[str], str]) -> bool:
    """Read ``path``, apply ``transform`` to its text, and write back (atomically,
    with one-shot backup) only if the result differs. Returns whether it changed.
    Raises :class:`ApoWriteError` if the file is missing/unreadable."""
    if not path.exists():
        raise ApoWriteError(f"file not found: {path}")
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ApoWriteError(f"cannot read {path}: {exc}") from exc
    new_text = transform(original)
    if new_text == original:
        return False
    atomic_write_with_backup(path, new_text)
    return True


def toggle_includes(
    config_path: Path,
    managed_paths: Set[str],
    active_path: Optional[str],
) -> None:
    """Uncomment ``active_path`` and comment the other ``managed_paths`` includes
    in ``config_path`` (global match — see module docstring)."""
    edit_include_file(
        config_path, lambda t: set_active_include(t, managed_paths, active_path)
    )


def _atomic_write_text(target: Path, content: str, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``target`` via tempfile + os.replace, with retries.

    On Windows, ``os.replace`` can fail with ``WinError 5 (Access denied)``
    when Equalizer APO's file-watcher has the target open. A short retry loop
    with back-off resolves this reliably — APO releases its handle within
    a few hundred milliseconds.
    """
    target_dir = target.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target_dir),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
        last_exc: Optional[OSError] = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                os.replace(tmp_path, target)
                return
            except OSError as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS - 1:
                    time.sleep(_RETRY_DELAY_S * (attempt + 1))
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise ApoWriteError(f"failed to write {target}: {last_exc}") from last_exc
    except ApoWriteError:
        raise
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise ApoWriteError(f"failed to write {target}: {exc}") from exc
