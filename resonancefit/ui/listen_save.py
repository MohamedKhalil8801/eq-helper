"""④ Listen & save: in-app EQ on/off, save a tuning, export, apply to APO."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from .common import Card, hint, tr


class ListenSaveCard(Card):
    saveRequested = Signal(str)   # note
    exportRequested = Signal()
    applyRequested = Signal()
    switchRequested = Signal(str)  # history entry id to apply
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
        self.switch_row = QWidget()
        switch = QHBoxLayout(self.switch_row)
        switch.setContentsMargins(0, 0, 0, 0)
        label = QLabel(tr("Switch to:"))
        switch.addWidget(label)
        self.switch_combo = QComboBox()
        self.switch_combo.setAccessibleName(tr("Apply a saved tuning to Equalizer APO"))
        self.switch_combo.setToolTip(tr("Apply one of your saved tunings to Equalizer APO."))
        self.switch_combo.activated.connect(self._switch)
        label.setBuddy(self.switch_combo)
        switch.addWidget(self.switch_combo, 1)
        self.switch_row.hide()
        self.body.addWidget(self.switch_row)
        self.export_button = QPushButton(tr("Export for my EQ app…"))
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.exportRequested)
        self.body.addWidget(self.export_button)
        self.status = hint("")
        self.body.addWidget(self.status)

    def set_apply_available(self, available: bool) -> None:
        self.apply_button.setVisible(available)
        self._apply_available = available
        self.switch_row.setVisible(available and self.switch_combo.count() > 0)

    def set_saved_tunings(self, items) -> None:
        """``items``: [(entry id, label)], newest first."""
        self.switch_combo.blockSignals(True)
        self.switch_combo.clear()
        for entry_id, label in items:
            self.switch_combo.addItem(label, entry_id)
        self.switch_combo.setCurrentIndex(-1)
        self.switch_combo.setPlaceholderText(tr("a saved tuning…"))
        self.switch_combo.blockSignals(False)
        self.switch_row.setVisible(getattr(self, "_apply_available", False) and bool(items))

    def _switch(self, index: int) -> None:
        entry_id = self.switch_combo.itemData(index)
        if entry_id:
            self.switchRequested.emit(entry_id)

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
