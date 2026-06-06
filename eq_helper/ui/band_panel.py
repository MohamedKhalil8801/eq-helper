"""Band-isolation toggle + fc/bw numeric readouts, bound to the spectrum region."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from eq_helper.audio.engine import AudioEngine


class BandPanel(QGroupBox):
    """Toggle + numeric spinboxes that mirror the LinearRegionItem on the graph."""

    bandValueChanged = Signal(float, float)  # (fc, bw) user-initiated

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__("Frequency isolation (band-pass)", parent)
        self._engine = engine

        layout = QVBoxLayout(self)

        self.enable_check = QCheckBox("Isolate band")
        layout.addWidget(self.enable_check)

        row = QHBoxLayout()
        row.addWidget(QLabel("Center:"))
        self.fc_spin = QDoubleSpinBox()
        self.fc_spin.setRange(20.0, 20000.0)
        self.fc_spin.setValue(1000.0)
        self.fc_spin.setSuffix(" Hz")
        self.fc_spin.setDecimals(1)
        self.fc_spin.setSingleStep(10.0)
        row.addWidget(self.fc_spin)

        row.addWidget(QLabel("Bandwidth:"))
        self.bw_spin = QDoubleSpinBox()
        self.bw_spin.setRange(1.0, 20000.0)
        self.bw_spin.setValue(500.0)
        self.bw_spin.setSuffix(" Hz")
        self.bw_spin.setDecimals(1)
        self.bw_spin.setSingleStep(10.0)
        row.addWidget(self.bw_spin)
        layout.addLayout(row)

        self.enable_check.toggled.connect(self._on_enable_toggled)
        self.fc_spin.valueChanged.connect(self._on_spin_changed)
        self.bw_spin.valueChanged.connect(self._on_spin_changed)

    # -------- state

    def _on_enable_toggled(self, checked: bool) -> None:
        if checked:
            self._engine.set_band_filter(
                fc=self.fc_spin.value(),
                bw=self.bw_spin.value(),
                enabled=True,
            )
        else:
            self._engine.set_band_enabled(False)
        self.bandValueChanged.emit(self.fc_spin.value(), self.bw_spin.value())

    def _on_spin_changed(self, _val: float) -> None:
        self._engine.set_band_filter(
            fc=self.fc_spin.value(),
            bw=self.bw_spin.value(),
            enabled=self.enable_check.isChecked(),
        )
        self.bandValueChanged.emit(self.fc_spin.value(), self.bw_spin.value())

    def set_from_region(self, fc: float, bw: float) -> None:
        """Called when the user drags the plot's LinearRegionItem."""
        self.fc_spin.blockSignals(True)
        self.bw_spin.blockSignals(True)
        self.fc_spin.setValue(max(20.0, min(20000.0, fc)))
        self.bw_spin.setValue(max(1.0, min(20000.0, bw)))
        self.fc_spin.blockSignals(False)
        self.bw_spin.blockSignals(False)
        if self.enable_check.isChecked():
            self._engine.set_band_filter(
                fc=self.fc_spin.value(),
                bw=self.bw_spin.value(),
                enabled=True,
            )

    # -------- settings

    def apply_settings(self, settings: dict) -> None:
        self.fc_spin.setValue(float(settings.get("band_fc_hz", 1000.0)))
        self.bw_spin.setValue(float(settings.get("band_bw_hz", 500.0)))
        self.enable_check.setChecked(bool(settings.get("band_enabled", False)))

    def collect_settings(self, settings: dict) -> None:
        settings["band_fc_hz"] = self.fc_spin.value()
        settings["band_bw_hz"] = self.bw_spin.value()
        settings["band_enabled"] = self.enable_check.isChecked()
