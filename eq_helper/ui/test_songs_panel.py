"""Test songs panel: manage a list of local audio files and web links.

Audio files are played via the system default player (``QDesktopServices``).
Links open in the default browser. Both are persisted in settings JSON.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_AUDIO_FILTER = (
    "Audio files (*.mp3 *.flac *.wav *.ogg *.opus *.m4a *.aac *.wma *.aiff)"
    ";;All files (*.*)"
)


class TestSongsPanel(QGroupBox):
    """Panel for bookmarking test songs (local files) and reference links."""

    itemsChanged = Signal()  # emitted whenever the list is modified

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Test Songs", parent)
        self._items: List[Dict[str, str]] = []
        # Each item: {"label": str, "path_or_url": str, "type": "file"|"link"}

        layout = QVBoxLayout(self)
        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self.btn_add_file = QPushButton("Add Song\u2026")
        self.btn_add_link = QPushButton("Add Link\u2026")
        self.btn_remove = QPushButton("Remove")
        self.btn_add_file.clicked.connect(self._on_add_file)
        self.btn_add_link.clicked.connect(self._on_add_link)
        self.btn_remove.clicked.connect(self._on_remove)
        btn_row.addWidget(self.btn_add_file)
        btn_row.addWidget(self.btn_add_link)
        btn_row.addWidget(self.btn_remove)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------ settings

    def apply_settings(self, settings: dict) -> None:
        self._items = list(settings.get("test_songs", []))
        self._rebuild_list()

    def collect_settings(self, settings: dict) -> None:
        settings["test_songs"] = list(self._items)

    # -------------------------------------------------------------- build

    def _rebuild_list(self) -> None:
        self._list.clear()
        for entry in self._items:
            kind = entry.get("type", "file")
            label = entry.get("label", "")
            icon = "\U0001f310 " if kind == "link" else "\u266b "
            item = QListWidgetItem(f"{icon}{label}")
            item.setData(Qt.UserRole, entry)
            item.setToolTip(entry.get("path_or_url", ""))
            self._list.addItem(item)

    # ------------------------------------------------------------- actions

    def _on_add_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select test song(s)",
            "",
            _AUDIO_FILTER,
        )
        if not paths:
            return
        existing = {e["path_or_url"] for e in self._items}
        added = False
        for p in paths:
            if p in existing:
                continue
            name = Path(p).stem
            self._items.append({
                "label": name,
                "path_or_url": p,
                "type": "file",
            })
            added = True
        if added:
            self._rebuild_list()
            self.itemsChanged.emit()

    def _on_add_link(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Add link", "URL (opens in default browser):"
        )
        if not ok or not url.strip():
            return
        url = url.strip()
        label, ok2 = QInputDialog.getText(
            self, "Link label", "Display name:", text=url
        )
        if not ok2:
            return
        self._items.append({
            "label": label.strip() or url,
            "path_or_url": url,
            "type": "link",
        })
        self._rebuild_list()
        self.itemsChanged.emit()

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._items):
            self._items.pop(row)
            self._rebuild_list()
            self.itemsChanged.emit()

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        target = entry.get("path_or_url", "")
        if entry.get("type") == "link":
            QDesktopServices.openUrl(QUrl(target))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        menu = QMenu(self)
        play_act = menu.addAction("Open / Play")
        rename_act = menu.addAction("Rename\u2026")
        remove_act = menu.addAction("Remove")
        action = menu.exec(self._list.mapToGlobal(pos))
        if action == play_act:
            self._on_item_activated(item)
        elif action == rename_act:
            self._on_rename(item, entry)
        elif action == remove_act:
            row = self._list.row(item)
            if 0 <= row < len(self._items):
                self._items.pop(row)
                self._rebuild_list()
                self.itemsChanged.emit()

    def _on_rename(self, item: QListWidgetItem, entry: dict) -> None:
        new_label, ok = QInputDialog.getText(
            self, "Rename", "New label:", text=entry.get("label", "")
        )
        if ok and new_label.strip():
            entry["label"] = new_label.strip()
            self._rebuild_list()
            self.itemsChanged.emit()


class TestSongsDialog(QDialog):
    """A pop-up host for :class:`TestSongsPanel`.

    The panel is reused as-is; its group-box title is cleared so the dialog's
    own title bar is the only "Test Songs" heading (no double title).
    """

    def __init__(
        self, panel: TestSongsPanel, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test Songs")
        self.resize(420, 380)
        panel.setTitle("")  # drop the redundant inner heading
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(panel)
