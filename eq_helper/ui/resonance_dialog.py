"""Measurement-trained, per-ear resonance prediction and APO auditioning."""

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QMessageBox, QPushButton, QTabBar, QVBoxLayout, QWidget,
)

from eq_helper.apo.errors import ApoError
from eq_helper.apo.writer import atomic_write_with_backup, poke
from eq_helper.audio.shared_state import ChannelMode
from eq_helper.calibration.tuning import TuningSession
from eq_helper.dsp.overlay import load_frequency_response
from eq_helper.dsp.resonance import (
    CONTROL_RANGE, PERSONAL_TREBLE_RANGE, ChannelEQ, Measurements, match_target,
)
from eq_helper.ui.spectrum_view import SpectrumView
from eq_helper.ui.widgets import LabeledSlider, ThreeWaySwitch


class _MeasurementLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, folder, parent):
        super().__init__(parent)
        self.folder = folder

    def run(self):
        try:
            self.loaded.emit(Measurements.from_folder(self.folder))
        except Exception as exc:
            self.failed.emit(str(exc))


@dataclass
class _Version:
    eq: ChannelEQ
    measurements: Measurements
    target: tuple
    peaks: dict


class ResonanceDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.setWindowTitle("Resonance EQ — measurement-trained L/R")
        self.resize(1100, 810)
        self.main = main
        # Tuning state lives in a Qt-free session shared with the customer app;
        # the properties below keep this dialog's existing attribute names.
        self.session = TuningSession(Measurements.bundled())
        self.session.primary = {}  # sides appear once measurements are installed
        self.personal_treble_peaks = self._load_personal_treble_peaks()
        self.versions = []
        self._selected = None
        self._generated = False
        self._loader = None
        self._preview_preset = None
        self._preview_eq = None
        self._previous_preset = None
        self._previous_channel = main.audio_panel.channel_switch.index()
        self._library = main.profile_panel._library()
        self._device = main.profile_panel._current_device()

        layout = QVBoxLayout(self)
        measurements = QHBoxLayout()
        self.load_measurements_button = QPushButton("Load measurement folder…")
        self.load_measurements_button.clicked.connect(self._pick_measurements)
        measurements.addWidget(self.load_measurements_button)
        self.measurements_label = QLabel()
        measurements.addWidget(self.measurements_label, 1)
        details = QPushButton("Model details…")
        details.clicked.connect(self._details)
        measurements.addWidget(details)
        layout.addLayout(measurements)
        files = QHBoxLayout()
        load = QPushButton("Load target…")
        load.clicked.connect(self._pick_target)
        files.addWidget(load)
        self.target_label = QLabel("Choose a target TXT or CSV file")
        files.addWidget(self.target_label, 1)
        layout.addLayout(files)
        hint = QLabel(
            "Find the strongest treble peak for each supplied ear. Click for a sine; "
            "Shift+click for a focus sweep; drag the orange marker to move the peak. "
            "L/R levels are preserved: measure both sides at the same volume and calibration."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        ear_row = QHBoxLayout()
        self.ears = QTabBar()
        self.ears.setExpanding(False)
        self.ears.currentChanged.connect(self._change_ear)
        ear_row.addWidget(self.ears, 1)
        self.listen_both = QCheckBox("Listen on both channels")
        self.listen_both.toggled.connect(self._route_audio)
        ear_row.addWidget(self.listen_both)
        layout.addLayout(ear_row)
        self.model_info = QLabel()
        self.model_info.setWordWrap(True)
        layout.addWidget(self.model_info)

        resonance = QHBoxLayout()
        self.peak = LabeledSlider("Resonance", *CONTROL_RANGE, 7700, step=1,
                                  formatter=lambda v: f"{v:.0f} Hz")
        self.peak.valueChanged.connect(self._move_peak)
        self.peak._slider.sliderReleased.connect(self._commit_peak)
        resonance.addWidget(self.peak, 1)
        self.peak_spin = QDoubleSpinBox()
        self.peak_spin.setRange(*CONTROL_RANGE)
        self.peak_spin.setDecimals(0)
        self.peak_spin.setSuffix(" Hz")
        self.peak_spin.setSingleStep(10)
        self.peak_spin.setValue(7700)
        self.peak_spin.valueChanged.connect(self._move_peak)
        self.peak_spin.editingFinished.connect(self._commit_peak)
        resonance.addWidget(self.peak_spin)
        layout.addLayout(resonance)

        personal_row = QHBoxLayout()
        self.personal_treble_enabled = QCheckBox(
            "Personalize higher-treble correction (optional; off = original EQ)"
        )
        self.personal_treble_enabled.setChecked(bool(
            main._settings.get("resonance_personal_treble_enabled", False)
        ))
        self.personal_treble_enabled.setToolTip(
            "Enables the saved adjustments for all supplied ears. "
            "The frequency slider edits only the ear selected in the tabs."
        )
        self.personal_treble_enabled.toggled.connect(self._set_personal_treble_enabled)
        personal_row.addWidget(self.personal_treble_enabled, 1)
        layout.addLayout(personal_row)
        self.personal_treble_controls = QWidget()
        personal_controls_layout = QHBoxLayout(self.personal_treble_controls)
        personal_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.personal_treble_slider = LabeledSlider(
            "Heard higher peak", *PERSONAL_TREBLE_RANGE, 15500, step=1,
            formatter=lambda v: f"{v:.0f} Hz")
        self.personal_treble_slider.valueChanged.connect(self._move_personal_treble)
        self.personal_treble_slider._slider.sliderReleased.connect(self._commit_personal_treble)
        personal_controls_layout.addWidget(self.personal_treble_slider, 1)
        self.personal_treble_spin = QDoubleSpinBox()
        self.personal_treble_spin.setRange(*PERSONAL_TREBLE_RANGE)
        self.personal_treble_spin.setValue(15500)
        self.personal_treble_spin.setDecimals(0)
        self.personal_treble_spin.setSingleStep(100)
        self.personal_treble_spin.setSuffix(" Hz")
        self.personal_treble_spin.valueChanged.connect(self._move_personal_treble)
        self.personal_treble_spin.setToolTip(
            "Place the personal high-treble correction around the frequency you hear. "
            "This adjusts only the EQ correction; it does not change the measured coupler model."
        )
        self.personal_treble_spin.editingFinished.connect(self._commit_personal_treble)
        personal_controls_layout.addWidget(self.personal_treble_spin)
        self.reset_personal_treble = QPushButton("Reset to predicted")
        self.reset_personal_treble.clicked.connect(self._reset_personal_treble)
        personal_controls_layout.addWidget(self.reset_personal_treble)
        layout.addWidget(self.personal_treble_controls)
        self.personal_treble_info = QLabel()
        self.personal_treble_info.setWordWrap(True)
        layout.addWidget(self.personal_treble_info)
        self.personal_treble_controls.setEnabled(self.personal_treble_enabled.isChecked())
        self.reset_personal_treble.setEnabled(self.personal_treble_enabled.isChecked())
        if self.personal_treble_enabled.isChecked():
            self._sync_personal_treble_controls()

        audio = main.audio_panel
        controls = QHBoxLayout()
        focus = QPushButton("Focus Sweep")
        focus.clicked.connect(lambda: self._play_focus(self.peak.value()))
        controls.addWidget(focus)
        self.focus_width = ThreeWaySwitch(("½ oct", "1 oct", "2 oct"), audio.focus_width.index())
        self.focus_width.selectionChanged.connect(self._set_focus_width)
        audio.focus_width.selectionChanged.connect(self.focus_width.set_index)
        controls.addWidget(self.focus_width)
        self.focus_speed = LabeledSlider("Focus speed", 1, 6, audio.focus_speed.value(),
                                         step=0.5, suffix=" s/oct")
        self.focus_speed.valueChanged.connect(self._set_focus_speed)
        audio.focus_speed.valueChanged.connect(self.focus_speed.set_value)
        controls.addWidget(self.focus_speed, 1)
        self.volume = LabeledSlider("Volume", 0, 1, audio.vol_slider.value(), step=0.005,
                                    formatter=lambda v: f"{20 * math.log10(max(v, 1e-6)):.1f} dB")
        self.volume.valueChanged.connect(self._set_volume)
        audio.vol_slider.valueChanged.connect(self.volume.set_value)
        controls.addWidget(self.volume, 1)
        stop = QPushButton("Stop")
        stop.clicked.connect(audio.stop_all)
        controls.addWidget(stop)
        layout.addLayout(controls)
        self._stop_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._stop_shortcut.activated.connect(audio.stop_all)

        self.spectrum = SpectrumView(main.engine)
        self.spectrum.setTitle("Selected ear: predicted measurement / target / corrected")
        self.spectrum.frequencyClicked.connect(self._play_sine)
        self.spectrum.focusSweepRequested.connect(self._play_focus)
        self.spectrum._band_center_line.setBounds(tuple(math.log10(v) for v in CONTROL_RANGE))
        self.spectrum.bandChanged.connect(self._drag_peak)
        self.spectrum._band_center_line.sigPositionChangeFinished.connect(self._commit_peak)
        self.spectrum.set_band_visible(True)
        self.spectrum._band_fill.hide()
        self.spectrum._overlay_curve.setData([], [])
        legend = self.spectrum._plot.plotItem.legend
        for curve, label in [(self.spectrum._overlay_curve, "Predicted"),
                             (self.spectrum._overlay2_curve, "Target"),
                             (self.spectrum._combined_curve, "Corrected")]:
            legend.removeItem(curve)
            legend.addItem(curve, label)
        for curve in [self.spectrum._live_curve, self.spectrum._eq_curve,
                      self.spectrum._eq2_curve, self.spectrum._combined2_curve,
                      self.spectrum._mic_curve, self.spectrum._eq_preview_curve]:
            legend.removeItem(curve)
            curve.hide()
        self.spectrum._plot.setYRange(-25, 20, padding=0)
        layout.addWidget(self.spectrum, 1)

        actions = QHBoxLayout()
        self.generate = QPushButton("Generate L/R EQ")
        self.generate.setEnabled(False)
        self.generate.clicked.connect(self._generate)
        actions.addWidget(self.generate)
        self.audition = QCheckBox("EQ on (Equalizer APO)")
        self.audition.setEnabled(False)
        self.audition.toggled.connect(self._apply_audition)
        actions.addWidget(self.audition)
        self.save = QPushButton("Save selected EQ…")
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        actions.addWidget(self.save)
        layout.addLayout(actions)
        self.history = QListWidget()
        self.history.setMaximumHeight(85)
        self.history.currentRowChanged.connect(self._select_version)
        layout.addWidget(self.history)
        self.status = QLabel("Use low listening levels. Model details show holdout checks and skipped files.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(300)
        self._commit_timer.timeout.connect(self._generate)
        self._install_measurements(self.measurements)
        self._sync_personal_treble_controls()
        previous = main._settings.get("resonance_measurements_dir")
        if previous:
            QTimer.singleShot(0, lambda: self.load_measurements(previous, quiet=True))

    measurements = property(lambda self: self.session.measurements,
                            lambda self, value: setattr(self.session, "measurements", value))
    peaks = property(lambda self: self.session.primary,
                     lambda self, value: setattr(self.session, "primary", value))
    personal_treble_peaks = property(lambda self: self.session.upper,
                                     lambda self, value: setattr(self.session, "upper", value))
    target = property(lambda self: self.session.target,
                      lambda self, value: setattr(self.session, "target", value))
    target_name = property(lambda self: self.session.target_name,
                           lambda self, value: setattr(self.session, "target_name", value))

    @property
    def side(self):
        return self.ears.tabData(self.ears.currentIndex())

    @property
    def model(self):
        return self.measurements.models[self.side]

    def _route_audio(self, *_):
        mode = ChannelMode.BOTH if self.listen_both.isChecked() else {
            "L": ChannelMode.LEFT, "R": ChannelMode.RIGHT, "M": ChannelMode.BOTH,
        }[self.side]
        self.main.audio_panel.channel_switch.set_index(int(mode))
        self.main.engine.set_channel_mode(mode)

    def _play_sine(self, hz):
        self._route_audio()
        self.main._on_graph_freq_clicked(hz)

    def _play_focus(self, hz):
        self._route_audio()
        self.main._on_focus_sweep_requested(hz)

    def _set_focus_width(self, index):
        audio = self.main.audio_panel
        audio.focus_width.set_index(index)
        audio._on_focus_param_changed()

    def _set_focus_speed(self, value):
        audio = self.main.audio_panel
        audio.focus_speed.set_value(value)
        audio._on_focus_param_changed()

    def _set_volume(self, value):
        audio = self.main.audio_panel
        audio.vol_slider.set_value(value)
        audio._on_volume_changed(value)

    def _pick_measurements(self):
        path = QFileDialog.getExistingDirectory(self, "Folder of L/R measurements",
                                                self.main._settings.get("resonance_measurements_dir", ""))
        if path:
            self.load_measurements(path)

    def load_measurements(self, path, quiet=False):
        if self._loader is not None:
            return
        self._commit_timer.stop()
        self.load_measurements_button.setEnabled(False)
        self.generate.setEnabled(False)
        self.status.setText("Reading measurements and checking prediction methods… Existing EQ is unchanged until ready.")
        self._loader = _MeasurementLoader(path, self)
        self._loader.loaded.connect(self._measurements_loaded)
        self._loader.failed.connect(lambda message: self._load_failed(message, quiet))
        self._loader.finished.connect(self._load_finished)
        self._loader.start()

    def _measurements_loaded(self, measurements):
        try:
            self._restore_preview()
        except (ApoError, OSError) as exc:
            self._load_failed(f"Could not restore the current audition preset: {exc}", False)
            return
        self._selected = None
        self._install_measurements(measurements)
        self.main._settings["resonance_measurements_dir"] = measurements.name
        self.main._schedule_save()
        self.status.setText(f"Learned {sum(m.sample_count for m in measurements.models.values())} measurements. "
                            + ("See Model details for skipped files. " if measurements.warnings else "")
                            + "Identify the peak for each supplied ear.")
        if self._generated and self.target is not None:
            self._generate()

    def _load_failed(self, message, quiet):
        self.status.setText(f"Measurement import failed; previous model retained. {message}")
        if not quiet:
            QMessageBox.warning(self, "Could not learn measurements", message)

    def _load_finished(self):
        self._loader.deleteLater()
        self._loader = None
        self.load_measurements_button.setEnabled(True)
        self.generate.setEnabled(self.target is not None)

    def _install_measurements(self, measurements, peaks=None):
        previous = self.side if self.ears.count() else None
        self.measurements = measurements
        self.peaks = {s: float((peaks or {}).get(s, 7700)) for s in measurements.models}
        self.ears.blockSignals(True)
        while self.ears.count():
            self.ears.removeTab(0)
        for side in measurements.models:
            i = self.ears.addTab("")
            self.ears.setTabData(i, side)
            if side == previous:
                self.ears.setCurrentIndex(i)
        self.ears.blockSignals(False)
        summary = " / ".join(f"{s}: {m.sample_count} fits" for s, m in measurements.models.items())
        warning = f" · {len(measurements.warnings)} import notes" if measurements.warnings else ""
        self.measurements_label.setText(f"{Path(measurements.name).name} · {summary}{warning}")
        self.measurements_label.setToolTip(measurements.name)
        self.generate.setText("Generate L/R EQ" if len(measurements.models) == 2 else "Generate shared EQ")
        self._change_ear(self.ears.currentIndex())

    def _change_ear(self, _index):
        self.main.audio_panel.stop_all()
        self._route_audio()
        self._sync_peak_controls()
        self._sync_personal_treble_controls()
        self._show_prediction()

    def _sync_peak_controls(self):
        hz = self.peaks[self.side]
        self.peak.set_value(hz)
        self.peak_spin.blockSignals(True)
        self.peak_spin.setValue(hz)
        self.peak_spin.blockSignals(False)
        self.spectrum.set_band(hz, 1)
        for i in range(self.ears.count()):
            side = self.ears.tabData(i)
            name = {"L": "Left", "R": "Right", "M": "Single side"}[side]
            suffix = " → both" if len(self.peaks) == 1 else ""
            self.ears.setTabText(i, f"{name}: {self.peaks[side]:.0f} Hz{suffix}")
        model = self.model
        score = model.validation.get("interpolation_treble_rms_db")
        check = f" · treble holdout RMS {score:.2f} dB" if score is not None else " · too few distinct fits for holdout validation"
        self.model_info.setText(f"{self.side}: {model.describe(hz)} · {model.method}{check}")

    def _details(self):
        report = [self.measurements.name, "", "Common measurement level reference; no separate L/R normalization.",
                  "Holdout scores are within-set model checks, NOT real-ear accuracy guarantees."]
        for side, model in self.measurements.models.items():
            report += ["", f"{side}: {model.sample_count} files; {len(model.peaks)} distinct peaks; {model.method}",
                       f"Edge trend strength: {model.edge_trend:g}",
                       "Peaks (Hz): " + ", ".join(f"{p:.1f}" for p in model.peaks),
                       f"Validation: {model.validation}", "Files: " + ", ".join(model.sources)]
        report += ["", "Optional personal high-treble alignment affects EQ correction only; "
                   "the displayed measurement prediction and learned model remain unchanged.",
                   "No resolved higher coupler peak in the available target/measurement bandwidth "
                   "means the personal shift is a no-op.", "", *self.measurements.warnings]
        box = QMessageBox(self)
        box.setWindowTitle("Learned resonance models")
        box.setText("Models are learned separately per side. Missing frequency ranges are extrapolations; "
                    "add measurements there for reliable interpolation.")
        box.setDetailedText("\n".join(report))
        box.exec()

    def _pick_target(self):
        path, _ = QFileDialog.getOpenFileName(self, "Target curve", "",
                                             "Frequency responses (*.txt *.csv);;All files (*.*)")
        if path:
            try:
                self.load_target(path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not load target", str(exc))

    def load_target(self, path):
        freqs, db = load_frequency_response(path)
        self.session.set_target(freqs, db, Path(path).name, path=str(path))
        self.target_label.setText(self.target_name)
        self.generate.setEnabled(self._loader is None)
        self._show_prediction()
        self._commit_peak()

    def _drag_peak(self, hz, _width):
        self._move_peak(hz)
        self._play_sine(self.peak.value())

    def _move_peak(self, hz):
        hz = round(max(CONTROL_RANGE[0], min(CONTROL_RANGE[1], hz)))
        self._commit_timer.stop()
        self.peaks[self.side] = hz
        self._sync_peak_controls()
        self._show_prediction()
        if self._generated:
            self._commit_timer.start()

    def _show_prediction(self):
        if self.target is None:
            self.spectrum.set_overlay(self.model.freqs, self.model.predict(self.peaks[self.side]))
        else:
            eq = match_target(self.model, self.peaks[self.side], *self.target, self.target_name)
            self.spectrum.set_overlay(eq.freqs, eq.predicted_db)
            self.spectrum.set_overlay2(eq.freqs, eq.target_db)
        if self.personal_treble_enabled.isChecked():
            self.spectrum.setTitle(
                "Selected ear: coupler prediction / target / optional personalized treble correction"
            )
        else:
            self.spectrum.setTitle("Selected ear: predicted measurement / target / corrected")
        selected = self._selected.eq.channels.get(self.side) if self._selected else None
        self.personal_treble_info.setText(
            "Optional listening preference, not a real-ear measurement. Off preserves the original EQ."
        )
        if self.personal_treble_enabled.isChecked() and selected is not None:
            if self.side not in self.personal_treble_peaks:
                self.personal_treble_info.setText("This ear uses the original correction. Move the control to personalize it.")
            elif selected.coupler_treble_peak_hz is None:
                self.spectrum.setTitle(
                    "Personal treble fit unavailable: no resolved higher coupler peak; prediction unchanged"
                )
                self.personal_treble_info.setText("No supported higher peak found; this ear's EQ is unchanged.")
            else:
                applied = selected.applied_personal_peak_hz
                limited = " (limited; requested %.0f Hz)" % selected.personal_peak_hz if applied != selected.personal_peak_hz else ""
                low, high = selected.personal_treble_band_hz
                ear = {"L": "Left", "R": "Right", "M": "Shared"}[self.side]
                self.personal_treble_info.setText(
                    f"{ear} correction anchor: {selected.coupler_treble_peak_hz:.0f} -> {applied:.0f} Hz{limited}. "
                    f"Only {low:.0f}-{high:.0f} Hz is remapped; outside that band the EQ is unchanged. "
                    "Coupler prediction unchanged; this is not a real-ear prediction."
                )
        if selected is not None:
            self.spectrum.set_eq_response(selected.freqs,
                                          selected.correction_db if self.audition.isChecked()
                                          else np.zeros_like(selected.freqs))
        else:
            self.spectrum.set_eq_response(self.model.freqs, np.zeros_like(self.model.freqs))

    def _commit_peak(self, *_):
        self._commit_timer.stop()
        if self._generated:
            self._generate()

    def _load_personal_treble_peaks(self):
        values = self.main._settings.get("resonance_personal_treble_peaks_hz", {})
        if not isinstance(values, dict):
            return {}
        result = {}
        for side, value in values.items():
            try:
                hz = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if side in ("L", "R", "M") and np.isfinite(hz) and PERSONAL_TREBLE_RANGE[0] <= hz <= PERSONAL_TREBLE_RANGE[1]:
                result[side] = hz
        return result

    def _sync_personal_treble_controls(self):
        if not hasattr(self, "personal_treble_spin") or not self.peaks:
            return
        hz = self.personal_treble_peaks.get(self.side, 15500.0)
        self.personal_treble_slider.set_value(hz)
        self.personal_treble_spin.blockSignals(True)
        self.personal_treble_spin.setValue(hz)
        self.personal_treble_spin.blockSignals(False)

    def _sync_personal_treble_status(self):
        if not hasattr(self, "status") or not hasattr(self, "personal_treble_enabled"):
            return
        if self.personal_treble_enabled.isChecked():
            self.status.setText(
                "Personal fit changes only the high-treble EQ correction; the coupler prediction is unchanged."
            )
        elif self._selected is not None:
            self.status.setText(
                "Selected EQ uses the standard coupler-based prediction; no personal treble adjustment."
            )
        else:
            self.status.setText(
                "Use low listening levels. Model details show holdout checks and skipped files."
            )

    def _save_personal_treble_preferences(self):
        self.main._settings["resonance_personal_treble_enabled"] = self.personal_treble_enabled.isChecked()
        self.main._settings["resonance_personal_treble_peaks_hz"] = self.personal_treble_peaks.copy()
        self.main._schedule_save()

    def _set_personal_treble_enabled(self, enabled):
        self.personal_treble_controls.setEnabled(enabled)
        self.reset_personal_treble.setEnabled(enabled)
        if enabled and self.peaks:
            self.personal_treble_peaks.setdefault(self.side, self.personal_treble_spin.value())
        self._save_personal_treble_preferences()
        self._sync_personal_treble_status()
        self._show_prediction()
        if self._generated:
            self._generate()

    def _move_personal_treble(self, hz):
        if not self.peaks:
            return
        self.personal_treble_peaks[self.side] = round(float(hz))
        self._sync_personal_treble_controls()
        self._save_personal_treble_preferences()
        if self._generated:
            self._commit_timer.start()

    def _commit_personal_treble(self):
        self._commit_timer.stop()
        if self._generated:
            self._generate()

    def _reset_personal_treble(self):
        if not self.peaks:
            return
        self._commit_timer.stop()
        self.personal_treble_peaks.pop(self.side, None)
        if not any(s in self.personal_treble_peaks for s in self.peaks):
            self.personal_treble_enabled.blockSignals(True)
            self.personal_treble_enabled.setChecked(False)
            self.personal_treble_enabled.blockSignals(False)
            self.personal_treble_controls.setEnabled(False)
        self._sync_personal_treble_controls()
        self._save_personal_treble_preferences()
        self._commit_personal_treble()

    def _generate(self):
        self._commit_timer.stop()
        if self.target is None:
            return
        self.session.upper_enabled = self.personal_treble_enabled.isChecked()
        eq = self.session.channel_eq()
        for i, old in enumerate(self.versions):
            if (old.measurements is self.measurements and old.eq.target_name == self.target_name
                    and old.eq.personal_treble_enabled == eq.personal_treble_enabled
                    and all(old.eq.channels[s].peak_hz == hz for s, hz in self.peaks.items())
                    and old.peaks == self.personal_treble_peaks
                    and all(np.array_equal(a, b) for a, b in zip(old.target, self.target))):
                if self.history.currentRow() == i:
                    self._select_version(i)
                else:
                    self.history.setCurrentRow(i)
                return
        self._generated = True
        self.versions.append(_Version(eq, self.measurements, self.target,
                                      self.personal_treble_peaks.copy()))
        peaks = " / ".join(f"{s} {v.peak_hz:.0f}" for s, v in eq.channels.items())
        suffix = " · personal treble" if eq.personal_treble_enabled else ""
        self.history.addItem(f"{len(self.versions)} · {peaks} Hz{suffix} · {eq.target_name}")
        self.history.setCurrentRow(len(self.versions) - 1)

    def _select_version(self, row):
        if not 0 <= row < len(self.versions):
            return
        version = self.versions[row]
        self._selected = version
        self.target = version.target
        self.target_name = version.eq.target_name
        self.target_label.setText(self.target_name)
        if self.measurements is not version.measurements:
            self._install_measurements(version.measurements, {s: v.peak_hz for s, v in version.eq.channels.items()})
        else:
            self.peaks = {s: v.peak_hz for s, v in version.eq.channels.items()}
            self._sync_peak_controls()
        self.personal_treble_peaks = version.peaks.copy()
        active_treble = getattr(version.eq, "personal_treble_enabled", False)
        self.personal_treble_enabled.blockSignals(True)
        self.personal_treble_enabled.setChecked(active_treble)
        self.personal_treble_enabled.blockSignals(False)
        self.personal_treble_controls.setEnabled(active_treble)
        self.reset_personal_treble.setEnabled(active_treble)
        self._sync_personal_treble_controls()
        self._save_personal_treble_preferences()
        self._commit_timer.stop()
        self.audition.setEnabled(True)
        self.save.setEnabled(True)
        self._apply_audition()

    def _apply_audition(self, *_):
        if self._commit_timer.isActive():
            self._generate()  # Commit visible controls before switching playback.
            # _generate() updates self._selected; retry audition now.
            QTimer.singleShot(0, lambda: self._apply_audition())
            return
        if self._selected is None:
            return
        eq = self._selected.eq
        self._show_prediction()
        if not self.audition.isChecked() and self._preview_preset is None:
            self.status.setText("EQ generated. Enable EQ on to audition through APO; select any version to compare.")
            return
        try:
            if self._library is None or not self._device:
                raise ValueError("Select an APO device in the main window first, then reopen Resonance EQ.")
            if self._preview_preset is None:
                self._previous_preset = self._library.active_preset(self._device)
                self._preview_preset = self._library.create_preset(self._device, "Resonance audition")
            if self._preview_eq is None:
                self._preview_eq = self._library.create_eq_file(self._preview_preset, "Resonance audition")
            atomic_write_with_backup(Path(self._preview_eq.path), eq.apo_text(self.audition.isChecked()))
            self._library.activate(self._preview_preset)
            poke(self._library._config_path)
            kind = "independent L/R EQ" if len(eq.channels) == 2 else "one measured side → same EQ on both"
            personalization = (" · personal treble fit (EQ correction only)"
                               if getattr(eq, "personal_treble_enabled", False)
                               else " · standard coupler-based correction")
            self.status.setText(
                f"APO: {'EQ on' if self.audition.isChecked() else 'bypassed'} · {kind}{personalization} · "
                f"shared preamp {eq.preamp_db:.1f} dB in both states. Previous preset restored on close."
            )
        except (ApoError, OSError, ValueError) as exc:
            self.audition.blockSignals(True)
            self.audition.setChecked(False)
            self.audition.blockSignals(False)
            self._show_prediction()
            try:
                self._restore_preview()
            except (ApoError, OSError) as restore_error:
                exc = f"{exc}\nCould not restore APO: {restore_error}. Close this window to retry."
            QMessageBox.warning(self, "Could not audition EQ", str(exc))

    def _save(self):
        if self._commit_timer.isActive():
            self._generate()  # Never export stale values during the debounce.
            QTimer.singleShot(0, lambda: self._save())
            return
        if self._selected is None:
            return
        eq = self._selected.eq
        peaks = " ".join(f"{s}{v.peak_hz:.0f}" for s, v in eq.channels.items())
        if eq.personal_treble_enabled:
            peaks += " personal-treble"
        path, _ = QFileDialog.getSaveFileName(self, "Save Equalizer APO EQ",
                                             f"Resonance {peaks}.txt", "APO EQ (*.txt)")
        if path:
            try:
                atomic_write_with_backup(Path(path), eq.apo_text())
                self.status.setText(f"Saved {path}")
            except (ApoError, OSError) as exc:
                QMessageBox.warning(self, "Could not save EQ", str(exc))

    def _restore_preview(self):
        if self._preview_preset is None:
            return
        active = self._library.active_preset(self._device)
        if active is not None and active.path == self._preview_preset.path and self._previous_preset:
            self._library.activate(self._previous_preset)
        self._library.remove_preset(self._preview_preset)
        for item in (self._preview_eq, self._preview_preset):
            if item is not None:
                path = Path(item.path)
                path.unlink(missing_ok=True)
                path.with_suffix(path.suffix + ".eqhelper.bak").unlink(missing_ok=True)
        self._preview_preset = None
        self._preview_eq = None
        poke(self._library._config_path)

    def done(self, result):
        if self._loader is not None:
            self.status.setText("Please wait for measurement training to finish before closing.")
            return
        self._commit_timer.stop()
        self.main.audio_panel.stop_all()
        try:
            self._restore_preview()
        except (ApoError, OSError) as exc:
            QMessageBox.warning(self, "Could not restore APO preset", str(exc))
            return
        self.main.audio_panel.channel_switch.set_index(self._previous_channel)
        self.main.engine.set_channel_mode(ChannelMode(self._previous_channel))
        self.main._schedule_save()
        self.spectrum._timer.stop()
        super().done(result)
