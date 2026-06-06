"""PyQtGraph spectrum view: live FFT, CSV overlay, band selector.

Display convention: the plot uses ``setLogMode(x=True)``, which means
PyQtGraph automatically applies ``log10`` to any data passed via
``setData``. So we pass raw frequencies in Hz to curves, but for items that
live in ViewBox coordinates (``LinearRegionItem``, ``InfiniteLine``) we
pass pre-computed ``log10`` values — the ViewBox's internal coordinate
system is already log-transformed.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import Mode
from eq_helper.dsp.fft import compute_spectrum
from eq_helper.ui.spectrum_interaction import classify_press, compute_ctrl_hint

_FFT_WINDOW = 2048
_FFT_INTERVAL_MS = 33  # ~30 FPS — keeps the FFT cost low while still feeling live
_F_MIN_HZ = 20.0
_F_MAX_HZ = 20000.0
_LOG_F_MIN = math.log10(_F_MIN_HZ)
_LOG_F_MAX = math.log10(_F_MAX_HZ)


def _format_hz(hz: float) -> str:
    """Format a frequency in Hz as a compact label (e.g. ``30``, ``1k``, ``10k``)."""
    if hz >= 1000.0:
        kv = hz / 1000.0
        if abs(kv - round(kv)) < 1e-6:
            return f"{int(round(kv))}k"
        return f"{kv:g}k"
    if abs(hz - round(hz)) < 1e-6:
        return f"{int(round(hz))}"
    return f"{hz:g}"


class LogFrequencyAxis(pg.AxisItem):
    """Axis that labels ticks in raw Hz at log-spaced positions.

    Matches the Equalizer APO axis convention: decade ticks plus intermediate
    ``2, 3, 4, 5, 6, 7, 8, 9`` marks within each decade, labeled as
    ``20, 30, ..., 100, 200, ..., 1k, 2k, ..., 20k``.

    Because the parent plot uses ``setLogMode(x=True)``, the values this axis
    receives are already log10(Hz), so we convert back via ``10**v`` for
    display.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Disable pyqtgraph's automatic SI-prefix (Hz -> kHz) rescaling:
        # we do our own formatting.
        self.enableAutoSIPrefix(False)

    def tickValues(self, minVal: float, maxVal: float, size: float):
        # minVal/maxVal are in log10(Hz) because setLogMode(x=True) is on.
        if maxVal <= minVal:
            return []
        lo_hz = max(1.0, 10 ** minVal)
        hi_hz = 10 ** maxVal
        major: list[float] = []
        minor: list[float] = []
        start_dec = int(math.floor(math.log10(lo_hz)))
        end_dec = int(math.ceil(math.log10(hi_hz)))
        for decade in range(start_dec, end_dec + 1):
            base = 10 ** decade
            for mult in (1, 2, 3, 4, 5, 6, 7, 8, 9):
                hz = base * mult
                if hz < lo_hz * 0.999 or hz > hi_hz * 1.001:
                    continue
                log_hz = math.log10(hz)
                # Major ticks get text labels; 1 and 2 are always major (so
                # even at a coarse zoom you still see every decade + its
                # half); 3-9 are minor ticks (tick mark only, no label) to
                # keep the axis readable without clutter.
                if mult in (1, 2, 5):
                    major.append(log_hz)
                else:
                    minor.append(log_hz)
        return [(1.0, major), (0.1, minor)]

    def tickStrings(self, values, scale, spacing):
        return [_format_hz(10 ** v) for v in values]


class SpectrumView(QGroupBox):
    """Log-frequency, dB-y spectrum with live curve, overlay, and band region."""

    bandChanged = Signal(float, float)  # (fc_hz, bw_hz)
    frequencyClicked = Signal(float)  # Hz — emitted on click or during drag
    frequencyDragFinished = Signal()  # emitted when the user releases the mouse
    # EQ mode signals
    eqFilterCreated = Signal(float, float)  # (hz, gain_db) — new filter from click
    eqFilterMoved = Signal(int, float, float)  # (index, hz, gain_db) — drag
    eqFilterQChanged = Signal(int, float)  # (index, new_q) — scroll wheel
    focusSweepRequested = Signal(float)  # Hz — Shift+click asks for a focused sweep here

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__("Spectrum", parent)
        self._engine = engine

        pg.setConfigOptions(antialias=False)  # cheaper redraws when zoomed
        # Install our custom log-Hz axis. No ``units=`` on the label so that
        # pyqtgraph's auto-SI-prefix stays disabled.
        freq_axis = LogFrequencyAxis(orientation="bottom")
        self._plot = pg.PlotWidget(axisItems={"bottom": freq_axis})
        self._plot.setLogMode(x=True, y=False)
        self._plot.setLabel("bottom", "Frequency (Hz)")
        self._plot.setLabel("left", "Magnitude", units="dB")
        self._plot.showGrid(x=True, y=True, alpha=0.3)

        # Add a small margin so the "20" and "20k" edge labels aren't clipped
        # by the plot border. 0.03 in log10 ≈ 7% of a decade — enough to
        # keep the tick text fully visible at both extremes.
        _XPAD = 0.03
        vb = self._plot.getViewBox()
        vb.setLimits(
            xMin=_LOG_F_MIN - _XPAD,
            xMax=_LOG_F_MAX + _XPAD,
            yMin=-120.0,
            yMax=40.0,
        )
        self._plot.setXRange(_LOG_F_MIN - _XPAD, _LOG_F_MAX + _XPAD, padding=0)
        self._plot.setYRange(-100.0, 20.0, padding=0)
        self._plot.disableAutoRange()

        legend = self._plot.addLegend(offset=(-10, 10))

        self._live_curve = self._plot.plot(
            pen=pg.mkPen(color=(255, 220, 80), width=2),
            name="Live",
        )
        self._live_curve.setClipToView(True)
        self._live_curve.setDownsampling(auto=True, method="peak")

        self._overlay_curve = self._plot.plot(
            pen=pg.mkPen(color=(80, 200, 255), width=2),
            name="Measurement",
        )
        self._overlay_curve.setClipToView(True)
        self._overlay_curve.setDownsampling(auto=True, method="peak")

        self._eq_curve = self._plot.plot(
            pen=pg.mkPen(color=(255, 120, 120), width=2),
            name="EQ",
        )
        self._eq_curve.setClipToView(True)
        self._eq_curve.setDownsampling(auto=True, method="peak")

        self._combined_curve = self._plot.plot(
            pen=pg.mkPen(color=(120, 255, 120), width=2),
            name="Combined",
        )
        self._combined_curve.setClipToView(True)
        self._combined_curve.setDownsampling(auto=True, method="peak")

        # Second headphone: measurement + EQ + combined (for A/B comparison).
        self._overlay2_curve = self._plot.plot(
            pen=pg.mkPen(color=(80, 160, 200), width=2, style=Qt.DashLine),
            name="Measurement 2",
        )
        self._overlay2_curve.setClipToView(True)
        self._overlay2_curve.setDownsampling(auto=True, method="peak")

        self._eq2_curve = self._plot.plot(
            pen=pg.mkPen(color=(200, 80, 80), width=2, style=Qt.DashLine),
            name="EQ 2",
        )
        self._eq2_curve.setClipToView(True)
        self._eq2_curve.setDownsampling(auto=True, method="peak")

        self._combined2_curve = self._plot.plot(
            pen=pg.mkPen(color=(80, 200, 80), width=2, style=Qt.DashLine),
            name="Combined 2",
        )
        self._combined2_curve.setClipToView(True)
        self._combined2_curve.setDownsampling(auto=True, method="peak")

        self._mic_curve = self._plot.plot(
            pen=pg.mkPen(color=(255, 150, 255), width=2),
            name="Mic",
        )
        self._mic_curve.setClipToView(True)
        self._mic_curve.setDownsampling(auto=True, method="peak")

        # EQ preview curve (the combined response of the user's filters).
        self._eq_preview_curve = self._plot.plot(
            pen=pg.mkPen(color=(255, 200, 50), width=3),
            name="EQ Preview",
        )
        self._eq_preview_curve.setClipToView(True)

        # EQ filter dots (scatter plot) — always visible in the unified view.
        self._eq_dots = pg.ScatterPlotItem(
            size=12,
            pen=pg.mkPen(color=(255, 255, 255), width=2),
            brush=pg.mkBrush(255, 200, 50, 200),
        )
        self._plot.addItem(self._eq_dots)

        # Ghost dot: a translucent preview shown under the cursor while Ctrl is
        # held over an empty area — "click here to create a peak filter".
        self._ghost_dot = pg.ScatterPlotItem(
            size=14,
            pen=pg.mkPen(color=(255, 255, 255, 200), width=2),
            brush=pg.mkBrush(255, 200, 50, 70),
        )
        self._ghost_dot.hide()
        self._plot.addItem(self._ghost_dot)

        # EQ interaction state.
        self._eq_dragging_index: Optional[int] = -1  # index of filter being dragged
        self._eq_filters_cache: list = []  # cached for dot interaction
        # Cursor tracking that drives the Ctrl-hover affordance.
        self._cursor_log_hz: float = 0.0
        self._cursor_db: float = 0.0
        self._cursor_in_plot: bool = False
        self._hint_highlight_index: int = -1

        # Internal storage for overlay/EQ data so we can recompute combined curves.
        self._overlay_freqs: Optional[np.ndarray] = None
        self._overlay_db: Optional[np.ndarray] = None
        self._eq_freqs: Optional[np.ndarray] = None
        self._eq_db: Optional[np.ndarray] = None
        self._overlay2_freqs: Optional[np.ndarray] = None
        self._overlay2_db: Optional[np.ndarray] = None
        self._eq2_freqs: Optional[np.ndarray] = None
        self._eq2_db: Optional[np.ndarray] = None

        # Make legend items clickable to toggle curve visibility.
        for sample, label in legend.items:
            sample.mouseClickEvent = self._make_legend_click_handler(sample, label)

        # Band isolation visualization: a draggable center line + a
        # non-interactive shaded region. The center line is what the user
        # drags; the shaded region is always positioned at fc ± bw/2 and
        # redrawn whenever the center moves or the bandwidth changes.
        # This avoids the LinearRegionItem drag-vs-snap fight on log axes.
        self._band_bw_hz: float = 500.0
        self._band_fc_hz: float = 1000.0

        self._band_fill = pg.LinearRegionItem(
            values=(math.log10(750.0), math.log10(1250.0)),
            brush=pg.mkBrush(255, 180, 80, 40),
            pen=pg.mkPen(color=(255, 180, 80, 80), width=1),
            movable=False,
        )
        self._band_fill.setZValue(-10)
        self._band_fill.hide()
        self._plot.addItem(self._band_fill)

        self._band_center_line = pg.InfiniteLine(
            pos=math.log10(1000.0),
            angle=90,
            pen=pg.mkPen(color=(255, 180, 80), width=2),
            movable=True,
        )
        self._band_center_line.setBounds((_LOG_F_MIN, _LOG_F_MAX))
        self._band_center_line.sigPositionChanged.connect(self._on_band_center_dragged)
        self._band_center_line.hide()
        self._plot.addItem(self._band_center_line)

        # Sweep progress marker (vertical line tracking instantaneous freq).
        self._sweep_marker = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(color=(180, 255, 180), width=1, style=Qt.DotLine),
            movable=False,
        )
        self._sweep_marker.hide()
        self._plot.addItem(self._sweep_marker)

        # Mouse-tracking crosshair: a vertical line + a floating text label
        # that shows the exact frequency under the cursor (like squig.link).
        self._crosshair = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(color=(200, 200, 200, 120), width=1),
            movable=False,
        )
        self._crosshair.hide()
        self._plot.addItem(self._crosshair)

        self._crosshair_label = pg.TextItem(
            text="",
            color=(220, 220, 220),
            anchor=(0.5, 1.0),  # center-bottom anchor → label sits above cursor
        )
        self._crosshair_label.setFont(QFont("monospace", 9))
        self._crosshair_label.hide()
        self._plot.addItem(self._crosshair_label)

        # Connect mouse move on the scene. pyqtgraph emits scene-coords, so
        # we map them to view (log10-Hz, dB) coordinates.
        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._plot.setMouseTracking(True)

        # Intercept left press/move/release on the ViewBox so holding left-
        # click continuously sweeps the sine through frequencies as the user
        # drags across the spectrum. Right-click is left to pyqtgraph defaults.
        vb = self._plot.getViewBox()
        self._freq_dragging = False
        self._orig_mouse_press = vb.mousePressEvent
        self._orig_mouse_move = vb.mouseMoveEvent
        self._orig_mouse_release = vb.mouseReleaseEvent
        self._orig_mouse_click = vb.mouseClickEvent

        def _hit_is_on_interactive(scene_pos):
            """Return True if scene_pos lands on an interactive overlay
            (legend, band center line) that should handle its own clicks."""
            items = self._plot.scene().items(scene_pos)
            for item in items:
                # The band center line is a movable InfiniteLine.
                if item is self._band_center_line and self._band_center_line.isVisible():
                    return True
                # Walk up the parent chain to see if it belongs to the legend.
                parent = item
                while parent is not None:
                    if isinstance(parent, pg.LegendItem):
                        return True
                    parent = parent.parentItem()
            return False

        def _find_nearest_dot(scene_pos, threshold_px=15):
            """Find the index of the nearest EQ filter dot, or -1."""
            view_pos = vb.mapSceneToView(scene_pos)
            return self._nearest_dot_index(view_pos.x(), view_pos.y(), threshold_px)

        def _press(ev):
            if ev.button() != Qt.LeftButton:
                self._orig_mouse_press(ev)
                return
            ctrl = bool(ev.modifiers() & Qt.ControlModifier)
            shift = bool(ev.modifiers() & Qt.ShiftModifier)
            near_idx = _find_nearest_dot(ev.scenePos())
            action = classify_press(
                ctrl_held=ctrl,
                shift_held=shift,
                near_dot=near_idx >= 0,
                on_interactive=_hit_is_on_interactive(ev.scenePos()),
            )
            if action == "pass":
                self._orig_mouse_press(ev)
                return
            if action == "sine":
                # Plain left-click/drag always auditions a sine wave.
                self._freq_dragging = True
                pos = vb.mapSceneToView(ev.scenePos())
                hz = 10.0 ** pos.x()
                if _F_MIN_HZ <= hz <= _F_MAX_HZ:
                    self.frequencyClicked.emit(hz)
                ev.accept()
                return
            if action == "focus_sweep":
                # Shift+click: focused sweep centred on the clicked frequency.
                pos = vb.mapSceneToView(ev.scenePos())
                hz = 10.0 ** pos.x()
                if _F_MIN_HZ <= hz <= _F_MAX_HZ:
                    self.focusSweepRequested.emit(hz)
                ev.accept()
                return
            if action == "grab":
                # Ctrl+click near a dot: pick it up to drag.
                self._eq_dragging_index = near_idx
                ev.accept()
                return
            # action == "create": Ctrl+click on empty space makes a peak filter.
            pos = vb.mapSceneToView(ev.scenePos())
            hz = max(_F_MIN_HZ, min(_F_MAX_HZ, 10.0 ** pos.x()))
            db = max(-30.0, min(30.0, pos.y()))
            # Capture the new filter's index BEFORE emitting: the signal
            # round-trips synchronously and appends to the cache, so reading
            # len() afterwards would point one past the new dot and break the
            # immediate drag.
            new_index = len(self._eq_filters_cache)
            self.eqFilterCreated.emit(hz, db)
            self._eq_dragging_index = new_index
            ev.accept()

        def _move(ev):
            if self._eq_dragging_index >= 0:
                pos = vb.mapSceneToView(ev.scenePos())
                hz = max(_F_MIN_HZ, min(_F_MAX_HZ, 10.0 ** pos.x()))
                db = max(-30.0, min(30.0, pos.y()))
                self.eqFilterMoved.emit(self._eq_dragging_index, hz, db)
                ev.accept()
                return
            if self._freq_dragging:
                pos = vb.mapSceneToView(ev.scenePos())
                hz = 10.0 ** pos.x()
                if _F_MIN_HZ <= hz <= _F_MAX_HZ:
                    self.frequencyClicked.emit(hz)
                ev.accept()
                return
            self._orig_mouse_move(ev)

        def _release(ev):
            if ev.button() == Qt.LeftButton:
                if self._eq_dragging_index >= 0:
                    self._eq_dragging_index = -1
                    ev.accept()
                    return
                if self._freq_dragging:
                    self._freq_dragging = False
                    self.frequencyDragFinished.emit()
                    ev.accept()
                    return
            self._orig_mouse_release(ev)

        def _click(ev):
            if ev.button() == Qt.LeftButton:
                # Plain clicks on the legend / band line keep their own
                # handling; everything else (sine, Ctrl filter ops) is handled
                # by press/release, so swallow the default click here.
                if not ev.modifiers() and _hit_is_on_interactive(ev.scenePos()):
                    self._orig_mouse_click(ev)
                    return
                ev.accept()
                return
            self._orig_mouse_click(ev)

        self._orig_wheel = vb.wheelEvent

        def _wheel(ev, axis=None):
            if self._eq_dragging_index >= 0:
                # Scroll wheel adjusts Q while dragging a filter dot.
                delta = ev.delta() if hasattr(ev, 'delta') else ev.angleDelta().y()
                step = 0.1 if delta > 0 else -0.1
                idx = self._eq_dragging_index
                if 0 <= idx < len(self._eq_filters_cache):
                    old_q = self._eq_filters_cache[idx].q
                    new_q = max(0.1, min(30.0, old_q + step))
                    self.eqFilterQChanged.emit(idx, new_q)
                ev.accept()
                return
            self._orig_wheel(ev, axis)

        vb.mousePressEvent = _press
        vb.mouseMoveEvent = _move
        vb.mouseReleaseEvent = _release
        vb.mouseClickEvent = _click
        vb.wheelEvent = _wheel

        layout = QVBoxLayout(self)
        layout.addWidget(self._plot)

        self._timer = QTimer(self)
        self._timer.setInterval(_FFT_INTERVAL_MS)
        self._timer.timeout.connect(self._update_fft)
        self._timer.start()

    # ---------------------------------------------------------- legend toggle

    def _make_legend_click_handler(self, sample, label):
        """Return a click handler that toggles a curve's visibility via legend."""
        def handler(event):
            curve = None
            for item in [
                self._live_curve,
                self._overlay_curve,
                self._eq_curve,
                self._combined_curve,
                self._overlay2_curve,
                self._eq2_curve,
                self._combined2_curve,
                self._mic_curve,
                self._eq_preview_curve,
            ]:
                if item.name() == label.text:
                    curve = item
                    break
            if curve is None:
                return
            vis = not curve.isVisible()
            curve.setVisible(vis)
            alpha = 255 if vis else 80
            label.setAttr("color", f"{alpha}")
            sample.setOpacity(1.0 if vis else 0.3)
        return handler

    # --------------------------------------------------------------- crosshair

    def _on_mouse_moved(self, scene_pos) -> None:
        vb = self._plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            self._crosshair.hide()
            self._crosshair_label.hide()
            self._cursor_in_plot = False
            self._update_ctrl_hint()
            return
        mouse_point = vb.mapSceneToView(scene_pos)
        log_hz = mouse_point.x()
        db_val = mouse_point.y()
        hz = 10.0 ** log_hz
        if hz < 1.0 or hz > 100000.0:
            self._crosshair.hide()
            self._crosshair_label.hide()
            self._cursor_in_plot = False
            self._update_ctrl_hint()
            return

        # Remember where the cursor is so the Ctrl-hover hint can follow it.
        self._cursor_log_hz = log_hz
        self._cursor_db = db_val
        self._cursor_in_plot = True
        self._update_ctrl_hint()

        self._crosshair.setPos(log_hz)
        self._crosshair.show()

        # Format with decimal precision that makes sense at this frequency.
        if hz >= 1000.0:
            label = f"{hz:,.0f} Hz"
        elif hz >= 100.0:
            label = f"{hz:.0f} Hz"
        else:
            label = f"{hz:.1f} Hz"
        label += f"  {db_val:.1f} dB"

        self._crosshair_label.setText(label)
        # Place the label near the top of the visible area, centered on the
        # crosshair.
        y_range = vb.viewRange()[1]
        top_y = y_range[1] - (y_range[1] - y_range[0]) * 0.02
        self._crosshair_label.setPos(log_hz, top_y)
        self._crosshair_label.show()

    # ------------------------------------------------------------------ live

    def _update_fft(self) -> None:
        # Live output spectrum.
        samples = self._engine.pop_recent_samples(_FFT_WINDOW)
        if samples is not None:
            freqs, db = compute_spectrum(samples, self._engine.sample_rate)
            if freqs.size > 0:
                self._live_curve.setData(freqs, db, skipFiniteCheck=True)

        # Mic input spectrum.
        mic_samples = self._engine.mic_ring.read_latest(_FFT_WINDOW)
        if mic_samples is not None:
            mic_freqs, mic_db = compute_spectrum(
                mic_samples, self._engine.sample_rate
            )
            if mic_freqs.size > 0:
                self._mic_curve.setData(mic_freqs, mic_db, skipFiniteCheck=True)

        # Update sweep marker if sweeping.
        sweep = self._engine._sweep  # type: ignore[attr-defined]
        if sweep is not None and self._engine.state.snapshot().mode == Mode.SWEEP:
            self._sweep_marker.setPos(math.log10(max(_F_MIN_HZ, sweep.current_freq)))
            self._sweep_marker.show()
        else:
            self._sweep_marker.hide()

        # Refresh the Ctrl-hover hint so toggling Ctrl with a still cursor reacts.
        self._update_ctrl_hint()

    # --------------------------------------------------------------- overlay

    def set_overlay(self, freqs: np.ndarray, db: np.ndarray) -> None:
        if freqs.size == 0:
            self._overlay_freqs = None
            self._overlay_db = None
            self._overlay_curve.clear()
            self._update_combined()
            return
        mask = freqs > 0
        self._overlay_freqs = freqs[mask]
        self._overlay_db = db[mask]
        self._overlay_curve.setData(
            self._overlay_freqs, self._overlay_db, skipFiniteCheck=True
        )
        self._update_combined()

    def clear_overlay(self) -> None:
        self._overlay_freqs = None
        self._overlay_db = None
        self._overlay_curve.clear()
        self._update_combined()

    # ------------------------------------------------------------------- EQ

    def set_eq_response(self, freqs: np.ndarray, db: np.ndarray) -> None:
        """Display the EQ frequency response curve."""
        if freqs.size == 0:
            self._eq_freqs = None
            self._eq_db = None
            self._eq_curve.clear()
            self._update_combined()
            return
        self._eq_freqs = freqs
        self._eq_db = db
        self._eq_curve.setData(freqs, db, skipFiniteCheck=True)
        self._update_combined()

    def clear_eq_response(self) -> None:
        self._eq_freqs = None
        self._eq_db = None
        self._eq_curve.clear()
        self._update_combined()

    # -------------------------------------------------------------- combined

    def _update_combined(self) -> None:
        """Recompute the combined (measurement + EQ) curve.

        If both measurement and EQ data are available, interpolate the EQ
        response onto the measurement's frequency grid and sum them.
        """
        if (
            self._overlay_freqs is None
            or self._overlay_db is None
            or self._eq_freqs is None
            or self._eq_db is None
        ):
            self._combined_curve.clear()
            return
        # Interpolate EQ onto the measurement frequency grid.
        eq_at_overlay = np.interp(
            self._overlay_freqs, self._eq_freqs, self._eq_db
        )
        combined_db = self._overlay_db + eq_at_overlay
        self._combined_curve.setData(
            self._overlay_freqs, combined_db, skipFiniteCheck=True
        )

    # ----------------------------------------------------------- overlay 2

    def set_overlay2(self, freqs: np.ndarray, db: np.ndarray) -> None:
        if freqs.size == 0:
            self._overlay2_freqs = None
            self._overlay2_db = None
            self._overlay2_curve.clear()
            self._update_combined2()
            return
        mask = freqs > 0
        self._overlay2_freqs = freqs[mask]
        self._overlay2_db = db[mask]
        self._overlay2_curve.setData(
            self._overlay2_freqs, self._overlay2_db, skipFiniteCheck=True
        )
        self._update_combined2()

    def clear_overlay2(self) -> None:
        self._overlay2_freqs = None
        self._overlay2_db = None
        self._overlay2_curve.clear()
        self._update_combined2()

    # --------------------------------------------------------------- EQ 2

    def set_eq2_response(self, freqs: np.ndarray, db: np.ndarray) -> None:
        if freqs.size == 0:
            self._eq2_freqs = None
            self._eq2_db = None
            self._eq2_curve.clear()
            self._update_combined2()
            return
        self._eq2_freqs = freqs
        self._eq2_db = db
        self._eq2_curve.setData(freqs, db, skipFiniteCheck=True)
        self._update_combined2()

    def clear_eq2_response(self) -> None:
        self._eq2_freqs = None
        self._eq2_db = None
        self._eq2_curve.clear()
        self._update_combined2()

    # ---------------------------------------------------------- combined 2

    def _update_combined2(self) -> None:
        if (
            self._overlay2_freqs is None
            or self._overlay2_db is None
            or self._eq2_freqs is None
            or self._eq2_db is None
        ):
            self._combined2_curve.clear()
            return
        eq_at_overlay = np.interp(
            self._overlay2_freqs, self._eq2_freqs, self._eq2_db
        )
        combined_db = self._overlay2_db + eq_at_overlay
        self._combined2_curve.setData(
            self._overlay2_freqs, combined_db, skipFiniteCheck=True
        )

    # --------------------------------------------------------- EQ interaction

    def set_eq_preview(self, freqs: np.ndarray, db: np.ndarray) -> None:
        """Update the EQ preview response curve (combined filter chain)."""
        if freqs is not None and db is not None and freqs.size > 0:
            self._eq_preview_curve.setData(freqs, db, skipFiniteCheck=True)
        else:
            self._eq_preview_curve.setData([], [])

    def clear_eq_preview(self) -> None:
        self._eq_preview_curve.setData([], [])

    def _nearest_dot_index(
        self, log_hz: float, db: float, threshold_px: float = 15.0
    ) -> int:
        """Index of the EQ dot nearest to a view-space point, or -1 if none is
        within ``threshold_px`` pixels."""
        if not self._eq_filters_cache:
            return -1
        vb = self._plot.getViewBox()
        best_i, best_dist = -1, float("inf")
        for i, f in enumerate(self._eq_filters_cache):
            dx = log_hz - math.log10(max(20.0, f.fc_hz))
            dy = (db - f.gain_db) / 30.0  # normalize dB range against octave-ish x
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < best_dist:
                best_dist = dist
                best_i = i
        # Convert the pixel threshold to view coords (approximate).
        view_range = vb.viewRange()
        x_span = view_range[0][1] - view_range[0][0]
        widget_width = max(1, vb.width())
        px_to_view = x_span / widget_width
        if best_dist < threshold_px * px_to_view:
            return best_i
        return -1

    def _update_ctrl_hint(self) -> None:
        """Show what a Ctrl-click would do: a ghost dot where a new filter would
        land, or a highlight on the dot a click would grab."""
        ctrl = bool(QGuiApplication.keyboardModifiers() & Qt.ControlModifier)
        near_idx = -1
        if ctrl and self._cursor_in_plot:
            near_idx = self._nearest_dot_index(self._cursor_log_hz, self._cursor_db)
        show_ghost, highlight_index = compute_ctrl_hint(
            ctrl_held=ctrl,
            cursor_in_plot=self._cursor_in_plot,
            near_dot_index=near_idx,
        )

        if show_ghost:
            hz = max(_F_MIN_HZ, min(_F_MAX_HZ, 10.0 ** self._cursor_log_hz))
            db = max(-30.0, min(30.0, self._cursor_db))
            self._ghost_dot.setData([{
                "pos": (math.log10(hz), db),
                "size": 14,
                "brush": pg.mkBrush(255, 200, 50, 70),
                "pen": pg.mkPen(color=(255, 255, 255, 200), width=2),
            }])
            self._ghost_dot.show()
        else:
            self._ghost_dot.hide()

        if highlight_index != self._hint_highlight_index:
            self._hint_highlight_index = highlight_index
            self._render_dots(highlight_index)

    def _render_dots(self, highlight_index: int = -1) -> None:
        """Draw the EQ filter dots; ``highlight_index`` (if >= 0) is drawn in
        the grab colour to signal that a Ctrl-click there will move it."""
        spots = []
        for i, f in enumerate(self._eq_filters_cache):
            if not f.enabled:
                continue
            if i == highlight_index:
                brush = pg.mkBrush(80, 220, 255, 235)  # cyan = "click moves this"
                size = 16
            else:
                brush = pg.mkBrush(255, 200, 50, 200)
                size = 12
            spots.append({
                "pos": (math.log10(max(20.0, f.fc_hz)), f.gain_db),
                "size": size,
                "brush": brush,
                "pen": pg.mkPen(color=(255, 255, 255), width=2),
            })
        self._eq_dots.setData(spots)

    def update_eq_dots(self, filters) -> None:
        """Update the scatter plot dots for each EQ filter."""
        self._eq_filters_cache = list(filters)
        if not self._eq_filters_cache:
            self._eq_dots.clear()
            self._hint_highlight_index = -1
            return
        self._render_dots(self._hint_highlight_index)

    # ------------------------------------------------------------------ band

    def set_band_visible(self, visible: bool) -> None:
        self._band_fill.setVisible(visible)
        self._band_center_line.setVisible(visible)

    def set_band(self, fc_hz: float, bw_hz: float) -> None:
        """Set the band center and width (called from spinbox changes)."""
        self._band_fc_hz = float(fc_hz)
        self._band_bw_hz = float(bw_hz)
        self._sync_band_fill()
        self._band_center_line.blockSignals(True)
        self._band_center_line.setPos(math.log10(max(_F_MIN_HZ, fc_hz)))
        self._band_center_line.blockSignals(False)

    def _sync_band_fill(self) -> None:
        """Reposition the shaded region to fc ± bw/2 in Hz."""
        lo = max(_F_MIN_HZ, self._band_fc_hz - self._band_bw_hz / 2.0)
        hi = min(_F_MAX_HZ, self._band_fc_hz + self._band_bw_hz / 2.0)
        if hi <= lo:
            hi = lo + 1.0
        self._band_fill.setRegion((math.log10(lo), math.log10(hi)))

    def _on_band_center_dragged(self, line) -> None:
        """User dragged the center line — update fc, keep bw fixed."""
        log_hz = line.value()
        fc = 10.0 ** log_hz
        fc = max(_F_MIN_HZ, min(_F_MAX_HZ, fc))
        self._band_fc_hz = fc
        self._sync_band_fill()
        self.bandChanged.emit(fc, self._band_bw_hz)
