"""Two-way live sync between the app and Equalizer APO.

App → APO: after the app writes any file other than ``config.txt`` (an EQ file
or preset file), it pokes ``config.txt`` (re-saves it unchanged) so APO re-reads
the whole include tree — picking up edits to files it doesn't directly watch
(subfolders / external locations), without manually toggling Instant Mode.

APO/external → App: a ``QFileSystemWatcher`` watches ``config.txt`` and the
currently-open EQ file; a genuine external change emits :attr:`externalChange`.

Self-write suppression: every app write flows through
``writer.atomic_write_with_backup``, whose observer records each file's content
hash here. When the watcher fires, the file is re-hashed; if it matches what the
app last wrote (including its own poke), the event is ignored — no feedback loop.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable, Dict, Optional

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from eq_helper.apo import writer


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _hash(data: bytes) -> str:
    """Hash raw bytes so write-time and read-back hashes match exactly,
    regardless of line endings (CRLF vs LF)."""
    return hashlib.sha1(data).hexdigest()


class ApoBridge(QObject):
    externalChange = Signal(str)  # absolute path of a file changed outside the app

    _POKE_MS = 200
    _RELOAD_MS = 300

    def __init__(
        self,
        get_config_path: Callable[[], Optional[Path]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._get_config_path = get_config_path
        self._hashes: Dict[str, str] = {}
        self._pending: Dict[str, str] = {}  # key -> path awaiting external-change emit

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        self._poke_timer = QTimer(self)
        self._poke_timer.setSingleShot(True)
        self._poke_timer.setInterval(self._POKE_MS)
        self._poke_timer.timeout.connect(self._do_poke)

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(self._RELOAD_MS)
        self._reload_timer.timeout.connect(self._flush)

        writer.set_write_observer(self._on_app_wrote)

    # ----------------------------------------------------- app → APO (poke)

    def _on_app_wrote(self, path, content: str) -> None:
        """Record the hash of every app write; schedule a config.txt poke for
        writes to anything other than config.txt itself. The on-disk bytes equal
        ``content.encode('utf-8')`` (the writer uses ``newline=''``), so hashing
        that matches the read-back in :meth:`_flush`."""
        self._hashes[_key(path)] = _hash(content.encode("utf-8"))
        cfg = self._get_config_path()
        if cfg is not None and _key(path) != _key(cfg):
            self._poke_timer.start()

    def _do_poke(self) -> None:
        cfg = self._get_config_path()
        if cfg is None or not Path(cfg).exists():
            return
        key = _key(cfg)
        if key in self._pending:
            return  # an external config.txt change is pending — don't mask it
        # Record config.txt's current hash so our mtime bump isn't mistaken for
        # an external change (the poke itself never alters the bytes).
        try:
            self._hashes[key] = _hash(Path(cfg).read_bytes())
        except OSError:
            return
        writer.poke(Path(cfg))

    # ----------------------------------------------------- watching (→ app)

    def set_watched(self, paths) -> None:
        """Watch exactly ``paths`` (existing files only), diffing against the
        current set so unchanged paths keep an uninterrupted watch."""
        desired = {str(p) for p in paths if p and Path(p).exists()}
        current = set(self._watcher.files())
        remove = list(current - desired)
        add = list(desired - current)
        if remove:
            self._watcher.removePaths(remove)
        if add:
            self._watcher.addPaths(add)

    def _on_file_changed(self, path: str) -> None:
        # An atomic replace drops the watch; re-add so we keep seeing changes.
        if Path(path).exists() and path not in self._watcher.files():
            self._watcher.addPath(path)
        self._pending[_key(path)] = path
        self._reload_timer.start()

    def _flush(self) -> None:
        pending, self._pending = self._pending, {}
        for key, path in pending.items():
            try:
                data = Path(path).read_bytes()
            except OSError:
                continue
            if _hash(data) == self._hashes.get(key):
                continue  # our own write echoing back — ignore
            self.externalChange.emit(path)

    def cleanup(self) -> None:
        self._poke_timer.stop()
        self._reload_timer.stop()
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)
        if writer._write_observer is self._on_app_wrote:  # type: ignore[attr-defined]
            writer.set_write_observer(None)
