"""Steps ①–③: find your resonance, choose a target, optional upper treble.

Driven by :class:`~eq_helper.calibration.tuning.TuningSession`. Ports the
behaviour of EQ Helper's ResonanceDialog in plain language: moving the
slider (or dragging the marker) updates the "Your IEM" curve at once and
regenerates the EQ after a short pause; a playing tone follows the slider.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSizePolicy, QSlider, QSpinBox, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from eq_helper.calibration.tuning import TuningSession, target_hash
from eq_helper.dsp.resonance import CONTROL_RANGE, PERSONAL_TREBLE_RANGE, detect_high_treble_peak

from .common import Card, chip, hint, side_name, tr
from .response_graph import ResponseGraph

REGENERATE_DELAY_MS = 250
LIVE_REGENERATE_DELAY_MS = 60   # while pink noise plays through the EQ: follow the slider closely
DEFAULT_UPPER_HZ = 15500


class ResonanceControls(QWidget):
    """Slider + Hz box + Play tone / Sweep around it / Pink noise + EQ.

    Used for both the primary and the upper resonance so they work the same way.
    """

    valueChanged = Signal(int)

    def __init__(self, label: str, accessible: str, hz_range, step: int, page: int, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.label = QLabel(label)
        self.label.setObjectName("hint")
        row.addWidget(self.label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(*hz_range)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(page)
        self.slider.setMinimumHeight(36)
        self.slider.setAccessibleName(accessible)
        self.slider.valueChanged.connect(self.valueChanged)
        self.label.setBuddy(self.slider)
        row.addWidget(self.slider, 1)
        self.spin = QSpinBox()
        self.spin.setRange(*hz_range)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(" Hz")
        self.spin.setAccessibleName(tr("{name} in hertz").format(name=accessible))
        self.spin.valueChanged.connect(self.valueChanged)
        row.addWidget(self.spin)
        outer.addLayout(row)
        self.buttons = QHBoxLayout()
        self.tone_button = QPushButton(tr("▶ Play tone"))
        self.tone_button.setCheckable(True)
        self.buttons.addWidget(self.tone_button)
        self.sweep_button = QPushButton(tr("〰 Sweep around it"))
        self.sweep_button.setCheckable(True)
        self.sweep_button.setToolTip(tr("A slow sweep around the slider's frequency: the resonance is "
                                        "where it sounds loudest."))
        self.buttons.addWidget(self.sweep_button)
        self.pink_button = QPushButton(tr("≈ Pink noise + EQ"))
        self.pink_button.setCheckable(True)
        self.pink_button.setToolTip(tr("Plays pink noise through your EQ. Move the slider while it plays "
                                       "until the treble sounds smooth, with no harsh or hissy spot."))
        self.buttons.addWidget(self.pink_button)
        self.buttons.addStretch(1)
        outer.addLayout(self.buttons)

    def set_value(self, hz: int) -> None:
        for widget in (self.slider, self.spin):
            widget.blockSignals(True)
            widget.setValue(int(hz))
            widget.blockSignals(False)

    def value(self) -> int:
        return self.slider.value()

    def set_playing(self, what: str | None) -> None:
        self.tone_button.setChecked(what == "tone")
        self.sweep_button.setChecked(what == "focus")
        self.pink_button.setChecked(what == "pink")


class _TargetItem:
    def __init__(self, name, freqs, db, path=None, is_default=False):
        self.name, self.freqs, self.db = name, np.asarray(freqs, float), np.asarray(db, float)
        self.path = None if path is None else str(path)
        self.is_default = is_default
        self.hash = target_hash(self.freqs, self.db)

    @property
    def label(self) -> str:
        return self.name + (tr(" (default)") if self.is_default else "")


class TuningPanel(QWidget):
    eqChanged = Signal(object)  # ChannelEQ
    apoOffRequested = Signal()

    def __init__(self, ctx, audio, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.audio = audio
        self.loaded = None
        self.session: TuningSession | None = None
        self.channel_eq = None
        self._targets: list[_TargetItem] = []
        self._syncing = False
        self._owner: str | None = None   # which control the playing sound belongs to: primary / upper / graph
        self._regen = QTimer(self)
        self._regen.setSingleShot(True)
        self._regen.setInterval(REGENERATE_DELAY_MS)
        self._regen.timeout.connect(self.regenerate)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._build_step1(), 1)
        lower = QHBoxLayout()
        lower.addWidget(self._build_step2(), 1)
        lower.addWidget(self._build_step3(), 1)
        outer.addLayout(lower)
        audio.on_change(self._sync_play_buttons)

    # ── layout ─────────────────────────────────────────────────────────────
    def _build_step1(self) -> Card:
        card = Card(tr("① Find your resonance"))
        top = QHBoxLayout()
        self.ears = QTabBar()
        self.ears.setExpanding(False)
        self.ears.setAccessibleName(tr("Ear"))
        self.ears.currentChanged.connect(self._ear_changed)
        top.addWidget(self.ears)
        top.addStretch(1)
        self.details_button = QPushButton(tr("Details…"))
        self.details_button.setFlat(True)
        self.details_button.clicked.connect(self.show_details)
        top.addWidget(self.details_button)
        card.body.addLayout(top)
        self.hint = hint(tr(
            "Press Play tone, then move the slider (or drag the orange line) until the tone sounds loudest "
            "or most piercing in this ear — that's your resonance. Do the same for the other ear. Click or "
            "drag on the graph to hear any frequency. Keep the volume low."))
        card.body.addWidget(self.hint)
        self.other_eq = QLabel()
        self.other_eq.setObjectName("hint")
        self.other_eq.setWordWrap(True)
        self.other_eq_off = QPushButton(tr("Turn it off in Equalizer APO"))
        self.other_eq_off.hide()
        self.other_eq_off.clicked.connect(self.apoOffRequested)
        other_row = QHBoxLayout()
        other_row.addWidget(self.other_eq, 1)
        other_row.addWidget(self.other_eq_off, 0, Qt.AlignTop)
        card.body.addLayout(other_row)
        self.set_other_eqs([], False)

        self.graph = ResponseGraph()
        self.graph.setMinimumHeight(200)
        self.graph.set_marker_bounds(*CONTROL_RANGE)
        self.graph.set_upper_bounds(*PERSONAL_TREBLE_RANGE)
        self.graph.markerMoved.connect(self._marker_dragged)
        self.graph.upperMoved.connect(self._upper_dragged)
        self.graph.frequencyClicked.connect(self._graph_clicked)
        card.body.addWidget(self.graph, 1)

        row = QHBoxLayout()
        readout = QVBoxLayout()
        readout.setSpacing(0)
        self.readout_side = QLabel()
        self.readout_side.setObjectName("hint")
        readout.addWidget(self.readout_side)
        self.readout = QLabel()
        self.readout.setObjectName("bigReadout")
        font = self.readout.font()
        font.setPointSizeF(font.pointSizeF() * 2.2)
        font.setBold(True)
        self.readout.setFont(font)
        self.readout.setMinimumWidth(self.readout.fontMetrics().horizontalAdvance("00,000 Hz") + 8)
        readout.addWidget(self.readout)
        row.addLayout(readout)
        self.primary = ResonanceControls(tr("Resonance"), tr("Resonance frequency"), CONTROL_RANGE, 10, 100)
        self.primary.valueChanged.connect(self.set_primary_hz)
        self.peak_slider, self.peak_spin = self.primary.slider, self.primary.spin
        self.tone_button, self.sweep_button = self.primary.tone_button, self.primary.sweep_button
        self.pink_button = self.primary.pink_button
        self.tone_button.setObjectName("primary")
        self.tone_button.clicked.connect(lambda: self._toggle("tone", "primary"))
        self.sweep_button.clicked.connect(lambda: self._toggle("focus", "primary"))
        self.pink_button.clicked.connect(lambda: self._toggle("pink", "primary"))
        self.stop_button = QPushButton(tr("■ Stop (Space)"))
        self.stop_button.clicked.connect(self.audio.stop_all)
        self.primary.buttons.insertWidget(3, self.stop_button)
        row.addWidget(self.primary, 1)
        card.body.addLayout(row)
        self.pink_hint = hint(tr(
            "Pink noise is playing through your EQ. Move the resonance slowly: where it's right, the treble "
            "sounds smooth and even, with no harsh, hissy or dull spot. Switch the EQ off and on to compare."))
        self.pink_hint.hide()
        card.body.addWidget(self.pink_hint)

        self.range_warning = QLabel()
        self.range_warning.setObjectName("warning")
        self.range_warning.setWordWrap(True)
        self.range_warning.hide()
        card.body.addWidget(self.range_warning)
        return card

    def _build_step2(self) -> Card:
        card = Card(tr("② Choose your target"))
        card.body.addWidget(hint(tr("The sound you want. The one marked default was chosen for you.")))
        row = QHBoxLayout()
        self.target_combo = QComboBox()
        self.target_combo.setAccessibleName(tr("Target"))
        self.target_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.target_combo.currentIndexChanged.connect(self._target_chosen)
        row.addWidget(self.target_combo, 1)
        self.import_target_button = QPushButton(tr("Import…"))
        self.import_target_button.clicked.connect(self._browse_target)
        row.addWidget(self.import_target_button)
        card.body.addLayout(row)
        self.default_chip = chip(tr("default"), "default")
        self.default_chip.hide()
        card.body.addWidget(self.default_chip, 0, Qt.AlignLeft)
        self.target_problem = QLabel()
        self.target_problem.setObjectName("warning")
        self.target_problem.setWordWrap(True)
        card.body.addWidget(self.target_problem)
        card.body.addStretch(1)
        return card

    def _build_step3(self) -> Card:
        card = Card()
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText(tr("③ Fine-tune upper treble (advanced, optional)"))
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setAutoRaise(True)
        font = self.advanced_toggle.font()
        font.setBold(True)
        self.advanced_toggle.setFont(font)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        card.body.addWidget(self.advanced_toggle)
        self.advanced_body = QWidget()
        body = QVBoxLayout(self.advanced_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(hint(tr("Only if you hear a second, higher peak that still bothers you. Play a tone "
                               "and move the slider to where you hear it, per ear. Off keeps the EQ as "
                               "calibrated.")))
        self.upper_enabled = QCheckBox(tr("Adjust the upper treble"))
        self.upper_enabled.toggled.connect(self._set_upper_enabled)
        body.addWidget(self.upper_enabled)
        self.upper = ResonanceControls(tr("Upper resonance"), tr("Upper resonance frequency"),
                                       PERSONAL_TREBLE_RANGE, 50, 250)
        self.upper.valueChanged.connect(self._upper_moved)
        self.upper_slider, self.upper_spin = self.upper.slider, self.upper.spin
        self.upper_tone = self.upper.tone_button
        self.upper_tone.clicked.connect(lambda: self._toggle("tone", "upper"))
        self.upper.sweep_button.clicked.connect(lambda: self._toggle("focus", "upper"))
        self.upper.pink_button.clicked.connect(lambda: self._toggle("pink", "upper"))
        self.reset_upper = QPushButton(tr("Reset"))
        self.reset_upper.setToolTip(tr("Put this ear's upper treble back as calibrated."))
        self.reset_upper.clicked.connect(self._reset_upper)
        self.upper.buttons.insertWidget(3, self.reset_upper)
        body.addWidget(self.upper)
        body.addWidget(hint(tr("Or drag the pink line on the graph.")))
        self.advanced_body.hide()
        card.body.addWidget(self.advanced_body)
        card.body.addStretch(1)
        return card

    def set_other_eqs(self, found: list[str], apo_on: bool) -> None:
        """Warn about other EQs: any EQ already running is heard on top of this one."""
        if apo_on:
            text = tr("Your EQ is on in Equalizer APO, so here you'd hear it twice. Turn it off while you "
                      "tune, then apply again when you're done.")
        elif found:
            text = tr("Turn off any other EQ while you tune. Found on this computer: {apps}. If one of them "
                      "(or your music app, a sound enhancement or your dongle) applies an EQ, you'd hear it on "
                      "top of this one and find the resonance in the wrong place.").format(apps=", ".join(found))
        else:
            text = tr("Turn off any other EQ while you tune: your music app's EQ, sound enhancements (Dolby, "
                      "Windows or Realtek effects…) and your dongle's EQ. You'd hear it on top of this one and "
                      "find the resonance in the wrong place.")
        self.other_eq.setText("⚠ " + text)
        self.other_eq.setObjectName("warning" if apo_on or found else "hint")
        self.other_eq.style().unpolish(self.other_eq)
        self.other_eq.style().polish(self.other_eq)
        self.other_eq_off.setVisible(apo_on)

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def side(self) -> str | None:
        if self.ears.count() == 0:
            return None
        return self.ears.tabData(self.ears.currentIndex())

    @property
    def model(self):
        return self.session.measurements.models[self.side]

    def set_calibration(self, loaded) -> None:
        self.audio.stop_all()
        self.loaded = loaded
        self.session = TuningSession(loaded.measurements)
        remembered = (self.ctx.settings.get("tunings") or {}).get(loaded.calibration_id) or {}
        for side, hz in (remembered.get("primary") or {}).items():
            if side in self.session.primary:
                try:
                    self.session.set_primary(side, hz)
                except ValueError:
                    pass
        for side, hz in (remembered.get("upper") or {}).items():
            try:
                self.session.set_upper(side, hz)
            except ValueError:
                pass
        self.session.upper_enabled = bool(remembered.get("upper_enabled", False)) and bool(self.session.upper)

        self._syncing = True
        while self.ears.count():
            self.ears.removeTab(0)
        for side in self.session.sides:
            index = self.ears.addTab(side_name(side))
            self.ears.setTabData(index, side)
        self.ears.setCurrentIndex(0)
        self._syncing = False

        self._load_targets(loaded.manifest.default_target, remembered.get("target"))
        self._sync_controls()
        self.regenerate()

    def _load_targets(self, manifest_default, preferred) -> None:
        targets = self.ctx.targets
        default = targets.default_for(manifest_default)
        self._targets = [_TargetItem(t.name, t.freqs, t.db, t.path, default is not None and t.path == default.path)
                         for t in targets.targets]
        order = []
        if preferred:
            order += [i for i, t in enumerate(self._targets) if t.path and Path(t.path).name == preferred]
        order += [i for i, t in enumerate(self._targets) if t.is_default]
        order += list(range(len(self._targets)))
        chosen = None
        for index in order:
            item = self._targets[index]
            try:
                self.session.set_target(item.freqs, item.db, item.name, item.path)
            except ValueError:
                continue
            chosen = index
            break
        self._fill_target_combo(chosen)
        if targets.invalid:
            self.target_problem.setText(tr("{n} target file(s) couldn't be used.").format(n=len(targets.invalid)))
            self.target_problem.setToolTip("\n".join(f"{t.path.name}: {t.reason}" for t in targets.invalid))
        else:
            self.target_problem.setText("")

    def _fill_target_combo(self, chosen) -> None:
        self._syncing = True
        self.target_combo.clear()
        for item in self._targets:
            self.target_combo.addItem(item.label)
        if chosen is not None:
            self.target_combo.setCurrentIndex(chosen)
        self._syncing = False
        self._sync_default_chip()

    def _sync_default_chip(self) -> None:
        index = self.target_combo.currentIndex()
        self.default_chip.setVisible(0 <= index < len(self._targets) and self._targets[index].is_default)

    def remember(self) -> None:
        """Keep the current choices for the next launch (not a history entry)."""
        if self.session is None:
            return
        tunings = dict(self.ctx.settings.get("tunings") or {})
        target = Path(self.session.target_path).name if self.session.target_path else None
        tunings[self.loaded.calibration_id] = {
            "primary": dict(self.session.primary), "upper": dict(self.session.upper),
            "upper_enabled": self.session.upper_enabled, "target": target}
        self.ctx.settings.set("tunings", tunings)

    # ── ① resonance ────────────────────────────────────────────────────────
    def set_primary_hz(self, hz) -> None:
        if self._syncing or self.session is None:
            return
        value = self.session.set_primary(self.side, round(float(hz)))
        self._sync_peak_controls()
        self._show_iem_curve()
        self._follow("primary", value)
        self._schedule_regen()

    def _marker_dragged(self, hz: float) -> None:
        self.set_primary_hz(hz)

    def _upper_dragged(self, hz: float) -> None:
        if self.session is None or not self.session.upper_enabled:
            return
        self._upper_moved(int(round(hz)))

    def _graph_clicked(self, hz: float) -> None:
        if self.session is None:
            return
        if self.audio.playing == "tone" and self._owner == "graph":
            self.audio.set_tone(hz)
        else:
            self.audio.play_tone(hz, self.side)
            self._owner = "graph"
            self._sync_play_buttons()

    def _follow(self, owner: str, hz: float) -> None:
        """A playing tone follows the control being moved (and now belongs to it)."""
        if self.audio.playing == "tone":
            self.audio.set_tone(hz)
            if self._owner != owner:
                self._owner = owner
                self._sync_play_buttons()

    def _schedule_regen(self) -> None:
        self._regen.setInterval(LIVE_REGENERATE_DELAY_MS if self.audio.playing == "pink" else REGENERATE_DELAY_MS)
        self._regen.start()

    def _control_hz(self, owner: str) -> float:
        if owner == "upper":
            return float(self.upper_slider.value())
        return float(self.session.primary[self.side])

    def _start(self, kind: str, owner: str) -> None:
        hz = self._control_hz(owner)
        if kind == "tone":
            self.audio.play_tone(hz, self.side)
        elif kind == "focus":
            self.audio.play_focus_sweep(hz, self.side)
        else:
            self.audio.set_eq_enabled(True)  # the point is to hear the EQ while moving the resonance
            self.audio.play_pink(self.side)
        self._owner = owner

    def _toggle(self, kind: str, owner: str) -> None:
        if self.audio.playing == kind and self._owner == owner:
            self.audio.stop_all()
        elif self.session is not None:
            self._start(kind, owner)
        self._sync_play_buttons()

    def _ear_changed(self, _index) -> None:
        if self._syncing or self.session is None:
            return
        self._sync_controls()
        self._show_eq_curves()
        playing = self.audio.playing
        if playing in ("tone", "focus", "pink") and self._owner in ("primary", "upper"):
            self._start(playing, self._owner)
        elif playing == "tone":
            self.audio.route(self.side)

    def _sync_play_buttons(self) -> None:
        playing = self.audio.playing
        if playing is None:
            self._owner = None
        self.primary.set_playing(playing if self._owner == "primary" else None)
        self.upper.set_playing(playing if self._owner == "upper" else None)
        self.pink_hint.setVisible(playing == "pink" and self._owner in ("primary", "upper"))

    def show_details(self) -> None:
        if self.session is None:
            return
        lines = []
        for side in self.session.sides:
            model = self.session.measurements.models[side]
            lines.append(f"{side_name(side)}: {model.describe(self.session.primary[side])}")
        lines += list(self.session.measurements.warnings)
        QMessageBox.information(self, tr("Details"), "\n".join(lines))

    # ── ② target ───────────────────────────────────────────────────────────
    def _target_chosen(self, index: int) -> None:
        if self._syncing or self.session is None or not 0 <= index < len(self._targets):
            return
        item = self._targets[index]
        try:
            self.session.set_target(item.freqs, item.db, item.name, item.path)
        except ValueError as exc:
            self.target_problem.setText(tr("This target can't be used: {reason}").format(reason=exc))
            return
        self._sync_default_chip()
        self.regenerate()

    def _browse_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import a target"), "",
                                              tr("Target curves (*.txt *.csv);;All files (*)"))
        if path:
            self.import_target(path)

    def import_target(self, path) -> bool:
        try:
            info = self.ctx.targets.import_file(path)
        except (OSError, ValueError) as exc:
            self.target_problem.setText(tr("{name} can't be used as a target: {reason}").format(
                name=Path(path).name, reason=exc))
            return False
        self.target_problem.setText("")
        manifest_default = self.loaded.manifest.default_target if self.loaded else None
        default = self.ctx.targets.default_for(manifest_default)
        self._targets = [_TargetItem(t.name, t.freqs, t.db, t.path, default is not None and t.path == default.path)
                         for t in self.ctx.targets.targets]
        index = next(i for i, t in enumerate(self._targets) if t.path == str(info.path))
        self._fill_target_combo(None)
        self.target_combo.setCurrentIndex(index)  # selects and regenerates
        return True

    # ── ③ upper resonance ──────────────────────────────────────────────────
    def _toggle_advanced(self, open_: bool) -> None:
        self.advanced_body.setVisible(open_)
        self.advanced_toggle.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)

    def _set_upper_enabled(self, enabled: bool) -> None:
        if self._syncing or self.session is None:
            return
        self.session.upper_enabled = bool(enabled)
        self._sync_upper_controls()
        self.regenerate()

    def _upper_moved(self, hz: int) -> None:
        if self._syncing or self.session is None:
            return
        self.upper.set_value(hz)
        self.graph.set_upper_marker(hz if self.session.upper_enabled else None)
        if not self.session.upper_enabled:
            return
        self.session.set_upper(self.side, hz)
        self._follow("upper", hz)
        self._schedule_regen()

    def _reset_upper(self) -> None:
        if self.session is None:
            return
        self.session.clear_upper(self.side)
        self._sync_upper_controls()
        self.regenerate()

    def _predicted_upper(self) -> int:
        model = self.model
        try:
            hz = detect_high_treble_peak(model.freqs, model.predict(self.session.primary[self.side]),
                                         self.session.primary[self.side])
        except ValueError:
            hz = None
        lo, hi = PERSONAL_TREBLE_RANGE
        return int(round(min(max(hz or DEFAULT_UPPER_HZ, lo), hi)))

    # ── syncing widgets ────────────────────────────────────────────────────
    def _sync_controls(self) -> None:
        self._sync_peak_controls()
        self._sync_upper_controls()
        self._show_iem_curve()

    def _sync_peak_controls(self) -> None:
        hz = int(round(self.session.primary[self.side]))
        self._syncing = True
        self.peak_slider.setValue(hz)
        self.peak_spin.setValue(hz)
        self._syncing = False
        self.graph.set_marker(hz)
        self.readout.setText(f"{hz:,} Hz")
        self.readout_side.setText(tr("{side} resonance").format(side=side_name(self.side)))
        peaks = self.model.peaks
        lo, hi = float(np.min(peaks)), float(np.max(peaks))
        outside = not lo <= hz <= hi
        if outside:
            self.range_warning.setText(tr(
                "{hz} Hz is outside what was measured for this IEM ({lo:.0f}–{hi:.0f} Hz), so the EQ "
                "there is an estimate. If it sounds right, that's fine.").format(hz=hz, lo=lo, hi=hi))
        self.range_warning.setVisible(outside)

    def _sync_upper_controls(self) -> None:
        enabled = self.session.upper_enabled
        self._syncing = True
        self.upper_enabled.setChecked(enabled)
        hz = int(round(self.session.upper.get(self.side) or self._predicted_upper()))
        self.upper.set_value(hz)
        self._syncing = False
        self.upper.setEnabled(enabled)
        self.graph.set_upper_marker(hz if enabled else None)

    # ── EQ and graph ───────────────────────────────────────────────────────
    def regenerate(self) -> None:
        self._regen.stop()
        if self.session is None or self.session.target is None:
            return
        try:
            self.channel_eq = self.session.channel_eq()
        except ValueError as exc:
            self.range_warning.setText(str(exc))
            self.range_warning.show()
            return
        self._show_eq_curves()
        self.remember()
        self.eqChanged.emit(self.channel_eq)

    def _show_iem_curve(self) -> None:
        model = self.model
        if self.session.target is not None:
            # same 1 kHz alignment as the matched curves
            from eq_helper.dsp.resonance import match_target
            eq = match_target(model, self.session.primary[self.side], *self.session.target,
                              self.session.target_name)
            self.graph.set_curve("iem", eq.freqs, eq.predicted_db)
            self.graph.set_curve("target", eq.freqs, eq.target_db)
        else:
            self.graph.set_curve("iem", model.freqs, model.predict(self.session.primary[self.side]))
            self.graph.set_curve("target", None, None)

    def _show_eq_curves(self) -> None:
        if self.channel_eq is None or self.side not in self.channel_eq.channels:
            self.graph.set_curve("with_eq", None, None)
            return
        ch = self.channel_eq.channels[self.side]
        self.graph.set_curve("iem", ch.freqs, ch.predicted_db)
        self.graph.set_curve("target", ch.freqs, ch.target_db)
        self.graph.set_curve("with_eq", ch.freqs, ch.predicted_db + ch.correction_db)

    # ── history support ────────────────────────────────────────────────────
    def apply_tuning(self, tuning, target) -> None:
        """Restore a saved tuning (with its stored target curve) into every control."""
        self.session.restore(tuning, target)
        wanted = target_hash(*self.session.target)
        index = next((i for i, t in enumerate(self._targets) if t.hash == wanted), None)
        if index is None:
            self._targets.append(_TargetItem(tr("{name} (saved)").format(name=tuning.target_name),
                                             *self.session.target, None, False))
            self._targets[-1].name = tuning.target_name
            index = len(self._targets) - 1
            self._fill_target_combo(index)
            self.target_combo.setItemText(index, tr("{name} (saved)").format(name=tuning.target_name))
        else:
            self._fill_target_combo(index)
        self._sync_controls()
        self.regenerate()
