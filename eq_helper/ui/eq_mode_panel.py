"""EQ editor: an interactive parametric-EQ filter list bound to ONE APO EQ file.

Selecting an EQ file (from the Profiles panel) loads its filters + preamp +
graphic EQs; edits are written straight back to that file via a debounced,
atomic, backed write, so Equalizer APO live-reloads and the change is audible.
When no file is selected the editor is empty/disabled.

The app's in-app sosfilt EQ chain is intentionally NOT used: all EQ lives in APO
files and APO processes the system audio (including the app's own tones), so
applying it again in-app would double it.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from eq_helper.apo.errors import ApoWriteError
from eq_helper.apo.writer import atomic_write_with_backup
from eq_helper.audio.engine import AudioEngine
from eq_helper.dsp.eq_chain import (
    EQFilter,
    compute_chain_response,
    filters_to_apo_text,
    read_apo_file_full,
)
from eq_helper.dsp.graphic_eq import GraphicEQ

_EQ_FREQS = np.geomspace(20.0, 20000.0, 500)
_FILTER_TYPES = ["PK", "LS", "HS", "LP", "HP"]


class _PrecisionSpinBox(QDoubleSpinBox):
    """A DoubleSpinBox that uses a finer step when the CTRL key is held."""

    def __init__(self, *args, ctrl_step_ratio: float = 0.2, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ctrl_step_ratio = ctrl_step_ratio

    def stepBy(self, steps: int) -> None:
        mods = QGuiApplication.keyboardModifiers()
        if mods & Qt.ControlModifier:
            old_step = self.singleStep()
            try:
                self.setSingleStep(old_step * self._ctrl_step_ratio)
                super().stepBy(steps)
            finally:
                self.setSingleStep(old_step)
        else:
            super().stepBy(steps)


class _FilterRow(QWidget):
    """One row in the filter list: type, freq, gain, Q, enable, remove."""

    changed = Signal()
    removeClicked = Signal()

    def __init__(self, filt: EQFilter, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.type_combo = QComboBox()
        self.type_combo.addItems(_FILTER_TYPES)
        self.type_combo.setCurrentText(filt.filter_type)
        self.type_combo.setFixedWidth(55)
        layout.addWidget(self.type_combo)

        self.fc_spin = _PrecisionSpinBox()
        self.fc_spin.setRange(20.0, 20000.0)
        self.fc_spin.setDecimals(2)
        self.fc_spin.setValue(filt.fc_hz)
        self.fc_spin.setSuffix(" Hz")
        self.fc_spin.setSingleStep(10.0)
        layout.addWidget(self.fc_spin)

        self.gain_spin = _PrecisionSpinBox()
        self.gain_spin.setRange(-30.0, 30.0)
        self.gain_spin.setDecimals(4)
        self.gain_spin.setValue(filt.gain_db)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setSingleStep(0.5)
        layout.addWidget(self.gain_spin)

        self.q_spin = _PrecisionSpinBox()
        self.q_spin.setRange(0.01, 30.0)
        self.q_spin.setDecimals(4)
        self.q_spin.setValue(filt.q)
        self.q_spin.setPrefix("Q ")
        self.q_spin.setSingleStep(0.1)
        layout.addWidget(self.q_spin)

        self.enable_check = QCheckBox()
        self.enable_check.setChecked(filt.enabled)
        self.enable_check.setToolTip("Enable/disable this filter")
        layout.addWidget(self.enable_check)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedWidth(24)
        self.remove_btn.setToolTip("Remove filter")
        layout.addWidget(self.remove_btn)

        self.type_combo.currentTextChanged.connect(lambda _: self.changed.emit())
        self.fc_spin.valueChanged.connect(lambda _: self.changed.emit())
        self.gain_spin.valueChanged.connect(lambda _: self.changed.emit())
        self.q_spin.valueChanged.connect(lambda _: self.changed.emit())
        self.enable_check.toggled.connect(lambda _: self.changed.emit())
        self.remove_btn.clicked.connect(self.removeClicked.emit)

    def to_filter(self) -> EQFilter:
        return EQFilter(
            filter_type=self.type_combo.currentText(),
            fc_hz=self.fc_spin.value(),
            gain_db=self.gain_spin.value(),
            q=self.q_spin.value(),
            enabled=self.enable_check.isChecked(),
        )

    def update_from_filter(self, f: EQFilter) -> None:
        """Update widgets without emitting changed signals."""
        self.blockSignals(True)
        self.type_combo.setCurrentText(f.filter_type)
        self.fc_spin.setValue(f.fc_hz)
        self.gain_spin.setValue(f.gain_db)
        self.q_spin.setValue(f.q)
        self.enable_check.setChecked(f.enabled)
        self.blockSignals(False)


class EQModePanel(QGroupBox):
    """Parametric EQ filter list bound to a single APO EQ file."""

    # (eq_response_freqs, eq_response_db) for graph display
    eqResponseChanged = Signal(object, object)
    # Emitted when the filter list changes (for graph dot sync)
    filtersChanged = Signal(list)

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__("EQ Filters", parent)
        self._engine = engine
        self._eq_file_path: Optional[str] = None
        self._preamp_db: float = 0.0
        self._rows: List[_FilterRow] = []
        self._graphic_eqs: List[GraphicEQ] = []

        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("+ Add")
        self.btn_clear = QPushButton("Clear")
        self.btn_add.clicked.connect(self._on_add)
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._hint = QLabel("Select an EQ file in Profiles to edit it.")
        self._hint.setStyleSheet("color: #999; font-style: italic;")
        layout.addWidget(self._hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._filter_container = QWidget()
        self._filter_layout = QVBoxLayout(self._filter_container)
        self._filter_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_layout.addStretch()
        self._scroll.setWidget(self._filter_container)
        layout.addWidget(self._scroll, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._auto_save)

        self._set_editing_enabled(False)

    # ------------------------------------------------------------- file

    def set_eq_file(self, path: Optional[str]) -> None:
        """Bind the editor to an EQ file (or clear it when ``path`` is None).

        Flushes any pending save for the previously-bound file first.
        """
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._auto_save()

        if not path:
            self._eq_file_path = None
            self._preamp_db = 0.0
            self._graphic_eqs = []
            self._rebuild_rows([])
            self._set_editing_enabled(False)
            self._emit_response()
            self.filtersChanged.emit([])
            return

        self._eq_file_path = str(path)
        filters, geqs, preamp = read_apo_file_full(Path(path))
        self._preamp_db = preamp
        self._graphic_eqs = geqs
        self._rebuild_rows(filters)
        self._set_editing_enabled(True)
        self._emit_response()
        self.filtersChanged.emit(self.filters())

    def eq_file(self) -> Optional[str]:
        return self._eq_file_path

    def has_pending_save(self) -> bool:
        """True if a debounced save is queued (the user just edited)."""
        return self._save_timer.isActive()

    # ------------------------------------------------------------ graphic EQ

    def graphic_eqs(self) -> List[GraphicEQ]:
        return list(self._graphic_eqs)

    def set_graphic_eqs(self, geqs: List[GraphicEQ]) -> None:
        """Update graphic EQs (from the graphic-EQ panel) and persist."""
        if self._eq_file_path is None:
            return
        self._graphic_eqs = list(geqs)
        self._on_any_changed()

    # ------------------------------------------------------------- filters

    def filters(self) -> List[EQFilter]:
        return [row.to_filter() for row in self._rows]

    def add_filter(self, f: EQFilter) -> None:
        """Add a filter programmatically (e.g. from a Ctrl+click on the graph)."""
        if self._eq_file_path is None:
            return
        self._add_row(f)
        self._on_any_changed()

    def update_filter(self, index: int, f: EQFilter) -> None:
        if self._eq_file_path is not None and 0 <= index < len(self._rows):
            self._rows[index].update_from_filter(f)
            self._on_any_changed()

    # ----------------------------------------------------------- internal

    def _set_editing_enabled(self, enabled: bool) -> None:
        self.btn_add.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)
        self._scroll.setEnabled(enabled)
        self._hint.setVisible(not enabled)

    def _rebuild_rows(self, filters: List[EQFilter]) -> None:
        for row in self._rows:
            self._filter_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        for f in filters:
            self._add_row(f)

    def _add_row(self, f: EQFilter) -> None:
        row = _FilterRow(f)
        row.changed.connect(self._on_any_changed)
        row.removeClicked.connect(lambda r=row: self._on_remove(r))
        self._filter_layout.insertWidget(self._filter_layout.count() - 1, row)
        self._rows.append(row)

    def _on_remove(self, row: _FilterRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self._filter_layout.removeWidget(row)
            row.deleteLater()
            self._on_any_changed()

    def _on_any_changed(self) -> None:
        self._emit_response()
        self._save_timer.start()
        self.filtersChanged.emit(self.filters())

    def _emit_response(self) -> None:
        filters = self.filters()
        if not filters:
            self.eqResponseChanged.emit(None, None)
            return
        db = compute_chain_response(filters, _EQ_FREQS, self._engine.sample_rate)
        self.eqResponseChanged.emit(_EQ_FREQS, db)

    def _auto_save(self) -> None:
        if not self._eq_file_path:
            return
        text = filters_to_apo_text(self.filters(), self._preamp_db, self._graphic_eqs)
        try:
            atomic_write_with_backup(Path(self._eq_file_path), text)
        except ApoWriteError:
            # Non-fatal: a later edit/save will retry.
            pass

    # ------------------------------------------------------------- actions

    def _on_add(self) -> None:
        self.add_filter(EQFilter())

    def _on_clear(self) -> None:
        self._rebuild_rows([])
        self._on_any_changed()

    def cleanup(self) -> None:
        self._save_timer.stop()
        self._auto_save()
