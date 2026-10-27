"""Frequency-response graph for the customer: Your IEM / Target / With your EQ.

Log-frequency x axis with plain Hz/kHz ticks, "Loudness" on y, and an
optional draggable marker for the primary resonance.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .common import COLOR_IEM, COLOR_MARKER, COLOR_TARGET, COLOR_WITH_EQ, tr

_TICKS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]


def hz_label(hz: float) -> str:
    return f"{hz / 1000:g}k" if hz >= 1000 else f"{hz:g}"


class ResponseGraph(QWidget):
    markerMoved = Signal(float)      # Hz, while dragging
    markerReleased = Signal(float)   # Hz, drag finished
    frequencyClicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setLogMode(x=True, y=False)
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.setXRange(math.log10(20), math.log10(20000), padding=0)
        self.plot.setYRange(-25, 20, padding=0)
        self.plot.setLabel("left", tr("Loudness (dB)"))
        self.plot.setLabel("bottom", tr("Frequency (Hz)"))
        self.plot.getAxis("bottom").setTicks([[(math.log10(t), hz_label(t)) for t in _TICKS]])
        self.legend = self.plot.addLegend(offset=(10, -10))
        self._curves: dict[str, pg.PlotDataItem] = {}
        for key, name, color, width, style in (
                ("iem", tr("Your IEM"), COLOR_IEM, 2, None),
                ("target", tr("Target"), COLOR_TARGET, 2, "dash"),
                ("with_eq", tr("With your EQ"), COLOR_WITH_EQ, 2.5, None)):
            pen = pg.mkPen(color, width=width)
            if style == "dash":
                from PySide6.QtCore import Qt
                pen.setStyle(Qt.DashLine)
            self._curves[key] = self.plot.plot([], [], pen=pen, name=name)
        self._curves["target"].setZValue(5)  # dashed target stays visible over a matching EQ curve
        self._apply_palette()
        self.marker = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(COLOR_MARKER, width=2),
                                      hoverPen=pg.mkPen(COLOR_MARKER, width=4))
        self.marker.setZValue(10)
        self.plot.addItem(self.marker)
        self.marker.hide()
        self.marker.sigPositionChanged.connect(lambda line: self.markerMoved.emit(10 ** line.value()))
        self.marker.sigPositionChangeFinished.connect(lambda line: self.markerReleased.emit(10 ** line.value()))
        self.plot.scene().sigMouseClicked.connect(self._clicked)
        layout.addWidget(self.plot)
        self.setAccessibleName(tr("Frequency response graph"))

    def _apply_palette(self) -> None:
        """Follow the system light/dark palette instead of pyqtgraph's black."""
        pal = self.palette()
        base, text = pal.color(QPalette.Base), pal.color(QPalette.Text)
        self.plot.setBackground(base)
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(pg.mkPen(text))
            axis.setTextPen(pg.mkPen(text))
        self.legend.setLabelTextColor(text)
        for _sample, label in self.legend.items:
            label.setText(label.text, color=text)

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._apply_palette()
        super().changeEvent(event)

    # ── data ───────────────────────────────────────────────────────────────
    def set_curve(self, key: str, freqs, db) -> None:
        if freqs is None:
            self._curves[key].setData([], [])
        else:
            self._curves[key].setData(np.asarray(freqs, dtype=float), np.asarray(db, dtype=float))

    def curve_names(self) -> list[str]:
        return [item.name() for item in self._curves.values()]

    def curve_data(self, key: str):
        return self._curves[key].getData()

    # ── marker ─────────────────────────────────────────────────────────────
    def set_marker_bounds(self, lo: float, hi: float) -> None:
        self.marker.setBounds((math.log10(lo), math.log10(hi)))

    def set_marker(self, hz: float | None) -> None:
        if hz is None:
            self.marker.hide()
            return
        self.marker.blockSignals(True)
        self.marker.setValue(math.log10(hz))
        self.marker.blockSignals(False)
        self.marker.show()

    def marker_hz(self) -> float:
        return 10 ** self.marker.value()

    def _clicked(self, event) -> None:
        if event.double() or self.marker.mouseHovering:
            return
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(event.scenePos()):
            return
        x = vb.mapSceneToView(event.scenePos()).x()
        self.frequencyClicked.emit(float(10 ** x))
