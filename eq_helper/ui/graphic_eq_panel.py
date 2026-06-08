"""Graphic EQ panel: editable frequency-gain curve with linked x-axis.

A pyqtgraph PlotWidget where the user can add, drag, and remove band points.
The x-axis is linked to the spectrum view so zooming/panning is synchronized.
Supports both variable (freeform) and fixed (ISO octave) band modes.
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from eq_helper.dsp.graphic_eq import (
    GraphicEQ,
    GraphicEQBand,
    graphic_eq_response,
    graphic_eq_to_apo_text,
    double_bands,
    make_fixed_bands,
    parse_graphic_eq_line,
    region_for_freq,
)

_LOG_F_MIN = math.log10(20.0)
_LOG_F_MAX = math.log10(20000.0)
_RESPONSE_FREQS = np.geomspace(20.0, 20000.0, 500)


class GraphicEQPanel(QWidget):
    """Editable graphic EQ with linked x-axis to the spectrum view."""

    # Emitted whenever bands change: (list_of_GraphicEQ,)
    graphicEqChanged = Signal(list)
    # Audition mode: clicking a band point asks to hear the region it influences.
    auditionIsolateRequested = Signal(float, float)  # (lo_hz, hi_hz) — plain click
    auditionSweepRequested = Signal(float, float)     # (lo_hz, hi_hz) — Shift+click
    auditionModeChanged = Signal(bool)
    auditionNoiseChanged = Signal(str)  # "pink" | "grey"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._geqs: List[GraphicEQ] = []
        self._current_idx: int = 0
        self._dragging_band: int = -1
        self._fixed_mode: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Mode:"))
        self._radio_var = QRadioButton("Variable")
        self._radio_fixed = QRadioButton("Fixed 8")
        self._radio_var.setChecked(True)
        self._radio_var.toggled.connect(self._on_mode_changed)
        toolbar.addWidget(self._radio_var)
        toolbar.addWidget(self._radio_fixed)

        self._btn_double_bands = QPushButton("Double Bands")
        self._btn_double_bands.setEnabled(False)
        self._btn_double_bands.clicked.connect(self._on_double_bands)
        toolbar.addWidget(self._btn_double_bands)

        toolbar.addWidget(QLabel("  GEQ:"))
        self._geq_combo = QComboBox()
        self._geq_combo.currentIndexChanged.connect(self._on_geq_selected)
        toolbar.addWidget(self._geq_combo)

        self._btn_add_geq = QPushButton("+")
        self._btn_add_geq.setToolTip("Add a new Graphic EQ")
        self._btn_add_geq.setFixedWidth(28)
        self._btn_add_geq.clicked.connect(self._on_add_geq)
        toolbar.addWidget(self._btn_add_geq)

        self._btn_remove_geq = QPushButton("\u00d7")
        self._btn_remove_geq.setToolTip("Remove current Graphic EQ")
        self._btn_remove_geq.setFixedWidth(28)
        self._btn_remove_geq.clicked.connect(self._on_remove_geq)
        toolbar.addWidget(self._btn_remove_geq)

        toolbar.addStretch()

        # Audition: click a band point to hear the region it influences.
        self._audition_check = QCheckBox("Audition region")
        self._audition_check.setToolTip(
            "Click a band point to isolate its region (Shift+click to sweep it)"
        )
        self._audition_check.toggled.connect(self._on_audition_toggled)
        toolbar.addWidget(self._audition_check)
        self._audition_noise = QComboBox()
        self._audition_noise.addItems(["Pink", "Grey"])
        self._audition_noise.setToolTip(
            "Noise auto-played when auditioning a region (only if nothing is playing)"
        )
        self._audition_noise.setEnabled(False)
        self._audition_noise.currentTextChanged.connect(
            lambda t: self.auditionNoiseChanged.emit(t.lower())
        )
        toolbar.addWidget(self._audition_noise)

        layout.addLayout(toolbar)

        # Plot + text editor in a horizontal splitter.
        from eq_helper.ui.spectrum_view import LogFrequencyAxis
        freq_axis = LogFrequencyAxis(orientation="bottom")
        self._plot = pg.PlotWidget(axisItems={"bottom": freq_axis})
        self._plot.setLogMode(x=True, y=False)
        self._plot.setLabel("bottom", "Frequency (Hz)")
        self._plot.setLabel("left", "Gain (dB)")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        _XPAD = 0.03
        vb = self._plot.getViewBox()
        vb.setLimits(
            xMin=_LOG_F_MIN - _XPAD,
            xMax=_LOG_F_MAX + _XPAD,
            yMin=-20.0,
            yMax=20.0,
        )
        self._plot.setXRange(_LOG_F_MIN - _XPAD, _LOG_F_MAX + _XPAD, padding=0)
        self._plot.setYRange(-15.0, 15.0, padding=0)
        self._plot.disableAutoRange()

        # Zero-line
        self._plot.addItem(pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen(color=(100, 100, 100, 150), width=1),
        ))

        # Interpolation curve
        self._curve = self._plot.plot(
            pen=pg.mkPen(color=(255, 200, 50), width=2),
        )

        # Dots
        self._dots = pg.ScatterPlotItem(
            size=10,
            pen=pg.mkPen(color=(255, 255, 255), width=2),
            brush=pg.mkBrush(255, 200, 50, 200),
        )
        self._plot.addItem(self._dots)

        # Text editor for manual band editing (APO format).
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText(
            "GraphicEQ: 100 3; 1000 0; 8000 -2\n"
            "(edit bands manually, one GraphicEQ: line)"
        )
        self._text_edit.setMaximumWidth(280)
        self._text_edit.textChanged.connect(self._on_text_changed)
        self._text_updating = False  # prevent feedback loops

        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.addWidget(self._plot)
        content_splitter.addWidget(self._text_edit)
        content_splitter.setStretchFactor(0, 3)
        content_splitter.setStretchFactor(1, 1)
        layout.addWidget(content_splitter, 1)

        # Mouse interaction on the plot.
        self._orig_press = vb.mousePressEvent
        self._orig_move = vb.mouseMoveEvent
        self._orig_release = vb.mouseReleaseEvent
        self._orig_click = vb.mouseClickEvent

        def _press(ev):
            if ev.button() == Qt.LeftButton:
                mods = ev.modifiers()
                shift = bool(mods & Qt.ShiftModifier)
                plain = mods == Qt.NoModifier
                if plain or shift:
                    pos = vb.mapSceneToView(ev.scenePos())
                    # Generous threshold (25px) so it's hard to accidentally
                    # create a point when trying to grab one.
                    idx = self._find_nearest_band(pos, threshold_px=25)
                    if idx >= 0 and self.audition_enabled():
                        lo, hi = self._region_for_band(idx)
                        if shift:
                            self.auditionSweepRequested.emit(lo, hi)
                        else:
                            # Isolate the region AND grab the point, so you can
                            # tweak its gain while listening.
                            self.auditionIsolateRequested.emit(lo, hi)
                            self._dragging_band = idx
                        ev.accept()
                        return
                    if plain:
                        if idx >= 0:
                            self._dragging_band = idx
                        else:
                            hz = max(20.0, min(20000.0, 10.0 ** pos.x()))
                            db = max(-15.0, min(15.0, pos.y()))
                            self._add_band(hz, db)
                            self._dragging_band = len(self._current_geq().bands) - 1
                        ev.accept()
                        return
            if ev.button() == Qt.RightButton:
                # Right-click removes nearest band (both modes).
                pos = vb.mapSceneToView(ev.scenePos())
                idx = self._find_nearest_band(pos, threshold_px=25)
                if idx >= 0:
                    self._remove_band(idx)
                ev.accept()
                return
            self._orig_press(ev)

        def _move(ev):
            if self._dragging_band >= 0:
                pos = vb.mapSceneToView(ev.scenePos())
                geq = self._current_geq()
                if geq and 0 <= self._dragging_band < len(geq.bands):
                    band = geq.bands[self._dragging_band]
                    band.gain_db = round(max(-15.0, min(15.0, pos.y())), 2)
                    if not self._fixed_mode:
                        band.freq_hz = round(max(20.0, min(20000.0, 10.0 ** pos.x())), 1)
                    self._refresh()
                ev.accept()
                return
            self._orig_move(ev)

        def _release(ev):
            if ev.button() == Qt.LeftButton and self._dragging_band >= 0:
                self._dragging_band = -1
                ev.accept()
                return
            self._orig_release(ev)

        def _click(ev):
            if ev.button() == Qt.LeftButton:
                ev.accept()
                return
            self._orig_click(ev)

        vb.mousePressEvent = _press
        vb.mouseMoveEvent = _move
        vb.mouseReleaseEvent = _release
        vb.mouseClickEvent = _click

    # ----------------------------------------------------------- public

    def link_x_axis(self, spectrum_view_box) -> None:
        """Link this plot's x-axis to the spectrum view's ViewBox."""
        self._plot.getViewBox().setXLink(spectrum_view_box)

    def set_graphic_eqs(self, geqs: List[GraphicEQ]) -> None:
        """Load graphic EQs (e.g. from saved state)."""
        self._geqs = list(geqs)
        self._rebuild_combo()
        self._update_mode_from_current()
        self._refresh_visual()

    def graphic_eqs(self) -> List[GraphicEQ]:
        return list(self._geqs)

    # --------------------------------------------------------- audition

    def audition_enabled(self) -> bool:
        return self._audition_check.isChecked()

    def set_audition_enabled(self, on: bool) -> None:
        self._audition_check.setChecked(bool(on))

    def audition_noise(self) -> str:
        """The selected auto-play noise: ``"pink"`` or ``"grey"``."""
        return self._audition_noise.currentText().lower()

    def set_audition_noise(self, kind: str) -> None:
        idx = self._audition_noise.findText(str(kind).capitalize())
        if idx >= 0:
            self._audition_noise.setCurrentIndex(idx)

    def _on_audition_toggled(self, on: bool) -> None:
        self._audition_noise.setEnabled(on)
        self.auditionModeChanged.emit(on)

    def _region_for_band(self, idx: int) -> tuple[float, float]:
        geq = self._current_geq()
        if not geq or not (0 <= idx < len(geq.bands)):
            return 20.0, 20000.0
        target = geq.bands[idx].freq_hz
        return region_for_freq([b.freq_hz for b in geq.bands], target)

    # --------------------------------------------------------- internal

    def _current_geq(self) -> Optional[GraphicEQ]:
        if 0 <= self._current_idx < len(self._geqs):
            return self._geqs[self._current_idx]
        return None

    def _rebuild_combo(self) -> None:
        self._geq_combo.blockSignals(True)
        self._geq_combo.clear()
        for g in self._geqs:
            self._geq_combo.addItem(g.name)
        if self._geqs:
            self._geq_combo.setCurrentIndex(min(self._current_idx, len(self._geqs) - 1))
        self._geq_combo.blockSignals(False)

    def _update_mode_from_current(self) -> None:
        geq = self._current_geq()
        if geq and geq.mode == "fixed":
            self._radio_fixed.blockSignals(True)
            self._radio_fixed.setChecked(True)
            self._radio_fixed.blockSignals(False)
            self._fixed_mode = True
            self._btn_double_bands.setEnabled(True)
        else:
            self._radio_var.blockSignals(True)
            self._radio_var.setChecked(True)
            self._radio_var.blockSignals(False)
            self._fixed_mode = False
            self._btn_double_bands.setEnabled(False)

    def _refresh_visual(self) -> None:
        """Redraw dots + curve for the current GEQ."""
        geq = self._current_geq()
        if not geq or not geq.bands:
            self._dots.setData([])
            self._curve.setData([], [])
            return

        sorted_bands = sorted(geq.bands, key=lambda b: b.freq_hz)
        spots = [
            {
                "pos": (math.log10(max(20.0, b.freq_hz)), b.gain_db),
                "size": 10,
                "brush": pg.mkBrush(255, 200, 50, 200),
                "pen": pg.mkPen(color=(255, 255, 255), width=2),
            }
            for b in sorted_bands
        ]
        self._dots.setData(spots)

        # Interpolation curve.
        resp = graphic_eq_response(sorted_bands, _RESPONSE_FREQS)
        self._curve.setData(_RESPONSE_FREQS, resp)

    def _refresh(self) -> None:
        """Refresh visuals + text editor + emit change signal."""
        self._refresh_visual()
        self._sync_text_from_bands()
        self.graphicEqChanged.emit(list(self._geqs))

    def _find_nearest_band(self, view_pos, threshold_px: int = 15) -> int:
        geq = self._current_geq()
        if not geq or not geq.bands:
            return -1
        click_log = view_pos.x()
        click_db = view_pos.y()
        best_i, best_dist = -1, float("inf")
        for i, b in enumerate(geq.bands):
            dx = click_log - math.log10(max(20.0, b.freq_hz))
            dy = (click_db - b.gain_db) / 15.0
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < best_dist:
                best_dist = dist
                best_i = i
        vr = self._plot.getViewBox().viewRange()
        x_span = vr[0][1] - vr[0][0]
        w = max(1, self._plot.getViewBox().width())
        px_to_view = x_span / w
        if best_dist < threshold_px * px_to_view:
            return best_i
        return -1

    def _add_band(self, hz: float, db: float) -> None:
        geq = self._current_geq()
        if not geq:
            return
        geq.bands.append(GraphicEQBand(freq_hz=hz, gain_db=round(db, 2)))
        self._refresh()

    def _remove_band(self, idx: int) -> None:
        geq = self._current_geq()
        if geq and 0 <= idx < len(geq.bands):
            geq.bands.pop(idx)
            self._refresh()

    # -------------------------------------------------------- toolbar slots

    def _on_mode_changed(self, var_checked: bool) -> None:
        self._fixed_mode = not var_checked
        self._btn_double_bands.setEnabled(self._fixed_mode)
        geq = self._current_geq()
        if not geq:
            return
        if self._fixed_mode:
            geq.mode = "fixed"
            if not geq.bands:
                geq.bands = make_fixed_bands(8)
        else:
            geq.mode = "variable"
        self._refresh()

    def _on_double_bands(self) -> None:
        geq = self._current_geq()
        if not geq:
            return
        geq.bands = double_bands(geq.bands)
        self._refresh()

    def _on_geq_selected(self, idx: int) -> None:
        self._current_idx = idx
        self._update_mode_from_current()
        self._refresh_visual()
        self._sync_text_from_bands()

    def _on_add_geq(self) -> None:
        name = f"GEQ {len(self._geqs) + 1}"
        self._geqs.append(GraphicEQ(name=name))
        self._rebuild_combo()
        self._current_idx = len(self._geqs) - 1
        self._geq_combo.setCurrentIndex(self._current_idx)
        self._update_mode_from_current()
        self._refresh()

    def _on_remove_geq(self) -> None:
        if not self._geqs:
            return
        if 0 <= self._current_idx < len(self._geqs):
            self._geqs.pop(self._current_idx)
        self._current_idx = max(0, self._current_idx - 1)
        self._rebuild_combo()
        self._update_mode_from_current()
        self._refresh()

    # ------------------------------------------------------- text sync

    def _sync_text_from_bands(self) -> None:
        """Update the text editor to match the current GEQ bands."""
        geq = self._current_geq()
        if not geq or not geq.bands:
            text = ""
        else:
            text = graphic_eq_to_apo_text(geq)
        self._text_updating = True
        self._text_edit.setPlainText(text)
        self._text_updating = False

    def _on_text_changed(self) -> None:
        """Parse the text editor and update bands if valid."""
        if self._text_updating:
            return
        geq = self._current_geq()
        if not geq:
            return
        text = self._text_edit.toPlainText().strip()
        if not text:
            geq.bands = []
            self._refresh_visual()
            self.graphicEqChanged.emit(list(self._geqs))
            return
        parsed = parse_graphic_eq_line(text)
        if parsed is not None:
            geq.bands = parsed.bands
            self._refresh_visual()
            self.graphicEqChanged.emit(list(self._geqs))
