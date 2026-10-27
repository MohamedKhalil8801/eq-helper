"""Steps ①–③: find your resonance, choose a target, optional upper treble.

Driven by :class:`~eq_helper.calibration.tuning.TuningSession`. Step ① starts
with the guided test ("Find it for me", :mod:`.guided_test_view`), which
takes the place of step ①'s contents while it runs. "Adjust by hand" ports
the behaviour of EQ Helper's ResonanceDialog in plain language: moving the
slider (or dragging the marker) updates the "Your IEM" curve at once and
regenerates the EQ after a short pause; a playing tone follows the slider.
"""

from __future__ import annotations

import atexit
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSizePolicy, QSlider, QSpinBox, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from eq_helper.calibration.tuning import TuningSession, target_hash
from eq_helper.dsp.resonance import CONTROL_RANGE, PERSONAL_TREBLE_RANGE, detect_high_treble_peak

from .common import Card, Worker, chip, hint, side_name, tr
from .guided_test_view import GuidedTestView
from .response_graph import ResponseGraph

_LIVE_JOBS: set = set()  # keep running workers alive even if the panel goes away


@atexit.register
def _join_live_jobs() -> None:
    for job in list(_LIVE_JOBS):
        job.wait(5000)


REGENERATE_DELAY_MS = 250
CURVE_REDRAW_MS = 15  # coalesce "Your IEM" redraws while a slider or line is dragged
LIVE_REGENERATE_DELAY_MS = 60   # while pink noise plays through the EQ: follow the slider closely
DEFAULT_UPPER_HZ = 15500


class ResonanceRow(QWidget):
    """Label + slider + Hz box for one resonance (primary or upper): both work the same way."""

    valueChanged = Signal(int)

    def __init__(self, label: str, accessible: str, hz_range, step: int, page: int, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.label.setMinimumWidth(self.label.fontMetrics().horizontalAdvance(tr("Upper treble")) + 12)
        row.addWidget(self.label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(*hz_range)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(page)
        self.slider.setMinimumHeight(32)
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
        self.extra = QHBoxLayout()
        row.addLayout(self.extra)

    def set_value(self, hz: int) -> None:
        for widget in (self.slider, self.spin):
            widget.blockSignals(True)
            widget.setValue(int(hz))
            widget.blockSignals(False)

    def value(self) -> int:
        return self.slider.value()

    def set_focused(self, focused: bool) -> None:
        font = self.label.font()
        font.setBold(focused)
        self.label.setFont(font)


# (kind, idle label, playing label)
SOUNDS = (("tone", "▶ Play tone", "■ Stop tone"),
          ("focus", "〰 Sweep around it", "■ Stop sweep"),
          ("pink", "≈ Pink noise + EQ", "■ Stop noise"))


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
        self._graph_tone = False  # the playing tone came from a press on the graph, not a resonance
        self._regen = QTimer(self)
        self._regen.setSingleShot(True)
        self._regen.setInterval(REGENERATE_DELAY_MS)
        self._regen.timeout.connect(self._regenerate_live)
        self._live_job = None
        self._live_again = False
        self._sync_generation = 0
        self._iem_redraw = QTimer(self)
        self._iem_redraw.setSingleShot(True)
        self._iem_redraw.setInterval(CURVE_REDRAW_MS)
        self._iem_redraw.timeout.connect(self._show_iem_curve)
        self._playing_poll = QTimer(self)
        self._playing_poll.setInterval(40)
        self._playing_poll.timeout.connect(self._show_playing)
        self._guided_eq = False  # an EQ was made while the guided test ran: hand it over when it closes

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._build_step1(), 1)
        lower = QHBoxLayout()
        self.step2 = self._build_step2()
        self.step3 = self._build_step3()
        lower.addWidget(self.step2, 1)
        lower.addWidget(self.step3, 1)
        outer.addLayout(lower)
        audio.on_change(self._sync_play_buttons)

    # ── layout ─────────────────────────────────────────────────────────────
    def _build_step1(self) -> Card:
        card = Card(tr("① Find your resonance"))
        self.step1_stack = QStackedWidget()
        card.body.addWidget(self.step1_stack, 1)
        self.manual_page = QWidget()
        body = QVBoxLayout(self.manual_page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.step1_stack.addWidget(self.manual_page)
        self.guided = GuidedTestView(self.audio, self._option_eqs, self._balance_job, self._upper_prediction)
        self.guided.resultFound.connect(self._guided_result)
        self.guided.upperFound.connect(self._guided_upper)
        self.guided.adjustByHand.connect(self._guided_to_manual)
        self.guided.finished.connect(self._guided_finished)
        self.step1_stack.addWidget(self.guided)

        start = QHBoxLayout()
        self.find_button = QPushButton(tr("Find it for me (about 2 minutes)"))
        self.find_button.setObjectName("primary")
        self.find_button.setToolTip(tr("A short listening test: which sound is louder, then which is smoothest, "
                                       "for each ear."))
        self.find_button.clicked.connect(self.start_guided_test)
        start.addWidget(self.find_button)
        self.by_hand_button = QPushButton(tr("Adjust by hand"))
        self.by_hand_button.setCheckable(True)
        self.by_hand_button.setToolTip(tr("Find it yourself with a tone and the slider."))
        self.by_hand_button.toggled.connect(self._show_manual)
        start.addWidget(self.by_hand_button)
        start.addStretch(1)
        body.addLayout(start)
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
        body.addLayout(top)
        self.hint = hint(tr(
            "Press Play tone, then move the slider (or drag the orange line) until the tone sounds loudest "
            "or most piercing in this ear — that's your resonance. Do the same for the other ear. Click or "
            "drag on the graph to hear any frequency. Keep the volume low."))
        self.other_eq = QLabel()
        self.other_eq.setObjectName("hint")
        self.other_eq.setWordWrap(True)
        self.other_eq_off = QPushButton(tr("Turn it off in Equalizer APO"))
        self.other_eq_off.hide()
        self.other_eq_off.clicked.connect(self.apoOffRequested)
        other_row = QHBoxLayout()
        other_row.addWidget(self.other_eq, 1)
        other_row.addWidget(self.other_eq_off, 0, Qt.AlignTop)
        body.addLayout(other_row)
        self.set_other_eqs([], False)

        self.graph = ResponseGraph()
        self.graph.setMinimumHeight(200)
        self.graph.set_marker_bounds(*CONTROL_RANGE)
        self.graph.set_upper_bounds(*PERSONAL_TREBLE_RANGE)
        self.graph.markerMoved.connect(self._marker_dragged)
        self.graph.upperMoved.connect(self._upper_dragged)
        self.graph.frequencyClicked.connect(self._graph_clicked)
        body.addWidget(self.graph, 1)

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
        readout.addStretch(1)
        row.addLayout(readout)
        self.manual_box = QWidget()  # "Adjust by hand": the slider and its sounds
        rows = QVBoxLayout(self.manual_box)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.addWidget(self.hint)
        self.primary = ResonanceRow(tr("Resonance"), tr("Resonance frequency"), CONTROL_RANGE, 10, 100)
        self.primary.valueChanged.connect(self.set_primary_hz)
        self.peak_slider, self.peak_spin = self.primary.slider, self.primary.spin
        rows.addWidget(self.primary)
        self.upper = ResonanceRow(tr("Upper treble"), tr("Upper resonance frequency"),
                                  PERSONAL_TREBLE_RANGE, 50, 250)
        self.upper.valueChanged.connect(self._upper_moved)
        self.upper_slider, self.upper_spin = self.upper.slider, self.upper.spin
        self.reset_upper = QPushButton(tr("Reset"))
        self.reset_upper.setToolTip(tr("Put this ear's upper treble back as calibrated."))
        self.reset_upper.clicked.connect(self._reset_upper)
        self.upper.extra.addWidget(self.reset_upper)
        self._reset_space = QWidget()  # keeps both sliders the same length
        self._reset_space.setFixedWidth(self.reset_upper.sizeHint().width())
        self.primary.extra.addWidget(self._reset_space)
        self.upper.hide()
        rows.addWidget(self.upper)

        # one set of sound buttons for whichever resonance is selected; each starts and stops its sound
        listen = QHBoxLayout()
        self.sound_buttons: dict[str, QPushButton] = {}
        for kind, idle, _playing in SOUNDS:
            button = QPushButton(tr(idle))
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=kind: self._toggle(k))
            listen.addWidget(button)
            self.sound_buttons[kind] = button
        self.tone_button = self.sound_buttons["tone"]
        self.sweep_button = self.sound_buttons["focus"]
        self.pink_button = self.sound_buttons["pink"]
        self.tone_button.setObjectName("primary")
        self.sweep_button.setToolTip(tr("A slow sweep around the selected frequency: the resonance is where "
                                        "it sounds loudest."))
        self.pink_button.setToolTip(tr("Pink noise through your EQ. Move the slider while it plays until the "
                                       "treble sounds smooth, with no harsh or hissy spot."))
        for button in self.sound_buttons.values():  # wide enough for either label: no jumping
            width = max(button.fontMetrics().horizontalAdvance(tr(t)) for _k, *texts in SOUNDS for t in texts)
            button.setMinimumWidth(width + 28)
        listen.addSpacing(8)
        self.focus_label = QLabel(tr("at"))
        self.focus_label.setObjectName("hint")
        listen.addWidget(self.focus_label)
        self.focus_primary = QPushButton(tr("Resonance"))
        self.focus_upper = QPushButton(tr("Upper treble"))
        self._focus_group = QButtonGroup(self)
        for button, which in ((self.focus_primary, "primary"), (self.focus_upper, "upper")):
            button.setCheckable(True)
            button.setObjectName("segment")
            self._focus_group.addButton(button)
            button.clicked.connect(lambda _=False, w=which: self.set_focus(w))
            listen.addWidget(button)
        listen.addStretch(1)
        rows.addLayout(listen)
        row.addWidget(self.manual_box, 1)
        self.manual_box.hide()
        self.manual_space = QWidget()  # keeps the readout on the left while the manual controls are hidden
        row.addWidget(self.manual_space, 1)
        body.addLayout(row)
        self.focus = "primary"
        self._sync_focus_widgets()
        self.pink_hint = hint(tr(
            "Pink noise is playing through your EQ. Move the slider slowly: where it's right, the treble "
            "sounds smooth and even, with no harsh, hissy or dull spot. Switch the EQ off and on to compare."))
        self.pink_hint.hide()
        body.addWidget(self.pink_hint)

        self.range_warning = QLabel()
        self.range_warning.setObjectName("warning")
        self.range_warning.setWordWrap(True)
        self.range_warning.hide()
        body.addWidget(self.range_warning)
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
        body.addWidget(hint(tr("Only if you hear a second, higher peak that still bothers you. Turn this on: "
                               "an Upper treble slider and a pink line appear in step ①. Find the peak the "
                               "same way, per ear. Off keeps the EQ as calibrated.")))
        self.upper_enabled = QCheckBox(tr("Adjust the upper treble"))
        self.upper_enabled.toggled.connect(self._set_upper_enabled)
        body.addWidget(self.upper_enabled)
        self.advanced_body.hide()
        card.body.addWidget(self.advanced_body)
        card.body.addStretch(1)
        return card

    def set_other_eqs(self, found: list[str], apo_on: bool, app: str = "Equalizer APO") -> None:
        """Warn about other EQs: any EQ already running is heard on top of this one.
        ``apo_on``: our own EQ is on in ``app`` (the system-wide EQ we apply to)."""
        self.other_eq_off.setText(tr("Turn it off in {app}").format(app=app))
        if apo_on:
            text = tr("Your EQ is on in {app}, so here you'd hear it twice. Turn it off while you "
                      "tune, then apply again when you're done.").format(app=app)
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
        self.guided.set_other_eq_text("⚠ " + text, bool(apo_on or found))

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
        self._close_guided()
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
        # found before: show the slider; the first time, the guided test is the way in
        self.by_hand_button.setChecked(bool(remembered.get("primary")))
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
        self._follow("primary", value)       # sound first: it must not wait for drawing
        self._sync_peak_controls()
        self._iem_redraw.start()
        self._schedule_regen()

    # ── ① the guided test ─────────────────────────────────────────────────
    @property
    def guided_active(self) -> bool:
        return self.step1_stack.currentWidget() is self.guided

    def start_guided_test(self, rng=None) -> None:
        """Show the guided test in place of step ①'s contents (intro first)."""
        if self.session is None or self.session.target is None:
            return
        self.audio.stop_all()
        defaults = {}
        for side in self.session.sides:
            peaks = self.session.measurements.models[side].peaks
            defaults[side] = float(np.clip(np.exp(np.median(np.log(peaks))), *CONTROL_RANGE))
        self._guided_eq = False
        self.guided.start(self.session.sides, defaults, rng)
        self.step1_stack.setCurrentWidget(self.guided)
        for card in (self.step2, self.step3):
            card.setEnabled(False)  # the test's EQs are made with today's target and upper treble

    def _option_eqs(self, side: str, hz_list, which: str = "primary"):
        """For the smoothness check: a worker job making one EQ (and its preview filters) per option,
        with only ``side``'s primary resonance — or, for ``which="upper"``, its upper resonance —
        changed. The session is copied now, on the GUI thread."""
        from eq_helper.dsp.resonance import match_channels
        s = self.session
        measurements, primary, target, name = s.measurements, dict(s.primary), s.target, s.target_name
        upper = dict(s.upper) if s.upper_enabled else None
        rate, audio = int(self.audio.engine.sample_rate), self.audio

        def work():
            built = []
            for hz in hz_list:
                if which == "upper":
                    eq = match_channels(measurements, primary, *target, name,
                                        personal_treble={**(upper or {}), side: float(hz)})
                else:
                    eq = match_channels(measurements, {**primary, side: float(hz)}, *target, name,
                                        personal_treble=upper)
                built.append((eq, audio.design_firs(eq, rate)))
            return built
        return work

    def _balance_job(self, rng):
        """For the balance check: the bands to centre, and a worker job making the current tuning's EQ,
        its preview filters and the model that reads the leans. The session is copied now."""
        from eq_helper.dsp.resonance import match_channels
        from resonancefit.balance_check import BalanceModel, plan_bands
        s = self.session
        primary = dict(s.primary)
        upper = dict(s.upper) if s.upper_enabled else None
        measurements, target, name = s.measurements, s.target, s.target_name
        rate, audio = int(self.audio.engine.sample_rate), self.audio
        bands = plan_bands(primary, upper, rng)

        def work():
            eq = match_channels(measurements, primary, *target, name, personal_treble=upper)
            return eq, audio.design_firs(eq, rate), BalanceModel(measurements.models, target, name, primary, upper)
        return bands, work

    def _select_side(self, side: str) -> None:
        index = next((i for i in range(self.ears.count()) if self.ears.tabData(i) == side), None)
        if index is not None and index != self.ears.currentIndex():
            self.ears.setCurrentIndex(index)

    def _guided_result(self, side: str, hz: float) -> None:
        """An ear is done: its resonance goes through the same path as the slider."""
        self._select_side(side)
        self.set_primary_hz(hz)

    def _guided_upper(self, side: str, hz) -> None:
        """An ear's upper resonance is done (``None``: no clear peak, leave the upper treble as it is)."""
        if hz is None or self.session is None:
            return
        self.session.set_upper(side, float(round(hz)))
        self.session.upper_enabled = True
        self._select_side(side)
        self._sync_upper_controls()
        self._schedule_regen()

    def _guided_to_manual(self, found: dict) -> None:
        """Leave the test for "Adjust by hand", keeping what's been found so far."""
        for side, hz in found.get("primary", {}).items():
            if self.session is not None and round(self.session.primary.get(side, 0)) != round(hz):
                self._select_side(side)
                self.set_primary_hz(hz)
        for side, hz in found.get("upper", {}).items():
            if self.session is not None and round(self.session.upper.get(side, 0)) != round(hz):
                self._guided_upper(side, hz)
        current = self.guided.test.side if self.guided.test is not None else None
        self._close_guided()
        self.by_hand_button.setChecked(True)
        if current is not None:
            self._select_side(current)

    def _guided_finished(self) -> None:
        self._close_guided()
        if self.session is not None:
            self._select_side(self.session.sides[0])

    def _close_guided(self) -> None:
        if not self.guided_active:
            return
        self.guided.stop()
        self.step1_stack.setCurrentWidget(self.manual_page)
        for card in (self.step2, self.step3):
            card.setEnabled(True)
        if self.channel_eq is not None:
            self.audio.set_eq(self.channel_eq)  # the test previewed its own EQs
            if self._guided_eq:
                self.eqChanged.emit(self.channel_eq)
        self._guided_eq = False

    def _show_manual(self, shown: bool) -> None:
        self.manual_box.setVisible(shown)
        self.manual_space.setVisible(not shown)

    def _marker_dragged(self, hz: float) -> None:
        self.set_primary_hz(hz)

    def _upper_dragged(self, hz: float) -> None:
        if self.session is None or not self.session.upper_enabled:
            return
        self._upper_moved(int(round(hz)))

    def _graph_clicked(self, hz: float) -> None:
        """Press/drag on the graph: a tone at that frequency (it doesn't move a resonance)."""
        if self.session is None:
            return
        if self.audio.playing == "tone" and self._graph_tone:
            self.audio.set_tone(hz)
            self._show_playing()
        else:
            self.audio.play_tone(hz, self.side)
            self._graph_tone = True
            self._sync_play_buttons()

    # ── which resonance the sound buttons play ─────────────────────────────
    def set_focus(self, which: str) -> None:
        """Select the resonance the sound buttons play at; a playing tone or sweep moves there."""
        if which == "upper" and not (self.session is not None and self.session.upper_enabled):
            which = "primary"
        changed = which != self.focus
        self.focus = which
        self._sync_focus_widgets()
        if changed and self.audio.playing in ("tone", "focus") and self.session is not None:
            self._start(self.audio.playing)

    def _sync_focus_widgets(self) -> None:
        upper_on = self.session is not None and self.session.upper_enabled
        if not upper_on:
            self.focus = "primary"
        for widget in (self.focus_label, self.focus_primary, self.focus_upper):
            widget.setVisible(upper_on)
        self.focus_primary.setChecked(self.focus == "primary")
        self.focus_upper.setChecked(self.focus == "upper")
        self.primary.set_focused(upper_on and self.focus == "primary")
        self.upper.set_focused(upper_on and self.focus == "upper")
        self._reset_space.setVisible(upper_on)
        self._sync_readout()

    def _sync_readout(self) -> None:
        """The big number shows the selected resonance."""
        if self.session is None or self.side is None:
            return
        side = side_name(self.side)
        if self.focus == "upper":
            self.readout_side.setText(tr("{side} upper treble").format(side=side))
            self.readout.setText(f"{self.upper_slider.value():,} Hz")
        else:
            self.readout_side.setText(tr("{side} resonance").format(side=side))
            self.readout.setText(f"{int(round(self.session.primary[self.side])):,} Hz")

    def _follow(self, which: str, hz: float) -> None:
        """Touching a slider or line selects it; a playing tone follows it."""
        if self.focus != which:
            self.focus = which
            self._sync_focus_widgets()
        if self.audio.playing == "tone":
            self.audio.set_tone(hz)
            self._graph_tone = False
            self._show_playing()

    def _schedule_regen(self) -> None:
        self._regen.setInterval(LIVE_REGENERATE_DELAY_MS if self.audio.playing == "pink" else REGENERATE_DELAY_MS)
        self._regen.start()

    def _focus_hz(self) -> float:
        if self.focus == "upper":
            return float(self.upper_slider.value())
        return float(self.session.primary[self.side])

    def _start(self, kind: str) -> None:
        if kind == "tone":
            self.audio.play_tone(self._focus_hz(), self.side)
        elif kind == "focus":
            self.audio.play_focus_sweep(self._focus_hz(), self.side)
        else:
            self.audio.set_eq_enabled(True)  # the point is to hear the EQ while moving the resonance
            self.audio.play_pink(self.side)
        self._graph_tone = False

    def _toggle(self, kind: str) -> None:
        """Each sound button starts its sound, and stops it when pressed again (as in EQ Helper)."""
        if self.audio.playing == kind:
            self.audio.stop_all()
        elif self.session is not None:
            self._start(kind)
        self._sync_play_buttons()

    def _ear_changed(self, _index) -> None:
        if self._syncing or self.session is None:
            return
        self._sync_controls()
        self._show_eq_curves()
        playing = self.audio.playing
        if playing in ("tone", "focus", "pink") and not self._graph_tone:
            self._start(playing)
        elif playing == "tone":
            self.audio.route(self.side)

    def _sync_play_buttons(self) -> None:
        playing = self.audio.playing
        if playing != "tone":
            self._graph_tone = False
        for kind, idle, active in SOUNDS:
            button = self.sound_buttons[kind]
            on = playing == kind
            button.setChecked(on)
            button.setText(tr(active) if on else tr(idle))
        self.pink_hint.setVisible(playing == "pink")
        if playing in ("tone", "focus", "sweep"):
            self._playing_poll.start()
        else:
            self._playing_poll.stop()
        self._show_playing()

    def _show_playing(self) -> None:
        self.graph.set_playing(self.audio.playing_frequency())

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
        if enabled:
            self.by_hand_button.setChecked(True)  # the upper treble is set by hand, under the resonance
        self._sync_upper_controls()
        self.set_focus("upper" if enabled else "primary")
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
        self._sync_readout()
        self._schedule_regen()

    def _reset_upper(self) -> None:
        if self.session is None:
            return
        self.session.clear_upper(self.side)
        self._sync_upper_controls()
        self.regenerate()

    def _upper_prediction(self, side: str, primary_hz: float) -> float | None:
        """Where the calibration puts ``side``'s upper peak for this primary resonance (None: no peak)."""
        model = self.session.measurements.models[side]
        try:
            hz = detect_high_treble_peak(model.freqs, model.predict(float(primary_hz)), float(primary_hz))
        except ValueError:
            return None
        return None if hz is None else float(hz)

    def _predicted_upper(self) -> int:
        hz = self._upper_prediction(self.side, self.session.primary[self.side])
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
        self._sync_readout()
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
        self.upper.setVisible(enabled)
        self.graph.set_upper_marker(hz if enabled else None)
        self._sync_focus_widgets()

    # ── EQ and graph ───────────────────────────────────────────────────────
    def _regenerate_live(self) -> None:
        """Rebuild the EQ and its preview filters on a worker while sliders move (latest wins)."""
        if self.session is None or self.session.target is None:
            return
        if self._live_job is not None:
            self._live_again = True
            return
        from eq_helper.dsp.resonance import match_channels
        s = self.session
        measurements, primary, target, name = s.measurements, dict(s.primary), s.target, s.target_name
        upper = dict(s.upper) if s.upper_enabled else None
        rate, audio, generation = int(self.audio.engine.sample_rate), self.audio, self._sync_generation

        def work():
            eq = match_channels(measurements, primary, *target, name, personal_treble=upper)
            return generation, eq, audio.design_firs(eq, rate)

        job = Worker(work)
        job.finished_ok.connect(self._live_done)
        job.failed.connect(self._live_failed)
        job.finished.connect(lambda: _LIVE_JOBS.discard(job))
        _LIVE_JOBS.add(job)
        self._live_job = job
        job.start()

    def _live_finished(self) -> None:
        self._live_job = None
        if self._live_again:
            self._live_again = False
            self._regenerate_live()

    def _live_done(self, result) -> None:
        generation, eq, designed = result
        self._live_finished()
        if generation != self._sync_generation or self.session is None:
            return  # a direct regenerate (target, history…) happened since: it wins
        self.channel_eq = eq
        self._show_eq_curves()
        self.remember()
        if self.guided_active:  # the test is previewing its own EQs: hand this one over when it closes
            self._guided_eq = True
            return
        self.audio.offer_firs(eq, designed)
        self.eqChanged.emit(eq)

    def _live_failed(self, message: str) -> None:
        self._live_finished()
        self.range_warning.setText(message)
        self.range_warning.show()

    def regenerate(self) -> None:
        self._regen.stop()
        self._sync_generation += 1
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
        if self.guided_active:
            self._guided_eq = True
            return
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
