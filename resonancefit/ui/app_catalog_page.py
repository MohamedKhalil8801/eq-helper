""""Where can I use my EQ?" — the same app cards as the export flow, to browse."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .app_cards import AppCard, ListeningPicker, listening_label
from .common import heading, hint, tr


class AppCatalogPage(QWidget):
    exportRequested = Signal()

    def __init__(self, catalog, platform: str, installed=frozenset(), parent=None):
        super().__init__(parent)
        self.catalog, self.platform = catalog, platform
        self.installed = set(installed)
        self.cards: dict[str, AppCard] = {}
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(hint(tr("Your EQ works with many apps on computers, phones and hardware. "
                              "Pick where you listen to see what fits.")), 1)
        export = QPushButton(tr("Export for my EQ app…"))
        export.setObjectName("primary")
        export.clicked.connect(self.exportRequested)
        top.addWidget(export)
        layout.addLayout(top)
        self.picker = ListeningPicker(platform)
        self.picker.chosen.connect(self._show)
        layout.addWidget(self.picker)
        self.apps_heading = heading("", 3)
        layout.addWidget(self.apps_heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.scroll, 1)
        self.picker.choose("this_computer")

    def set_installed(self, installed) -> None:
        self.installed = set(installed)
        self._show(*self.picker.current())

    def choose(self, where: str, os_code: str | None = None) -> None:
        self.picker.choose(where, os_code)

    def card_ids(self) -> list[str]:
        return list(self.cards)

    def _show(self, where: str, os_code: str) -> None:
        installed = self.installed if where == "this_computer" else set()
        apps = self.catalog.for_listening(where, os_code or None, installed)
        self.apps_heading.setText(tr("Apps for: {where}").format(where=listening_label(where, os_code)))
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 8, 0)
        self.cards = {}
        for app in apps:
            card = AppCard(app, app.id in installed, can_export=False)
            layout.addWidget(card)
            self.cards[app.id] = card
        layout.addStretch(1)
        self.scroll.setWidget(holder)
