"""Audio controls: play buttons, frequency/volume sliders, channel switch."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from eq_helper.audio.engine import AudioEngine, list_output_devices
from eq_helper.audio.shared_state import ChannelMode, DualRouting, Mode
from eq_helper.ui.volume_memory import VolumeMemory
from eq_helper.ui.widgets import LabeledSlider, ThreeWaySwitch

_ACTIVE_STYLE = "QPushButton { background-color: #2a6e2a; color: white; font-weight: bold; }"
_INACTIVE_STYLE = ""


def _freq_formatter(value: float) -> str:
    if value >= 1000.0:
        return f"{value / 1000.0:.2f} kHz"
    return f"{value:.0f} Hz"


def _db_formatter(value: float) -> str:
    return f"{value:.1%}"


class AudioPanel(QGroupBox):
    """Group box wrapping all source-generator controls.

    Each play button is a toggle: pressing it starts that source; pressing
    it again (or pressing a different source) stops playback. The active
    button is highlighted. Spacebar stops all playback.

    Grey noise plays through the engine (looped from a bundled WAV) like the
    other sources; its loudness uses a separately-remembered volume, since grey
    noise is usually run much louder than tones and sweeps.
    """

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__("Audio Controls", parent)
        self._engine = engine
        self._active_btn: Optional[QPushButton] = None
        # Separate remembered volumes for Grey vs everything else; real values
        # are loaded in apply_settings().
        self._volume = VolumeMemory(regular=0.08, grey=0.08)
        self._build_ui()
        self._wire()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Play buttons (no Stop button — each is a toggle)
        btn_row = QHBoxLayout()
        self.btn_sine = QPushButton("Sine")
        self.btn_pink = QPushButton("Pink")
        self.btn_grey = QPushButton("Grey")
        self.btn_sweep = QPushButton("Sweep")
        self.btn_focus = QPushButton("Focus Sweep")
        self.btn_focus.setToolTip(
            "Looping ping-pong sweep around the current frequency — for judging "
            "smoothness. (Shift+click the spectrum to centre it there.)"
        )
        self._source_buttons = [
            self.btn_sine, self.btn_pink, self.btn_grey, self.btn_sweep, self.btn_focus
        ]
        for b in self._source_buttons:
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        # Frequency slider (log mapping: 20 Hz - 20 kHz)
        self.freq_slider = LabeledSlider(
            label="Sine frequency",
            minimum=math.log10(20.0),
            maximum=math.log10(20000.0),
            value=math.log10(1000.0),
            step=0.01,
            formatter=lambda logv: _freq_formatter(10 ** logv),
        )
        layout.addWidget(self.freq_slider)

        # Volume slider (0-100%)
        self.vol_slider = LabeledSlider(
            label="Volume",
            minimum=0.0,
            maximum=1.0,
            value=0.08,
            step=0.005,
            formatter=_db_formatter,
        )
        layout.addWidget(self.vol_slider)

        # Sweep duration slider (1-60 s) — applies to the full-range Sweep.
        self.sweep_duration = LabeledSlider(
            label="Sweep duration",
            minimum=1.0,
            maximum=60.0,
            value=10.0,
            step=0.5,
            suffix=" s",
        )
        layout.addWidget(self.sweep_duration)

        # Focused-sweep controls: band width (½/1/2 octaves around the current
        # frequency) and glide speed in seconds per octave.
        focus_row = QHBoxLayout()
        focus_row.addWidget(QLabel("Focus sweep:"))
        self.focus_width = ThreeWaySwitch(("½ oct", "1 oct", "2 oct"), initial=1)
        focus_row.addWidget(self.focus_width, 1)
        layout.addLayout(focus_row)

        self.focus_speed = LabeledSlider(
            label="Focus speed",
            minimum=1.0,
            maximum=6.0,
            value=3.0,
            step=0.5,
            suffix=" s/oct",
        )
        layout.addWidget(self.focus_speed)

        # Channel switch + mono toggle
        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Channel:"))
        self.channel_switch = ThreeWaySwitch(("L", "Both", "R"), initial=1)
        chan_row.addWidget(self.channel_switch, 1)
        layout.addLayout(chan_row)

        options_row = QHBoxLayout()
        self.mono_check = QCheckBox("Force mono")
        self.mono_check.setChecked(True)
        self.loop_check = QCheckBox("Loop sweep")
        self.loop_check.setChecked(True)
        self.dual_check = QCheckBox("Dual output")
        options_row.addWidget(self.mono_check)
        options_row.addWidget(self.loop_check)
        options_row.addWidget(self.dual_check)
        options_row.addStretch()
        layout.addLayout(options_row)

        self._dual_container = QWidget()
        dual_layout = QVBoxLayout(self._dual_container)
        dual_layout.setContentsMargins(16, 0, 0, 0)

        dev_a_row = QHBoxLayout()
        dev_a_row.addWidget(QLabel("Device 1:"))
        self.device_a_combo = QComboBox()
        self.device_a_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        dev_a_row.addWidget(self.device_a_combo, 1)
        dual_layout.addLayout(dev_a_row)

        dev_b_row = QHBoxLayout()
        dev_b_row.addWidget(QLabel("Device 2:"))
        self.device_b_combo = QComboBox()
        self.device_b_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        dev_b_row.addWidget(self.device_b_combo, 1)
        dual_layout.addLayout(dev_b_row)

        routing_row = QHBoxLayout()
        routing_row.addWidget(QLabel("Routing:"))
        self.routing_mirror = QRadioButton("Mirror")
        self.routing_split = QRadioButton("Split L/R")
        self.routing_mirror.setChecked(True)
        routing_row.addWidget(self.routing_mirror)
        routing_row.addWidget(self.routing_split)
        routing_row.addStretch()
        dual_layout.addLayout(routing_row)

        self._dual_container.hide()
        layout.addWidget(self._dual_container)

        self._populate_device_combos()

    def _wire(self) -> None:
        self.btn_sine.clicked.connect(lambda: self._toggle_source(self.btn_sine))
        self.btn_pink.clicked.connect(lambda: self._toggle_source(self.btn_pink))
        self.btn_grey.clicked.connect(lambda: self._toggle_source(self.btn_grey))
        self.btn_sweep.clicked.connect(lambda: self._toggle_source(self.btn_sweep))
        self.btn_focus.clicked.connect(lambda: self._toggle_source(self.btn_focus))

        self.freq_slider.valueChanged.connect(
            lambda logv: self._engine.set_freq(10 ** logv)
        )
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.sweep_duration.valueChanged.connect(self._engine.set_sweep_duration)
        self.focus_width.selectionChanged.connect(self._on_focus_param_changed)
        self.focus_speed.valueChanged.connect(self._on_focus_param_changed)

        self.channel_switch.selectionChanged.connect(
            lambda idx: self._engine.set_channel_mode(ChannelMode(idx))
        )
        self.mono_check.toggled.connect(self._engine.set_mono)
        self.loop_check.toggled.connect(self._engine.set_sweep_loop)

        self.dual_check.toggled.connect(self._on_dual_toggled)
        self.device_a_combo.currentIndexChanged.connect(self._on_device_a_changed)
        self.device_b_combo.currentIndexChanged.connect(self._on_device_b_changed)
        self.routing_mirror.toggled.connect(self._on_routing_changed)
        self.routing_split.toggled.connect(self._on_routing_changed)

    # -------- source toggle logic

    def _toggle_source(self, btn: QPushButton) -> None:
        """Toggle a source button. If it's already active, stop. If a different
        source is active, stop it first then start the new one."""
        if btn is self._active_btn:
            # Same button pressed again → stop
            self.stop_all()
            return

        # Different source (or nothing playing) → stop previous, start new
        self._stop_all_internal()

        if btn is self.btn_sine:
            self._on_play_sine()
        elif btn is self.btn_pink:
            self._engine.start_pink()
        elif btn is self.btn_grey:
            self._engine.start_grey()
        elif btn is self.btn_sweep:
            self._on_play_sweep()
        elif btn is self.btn_focus:
            self._on_play_focus_sweep()

        self._set_active(btn)

    def _set_active(self, btn: Optional[QPushButton]) -> None:
        """Highlight the active button and clear others, then apply that
        source's remembered volume (the single place the context changes)."""
        for b in self._source_buttons:
            b.setStyleSheet(_ACTIVE_STYLE if b is btn else _INACTIVE_STYLE)
        self._active_btn = btn
        self._apply_volume_for_context()

    # -------- per-context volume (Grey vs everything else)

    def _is_grey_context(self) -> bool:
        return self._active_btn is self.btn_grey

    def _on_volume_changed(self, value: float) -> None:
        """User moved the volume slider: apply it and remember it for the active
        context. Fires only on real drags — LabeledSlider.set_value() blocks
        signals, so programmatic context switches don't re-save here."""
        self._engine.set_volume(value)
        self._volume.remember(self._is_grey_context(), value)

    def _apply_volume_for_context(self) -> None:
        """Set slider + engine to the remembered volume for the active source
        (Grey -> its level; any other source or idle -> regular)."""
        value = self._volume.current(self._is_grey_context())
        self.vol_slider.set_value(value)  # blocks signals -> no re-save
        self._engine.set_volume(value)

    def _stop_all_internal(self) -> None:
        """Stop the engine source."""
        self._engine.stop()

    def stop_all(self) -> None:
        """Public stop method — called from spacebar shortcut."""
        if self._active_btn is None:
            return
        self._stop_all_internal()
        self._set_active(None)

    def _on_play_sine(self) -> None:
        logv = self.freq_slider.value()
        self._engine.start_sine(freq=10 ** logv)

    def _on_play_sweep(self) -> None:
        self._engine.start_sweep(
            f_start=20.0,
            f_end=20000.0,
            duration_s=self.sweep_duration.value(),
            loop=self.loop_check.isChecked(),
        )

    def _focus_width_octaves(self) -> float:
        return {0: 0.5, 1: 1.0, 2: 2.0}[self.focus_width.index()]

    def _on_play_focus_sweep(self) -> None:
        self._engine.start_focused_sweep(
            center_hz=10 ** self.freq_slider.value(),
            width_octaves=self._focus_width_octaves(),
            seconds_per_octave=self.focus_speed.value(),
        )

    def trigger_focus_sweep(self) -> None:
        """Start a focused sweep around the current frequency and highlight the
        button. Used by Shift+click on the spectrum."""
        self._stop_all_internal()
        self._on_play_focus_sweep()
        self._set_active(self.btn_focus)

    def _on_focus_param_changed(self, *_args: object) -> None:
        """Width/speed changed: apply live to a running focused sweep (no
        restart). A no-op for any other source."""
        if self._active_btn is self.btn_focus:
            self._engine.update_focused_sweep(
                self._focus_width_octaves(), self.focus_speed.value()
            )

    # -------- dual output

    def _populate_device_combos(self) -> None:
        devices = list_output_devices()
        for combo in (self.device_a_combo, self.device_b_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("System default", None)
            for dev_id, name in devices:
                combo.addItem(name, dev_id)
            combo.blockSignals(False)

    def _on_dual_toggled(self, checked: bool) -> None:
        self._dual_container.setVisible(checked)
        self._engine.set_dual_output(checked)

    def _on_device_a_changed(self, _idx: int) -> None:
        dev_id = self.device_a_combo.currentData()
        self._engine.set_device_a(dev_id)

    def _on_device_b_changed(self, _idx: int) -> None:
        dev_id = self.device_b_combo.currentData()
        self._engine.set_device_b(dev_id)

    def _on_routing_changed(self, _checked: bool) -> None:
        if self.routing_mirror.isChecked():
            self._engine.set_dual_routing(DualRouting.MIRROR)
        else:
            self._engine.set_dual_routing(DualRouting.SPLIT_LR)

    def _find_device_combo_index(self, combo: QComboBox, device_id) -> int:
        if device_id is None:
            return 0
        for i in range(combo.count()):
            if combo.itemData(i) == device_id:
                return i
        return 0

    # -------- settings application

    def apply_settings(self, settings: dict) -> None:
        freq = float(settings.get("sine_freq_hz", 1000.0))
        self.freq_slider.set_value(math.log10(max(20.0, min(20000.0, freq))))
        regular_vol = float(settings.get("volume", 0.08))
        grey_vol = float(settings.get("grey_volume", regular_vol))
        self._volume = VolumeMemory(regular=regular_vol, grey=grey_vol)
        self.vol_slider.set_value(regular_vol)  # idle shows the regular volume
        self.sweep_duration.set_value(float(settings.get("sweep_duration_s", 10.0)))
        self.channel_switch.set_index(
            {"LEFT": 0, "BOTH": 1, "RIGHT": 2}.get(settings.get("channel_mode", "BOTH"), 1)
        )
        self.mono_check.setChecked(bool(settings.get("force_mono", True)))
        self.loop_check.setChecked(bool(settings.get("sweep_loop", True)))
        width_oct = float(settings.get("focus_width_octaves", 1.0))
        self.focus_width.set_index({0.5: 0, 1.0: 1, 2.0: 2}.get(width_oct, 1))
        self.focus_speed.set_value(float(settings.get("focus_speed_s_per_oct", 3.0)))

        # Dual output
        dev_a = settings.get("device_a_id")
        dev_b = settings.get("device_b_id")
        self.device_a_combo.blockSignals(True)
        self.device_b_combo.blockSignals(True)
        self.device_a_combo.setCurrentIndex(self._find_device_combo_index(self.device_a_combo, dev_a))
        self.device_b_combo.setCurrentIndex(self._find_device_combo_index(self.device_b_combo, dev_b))
        self.device_a_combo.blockSignals(False)
        self.device_b_combo.blockSignals(False)
        routing = settings.get("dual_routing", "MIRROR")
        self.routing_mirror.setChecked(routing == "MIRROR")
        self.routing_split.setChecked(routing == "SPLIT_LR")
        dual = bool(settings.get("dual_output", False))
        self.dual_check.setChecked(dual)
        self._dual_container.setVisible(dual)

        # Push to engine so the initial state matches the UI.
        self._engine.set_freq(freq)
        self._engine.set_volume(regular_vol)
        self._engine.set_sweep_duration(float(settings.get("sweep_duration_s", 10.0)))
        self._engine.set_channel_mode(ChannelMode(self.channel_switch.index()))
        self._engine.set_mono(self.mono_check.isChecked())
        self._engine.set_sweep_loop(self.loop_check.isChecked())
        self._engine.set_device_a(dev_a)
        self._engine.set_device_b(dev_b)
        if self.routing_split.isChecked():
            self._engine.set_dual_routing(DualRouting.SPLIT_LR)
        self._engine.set_dual_output(dual)

    def collect_settings(self, settings: dict) -> None:
        settings["sine_freq_hz"] = 10 ** self.freq_slider.value()
        settings["volume"] = self._volume.regular
        settings["grey_volume"] = self._volume.grey
        settings["sweep_duration_s"] = self.sweep_duration.value()
        settings["channel_mode"] = {0: "LEFT", 1: "BOTH", 2: "RIGHT"}[self.channel_switch.index()]
        settings["force_mono"] = self.mono_check.isChecked()
        settings["sweep_loop"] = self.loop_check.isChecked()
        settings["focus_width_octaves"] = self._focus_width_octaves()
        settings["focus_speed_s_per_oct"] = self.focus_speed.value()
        settings["dual_output"] = self.dual_check.isChecked()
        settings["device_a_id"] = self.device_a_combo.currentData()
        settings["device_b_id"] = self.device_b_combo.currentData()
        settings["dual_routing"] = "SPLIT_LR" if self.routing_split.isChecked() else "MIRROR"
