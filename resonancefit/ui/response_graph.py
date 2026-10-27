"""Frequency-response graph for the customer: Your IEM / Target / With your EQ.

Log-frequency x axis with plain Hz/kHz ticks, "Loudness" on y, and two
draggable markers: the primary resonance and (when the upper treble is being
adjusted) the upper resonance. Hovering shows the frequency under the mouse;
pressing and dragging on the graph plays a tone that follows the mouse, as in
EQ Helper.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .common import COLOR_IEM, COLOR_MARKER, COLOR_TARGET, COLOR_UPPER, COLOR_WITH_EQ, tr

_TICKS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
F_MIN, F_MAX = 20.0, 20000.0
DISPLAY_POINTS = 1600  # ≈ 1/160 octave: finer than the screen, far lighter than a 55k-point measurement


def thin_for_display(freqs, db, points: int = DISPLAY_POINTS):
    """Resample a dense curve onto a log-spaced grid for drawing (the data itself is untouched)."""
    freqs, db = np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)
    if freqs.size <= points * 1.5:
        return freqs, db
    lo, hi = max(freqs[0], F_MIN * 0.9), min(freqs[-1], F_MAX * 1.1)
    grid = np.geomspace(lo, hi, points)
    return grid, np.interp(np.log(grid), np.log(freqs), db)


def hz_label(hz: float) -> str:
    return f"{hz / 1000:g}k" if hz >= 1000 else f"{hz:g}"


def hz_text(hz: float) -> str:
    """The hover readout: whole Hz with a thousands separator ("8,120 Hz")."""
    return f"{hz:,.0f} Hz" if hz >= 100 else f"{hz:.1f} Hz"


def _marker(color: str, label: str, label_left: bool = False, height: float = 0.95) -> pg.InfiniteLine:
    """A draggable resonance line; its name sits near the top, beside the line."""
    anchor = (1, 0.5) if label_left else (0, 0.5)
    line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(color, width=2),
                           hoverPen=pg.mkPen(color, width=4),
                           label=label, labelOpts={"position": height, "color": color, "movable": False,
                                                   "anchors": [anchor, anchor]})
    line.setZValue(10)
    line.hide()
    return line


class ResponseGraph(QWidget):
    markerMoved = Signal(float)        # Hz, while dragging the resonance line
    markerReleased = Signal(float)     # Hz, drag finished
    upperMoved = Signal(float)         # Hz, while dragging the upper-resonance line
    frequencyClicked = Signal(float)   # Hz: pressed on the graph, and again as the mouse drags
    frequencyDragFinished = Signal()
    hoverChanged = Signal(object)      # Hz under the mouse, or None when it leaves

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
        self.plot.setXRange(math.log10(F_MIN), math.log10(F_MAX), padding=0)
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
                pen.setStyle(Qt.DashLine)
            self._curves[key] = self.plot.plot([], [], pen=pen, name=name)
        self._curves["target"].setZValue(5)  # dashed target stays visible over a matching EQ curve

        # hover readout: a thin line under the mouse and its frequency at the top
        self.crosshair = pg.InfiniteLine(angle=90, movable=False)
        self.crosshair.setZValue(8)
        self.crosshair.hide()
        self.plot.addItem(self.crosshair, ignoreBounds=True)
        self.hover_label = pg.TextItem(anchor=(0.5, 1))  # at the bottom: the line names are at the top
        self.hover_label.setZValue(20)
        self.hover_label.hide()
        self.plot.addItem(self.hover_label, ignoreBounds=True)
        self.hover_hz: float | None = None

        # what's playing now (tone or sweep position)
        self.playing_line = pg.InfiniteLine(angle=90, movable=False, label="",
                                            labelOpts={"position": 0.79, "movable": False,
                                                       "anchors": [(0, 0.5), (0, 0.5)]})
        self.playing_line.setZValue(9)
        self.playing_line.hide()
        self.plot.addItem(self.playing_line, ignoreBounds=True)
        self.playing_hz: float | None = None

        self.marker = _marker(COLOR_MARKER, tr("Resonance"))
        self.plot.addItem(self.marker)
        self.marker.sigPositionChanged.connect(lambda line: self.markerMoved.emit(10 ** line.value()))
        self.marker.sigPositionChangeFinished.connect(lambda line: self.markerReleased.emit(10 ** line.value()))
        self.upper_marker = _marker(COLOR_UPPER, tr("Upper"), label_left=True, height=0.87)
        self.plot.addItem(self.upper_marker)
        self.upper_marker.sigPositionChanged.connect(lambda line: self.upperMoved.emit(10 ** line.value()))
        self._apply_palette()

        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self.plot.viewport().installEventFilter(self)
        self._listening = False
        self._install_press_and_drag()
        layout.addWidget(self.plot)
        self.setAccessibleName(tr("Frequency response graph"))
        self.setToolTip(tr("Click or drag on the graph to hear a tone at that frequency. "
                           "Drag the coloured lines to move your resonance."))

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
        faint = pal.color(QPalette.Text)
        faint.setAlpha(110)
        self.crosshair.setPen(pg.mkPen(faint, width=1, style=Qt.DashLine))
        self.hover_label.setColor(text)
        fill = pal.color(QPalette.Base)
        fill.setAlpha(220)
        self.hover_label.fill = pg.mkBrush(fill)
        playing = pal.color(QPalette.Highlight)
        self.playing_line.setPen(pg.mkPen(playing, width=2, style=Qt.DotLine))
        self.playing_line.label.setColor(playing)

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._apply_palette()
        super().changeEvent(event)

    # ── data ───────────────────────────────────────────────────────────────
    def set_curve(self, key: str, freqs, db) -> None:
        if freqs is None:
            self._curves[key].setData([], [])
        else:
            self._curves[key].setData(*thin_for_display(freqs, db))

    def curve_names(self) -> list[str]:
        return [item.name() for item in self._curves.values()]

    def curve_data(self, key: str):
        return self._curves[key].getData()

    # ── markers ────────────────────────────────────────────────────────────
    def set_marker_bounds(self, lo: float, hi: float) -> None:
        self.marker.setBounds((math.log10(lo), math.log10(hi)))

    def set_upper_bounds(self, lo: float, hi: float) -> None:
        self.upper_marker.setBounds((math.log10(lo), math.log10(hi)))

    @staticmethod
    def _place(line: pg.InfiniteLine, hz: float | None) -> None:
        if hz is None:
            line.hide()
            return
        line.blockSignals(True)
        line.setValue(math.log10(hz))
        line.blockSignals(False)
        line.show()

    def set_marker(self, hz: float | None) -> None:
        self._place(self.marker, hz)

    def set_upper_marker(self, hz: float | None) -> None:
        self._place(self.upper_marker, hz)

    def marker_hz(self) -> float:
        return 10 ** self.marker.value()

    def upper_marker_hz(self) -> float:
        return 10 ** self.upper_marker.value()

    def set_playing(self, hz: float | None) -> None:
        """Show the frequency that's playing (a dotted line labelled "▶ 7,700 Hz")."""
        self.playing_hz = hz
        if hz is None or not F_MIN <= hz <= F_MAX:
            self.playing_line.hide()
            return
        self.playing_line.setValue(math.log10(hz))
        self.playing_line.label.setFormat("▶ " + hz_text(hz))
        self.playing_line.show()

    # ── hover ──────────────────────────────────────────────────────────────
    def _view_hz(self, scene_pos) -> float | None:
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(scene_pos):
            return None
        hz = 10 ** vb.mapSceneToView(scene_pos).x()
        return hz if F_MIN <= hz <= F_MAX else None

    def _mouse_moved(self, scene_pos) -> None:
        self.show_hover(self._view_hz(scene_pos))

    def show_hover(self, hz: float | None) -> None:
        self.hover_hz = hz
        if hz is None:
            self.crosshair.hide()
            self.hover_label.hide()
        else:
            x = math.log10(hz)
            self.crosshair.setValue(x)
            self.crosshair.show()
            bottom = self.plot.getPlotItem().vb.viewRange()[1][0]
            self.hover_label.setText(hz_text(hz))
            self.hover_label.setPos(x, bottom)
            self.hover_label.show()
        self.hoverChanged.emit(hz)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Leave:
            self.show_hover(None)
        return super().eventFilter(obj, event)

    # ── press and drag to listen ───────────────────────────────────────────
    def _on_marker(self, scene_pos) -> bool:
        for item in self.plot.scene().items(scene_pos):
            parent = item
            while parent is not None:
                if parent in (self.marker, self.upper_marker) and parent.isVisible():
                    return True
                if isinstance(parent, pg.LegendItem):
                    return True
                parent = parent.parentItem()
        return False

    def _install_press_and_drag(self) -> None:
        """A single press plays a tone at once; dragging moves it (no double-click needed)."""
        vb = self.plot.getPlotItem().vb
        orig_press, orig_move = vb.mousePressEvent, vb.mouseMoveEvent
        orig_release, orig_click = vb.mouseReleaseEvent, vb.mouseClickEvent

        def press(ev):
            if ev.button() != Qt.LeftButton or self._on_marker(ev.scenePos()):
                orig_press(ev)
                return
            hz = self._view_hz(ev.scenePos())
            if hz is None:
                orig_press(ev)
                return
            self._listening = True
            self.frequencyClicked.emit(hz)
            ev.accept()

        def move(ev):
            if self._listening:
                hz = self._view_hz(ev.scenePos())
                if hz is not None:
                    self.frequencyClicked.emit(hz)
                    self.show_hover(hz)
                ev.accept()
                return
            orig_move(ev)

        def release(ev):
            if self._listening and ev.button() == Qt.LeftButton:
                self._listening = False
                self.frequencyDragFinished.emit()
                ev.accept()
                return
            orig_release(ev)

        def click(ev):
            if ev.button() == Qt.LeftButton and not self._on_marker(ev.scenePos()):
                ev.accept()  # handled on press
                return
            orig_click(ev)

        vb.mousePressEvent, vb.mouseMoveEvent = press, move
        vb.mouseReleaseEvent, vb.mouseClickEvent = release, click
