"""④ Listen & save: in-app EQ on/off, save a tuning, export, apply to APO."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

from .common import Card, hint, tr


class ListenSaveCard(Card):
    saveRequested = Signal(str)   # note
    exportRequested = Signal()
    applyRequested = Signal()
    eqToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(tr("④ Listen & save"), parent)
        self.body.addWidget(hint(tr(
            "Play a tone, a sweep or one of your test songs and switch the EQ on and off to compare.")))
        self.eq_toggle = QPushButton(tr("EQ is off — click to turn on"))
        self.eq_toggle.setCheckable(True)
        self.eq_toggle.setMinimumHeight(34)
        self.eq_toggle.toggled.connect(self._toggled)
        self.body.addWidget(self.eq_toggle)

        row = QHBoxLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText(tr("Note (optional)"))
        self.note.setAccessibleName(tr("Note for this tuning"))
        self.note.returnPressed.connect(self._save)
        row.addWidget(self.note, 1)
        self.save_button = QPushButton(tr("Save tuning"))
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        self.body.addLayout(row)

        self.apply_button = QPushButton(tr("Apply to Equalizer APO"))
        self.apply_button.setObjectName("primary")
        self.apply_button.setToolTip(tr("Use this tuning for everything you play on this computer."))
        self.apply_button.clicked.connect(self.applyRequested)
        self.apply_button.hide()
        self.body.addWidget(self.apply_button)
        self.export_button = QPushButton(tr("Export for my EQ app…"))
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.exportRequested)
        self.body.addWidget(self.export_button)
        self.status = hint("")
        self.body.addWidget(self.status)

    def set_apply_available(self, available: bool) -> None:
        self.apply_button.setVisible(available)

    def set_eq_on(self, on: bool) -> None:
        self.eq_toggle.setChecked(on)

    def _toggled(self, on: bool) -> None:
        self.eq_toggle.setText(tr("EQ is on — click to compare without it") if on
                               else tr("EQ is off — click to turn on"))
        self.eqToggled.emit(on)

    def _save(self) -> None:
        self.saveRequested.emit(self.note.text().strip())
        self.note.clear()

    def show_status(self, text: str) -> None:
        self.status.setText(text)
