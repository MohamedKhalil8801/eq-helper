"""One running app per user: a second launch hands its files to the first.

Double-clicking a ``.rescal`` while the app is open starts a second process
(Windows, Linux); it forwards the paths over a local socket and exits, and
the running window imports them and comes to the front. On macOS the system
delivers double-clicked files to the running app as ``QFileOpenEvent``
instead — :class:`FileOpenFilter` handles those, including at launch.
"""

from __future__ import annotations

import getpass
import json
import re
from typing import Callable

from PySide6.QtCore import QEvent, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

TIMEOUT_MS = 1500


def server_name(product_stem: str) -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{product_stem}-{user}")


class SingleInstance(QObject):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.server: QLocalServer | None = None
        self._on_message: Callable[[list[str]], None] | None = None

    def forward(self, paths) -> bool:
        """If another instance is running, send it ``paths`` and return True."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(TIMEOUT_MS):
            return False
        socket.write(json.dumps([str(p) for p in paths]).encode("utf-8") + b"\n")
        socket.flush()
        socket.waitForBytesWritten(TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def listen(self, on_message: Callable[[list[str]], None]) -> bool:
        """Become the instance others forward to."""
        self._on_message = on_message
        self.server = QLocalServer(self)
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)  # stale socket from a crashed run
            if not self.server.listen(self.name):
                return False
        self.server.newConnection.connect(self._accept)
        return True

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)

    def _read(self, socket: QLocalSocket) -> None:
        if not socket.canReadLine():
            return
        try:
            paths = json.loads(bytes(socket.readLine()).decode("utf-8"))
        except ValueError:
            return
        if isinstance(paths, list) and self._on_message is not None:
            self._on_message([str(p) for p in paths])


class FileOpenFilter(QObject):
    """macOS: files opened from Finder arrive as QFileOpenEvent on the app."""

    def __init__(self, on_file: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.on_file = on_file

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.FileOpen:
            path = event.file()
            if path:
                self.on_file(path)
                return True
        return super().eventFilter(obj, event)
