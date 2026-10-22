"""Dialog for live balance matching and sweep capture reference selection."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import tempfile
import uuid

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eq_helper.audio.engine import AudioEngine
from eq_helper.dsp.balance_score import alignment_score_from_measurements
from eq_helper.dsp.fft import compute_spectrum
from eq_helper.dsp.overlay import (
    OverlayLoadError,
    load_measurement_csv,
    save_measurement_csv,
)
from eq_helper.dsp.sweep_measurement import measurement_from_sweep_recording
from eq_helper.dsp.balance_smoothing import smooth_low_frequency_noise
from eq_helper.ui.spectrum_view import SpectrumView

_MIC_WINDOW = 2048
_UPDATE_INTERVAL_MS = 33  # ~30 FPS for live scoring
_CAPTURE_POLL_MS = 100
_SMOOTHING_OPTIONS = [
    ("None", None),
    ("1/12 octave", 1.0 / 12.0),
    ("1/6 octave", 1.0 / 6.0),
]


class LiveBalanceDialog(QDialog):
    """Live-match and sweep-capture workflow for balanced headphone measurements."""

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Live balance match")
        self._engine = engine
        self._ref_path: Optional[str] = None
        self._ref_freqs = np.array([], dtype=np.float64)
        self._ref_db = np.array([], dtype=np.float64)
        self._best_score = 0.0
        self._best_freqs = np.array([], dtype=np.float64)
        self._best_db = np.array([], dtype=np.float64)
        self._best_path: Optional[str] = None
        self._run_id = uuid.uuid4().hex
        self._temp_dir = Path(tempfile.gettempdir()) / "eq_helper_live_balance"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        self._stage = "capture"
        self._measurements: List[dict] = []
        self._selected_index = -1
        self._capture_timer: Optional[QTimer] = None
        self._smoothing_oct: Optional[float] = None
        self._match_best_score: float = 0.0

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Capture one side with a full-range sweep, choose the best measurement as the reference, "
            "then insert the opposite side and capture a matching sweep."
        ))

        self._stage_label = QLabel("First pass: capture reference-side measurements.")
        layout.addWidget(self._stage_label)

        action_row = QHBoxLayout()
        self._capture_button = QPushButton("Capture sweep")
        self._capture_button.clicked.connect(self._start_capture)
        action_row.addWidget(self._capture_button)

        self._promote_button = QPushButton("Use selected as reference")
        self._promote_button.clicked.connect(self._promote_selected_measurement)
        self._promote_button.setEnabled(False)
        action_row.addWidget(self._promote_button)

        self._load_button = QPushButton("Load reference…")
        self._load_button.clicked.connect(self._pick_reference)
        action_row.addWidget(self._load_button)

        self._clear_button = QPushButton("Clear reference")
        self._clear_button.clicked.connect(self._clear_reference)
        self._clear_button.setEnabled(False)
        action_row.addWidget(self._clear_button)
        layout.addLayout(action_row)

        controls_row = QHBoxLayout()
        self._capture_list = QListWidget()
        self._capture_list.currentRowChanged.connect(self._on_measurement_selected)
        controls_row.addWidget(self._capture_list, 1)

        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("Display smoothing"))
        self._smoothing_combo = QComboBox()
        for label, value in _SMOOTHING_OPTIONS:
            self._smoothing_combo.addItem(label, userData=value)
        self._smoothing_combo.currentIndexChanged.connect(self._update_smoothing)
        sidebar.addWidget(self._smoothing_combo)

        self._export_button = QPushButton("Export selected measurement…")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export_selected_measurement)
        sidebar.addWidget(self._export_button)

        sidebar.addStretch(1)
        controls_row.addLayout(sidebar)
        layout.addLayout(controls_row)

        self._status_label = QLabel(
            "Ready to capture the first side. Insert the headphone or earbud and keep the mic in place."
        )
        self._status_label.setStyleSheet("color: #666;")
        layout.addWidget(self._status_label)

        self._score_label = QLabel("Score: —")
        self._score_label.setStyleSheet("font-size: 1.1em; font-weight: bold;")
        layout.addWidget(self._score_label)

        self._best_score_label = QLabel("Best score: —")
        self._best_score_label.setStyleSheet("color: #555;")
        layout.addWidget(self._best_score_label)

        self._best_file_label = QLabel("")
        self._best_file_label.setStyleSheet("color: #666;")
        layout.addWidget(self._best_file_label)

        self._match_score_label = QLabel("Match best score: —")
        self._match_score_label.setStyleSheet("font-size: 1.1em; font-weight: bold;")
        layout.addWidget(self._match_score_label)

        self._spectrum = SpectrumView(self._engine)
        layout.addWidget(self._spectrum, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(_UPDATE_INTERVAL_MS)
        self._timer.timeout.connect(self._update_match_score)
        self._timer.start()

    def _pick_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Reference measurement",
            "",
            "Measurements (*.txt *.csv);;All files (*.*)",
        )
        if path:
            try:
                self.load_reference_measurement(path)
            except OverlayLoadError as exc:
                QMessageBox.warning(self, "Could not load", str(exc))

    def load_reference_measurement(self, path: str) -> None:
        freqs, db = load_measurement_csv(path)
        self._ref_path = path
        self._ref_freqs = freqs
        self._ref_db = db
        self._best_score = 0.0
        self._best_freqs = np.array([], dtype=np.float64)
        self._best_db = np.array([], dtype=np.float64)
        self._best_path = None
        self._match_best_score = 0.0
        self._export_button.setEnabled(False)
        self._promote_button.setEnabled(False)
        self._clear_button.setEnabled(True)
        self._stage = "match"
        self._stage_label.setText("Second pass: capture the opposite side against the selected reference.")
        self._status_label.setText(
            "Reference loaded. Capture the other side with a sweep, then compare the match score."
        )
        self._match_score_label.setText("Match best score: —")
        self._spectrum.set_overlay(freqs, self._display_db(freqs, db))
        self._spectrum.clear_overlay2()
        self._capture_button.setText("Capture opposing-side sweep")
        if not self._engine.state.snapshot().mic_monitoring:
            self._engine.set_mic_monitoring(True)
        self._update_match_score()

    def _clear_reference(self) -> None:
        self._ref_path = None
        self._ref_freqs = np.array([], dtype=np.float64)
        self._ref_db = np.array([], dtype=np.float64)
        self._best_score = 0.0
        self._best_freqs = np.array([], dtype=np.float64)
        self._best_db = np.array([], dtype=np.float64)
        self._best_path = None
        self._match_best_score = 0.0
        self._stage = "capture"
        self._stage_label.setText("First pass: capture reference-side measurements.")
        self._capture_button.setText("Capture sweep")
        self._status_label.setText(
            "Ready to capture the first side. Insert the headphone or earbud and keep the mic in place."
        )
        self._match_score_label.setText("Match best score: —")
        self._export_button.setEnabled(False)
        self._promote_button.setEnabled(self._selected_index >= 0)
        self._clear_button.setEnabled(False)
        self._spectrum.clear_overlay()
        self._spectrum.clear_overlay2()

    def _update_smoothing(self) -> None:
        self._smoothing_oct = self._smoothing_combo.currentData()
        self._update_spectrum_preview()

    def _start_capture(self) -> None:
        if self._capture_timer is not None:
            return

        snapshot = self._engine.state.snapshot()
        self._engine.set_mic_monitoring(True)
        self._engine.start_recording()
        self._engine.start_sweep(loop=False)

        self._capture_button.setEnabled(False)
        self._promote_button.setEnabled(False)
        self._load_button.setEnabled(False)
        self._clear_button.setEnabled(False)
        self._export_button.setEnabled(False)
        self._status_label.setText("Recording sweep... keep the mic in place until the sweep completes.")

        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(_CAPTURE_POLL_MS)
        self._capture_timer.timeout.connect(self._check_capture_complete)
        self._capture_timer.start()

    def _check_capture_complete(self) -> None:
        sweep = self._engine._sweep
        if sweep is None or not getattr(sweep, "finished", False):
            return

        self._capture_timer.stop()
        self._capture_timer = None

        recorded = self._engine.stop_recording()
        self._engine.stop()
        self._engine.set_mic_monitoring(True)

        if recorded is None or recorded.size == 0:
            self._status_label.setText("Capture failed, no mic data was recorded. Try again.")
            self._capture_button.setEnabled(True)
            self._load_button.setEnabled(True)
            self._clear_button.setEnabled(self._ref_freqs.size > 0)
            return

        self._finalize_capture(recorded, snapshot=self._engine.state.snapshot())

    def _finalize_capture(self, recorded: np.ndarray, snapshot) -> None:
        try:
            freqs, db = measurement_from_sweep_recording(
                recorded,
                self._engine.sample_rate,
                snapshot.sweep_f_start,
                snapshot.sweep_f_end,
                snapshot.sweep_duration_s,
            )
        except Exception as exc:
            self._status_label.setText(f"Sweep analysis failed: {exc}")
            self._capture_button.setEnabled(True)
            self._load_button.setEnabled(True)
            self._clear_button.setEnabled(self._ref_freqs.size > 0)
            return

        path = str(self._temp_dir / f"capture_{len(self._measurements) + 1}_{self._run_id}.txt")
        save_measurement_csv(path, freqs, db)

        side = "reference_candidate" if self._stage == "capture" else "match_candidate"
        label_parts = []
        if side == "reference_candidate":
            label_parts.append(f"Candidate {sum(1 for item in self._measurements if item['side'] == 'reference_candidate') + 1}")
        else:
            label_parts.append(f"Match {sum(1 for item in self._measurements if item['side'] == 'match_candidate') + 1}")

        score = None
        if side == "match_candidate" and self._ref_freqs.size > 0:
            score = alignment_score_from_measurements(
                self._ref_freqs,
                self._ref_db,
                freqs,
                db,
            )
            label_parts.append(f"score {score:.2f}")
            self._match_best_score = max(self._match_best_score, score)
            self._match_score_label.setText(f"Match best score: {self._match_best_score:.2f}")
            self._status_label.setText(
                f"Captured match measurement. Score {score:.2f}. "
                "Capture again to improve or export the best match."
            )
        else:
            self._status_label.setText(
                "Sweep captured. Select the best measurement and use it as the reference."
                if self._stage == "capture"
                else "Sweep captured. Select a measurement to preview or export."
            )

        item = {
            "freqs": freqs,
            "db": db,
            "path": path,
            "side": side,
            "score": score,
        }
        self._measurements.append(item)
        self._capture_list.addItem(" — ".join(label_parts))
        self._capture_list.setCurrentRow(len(self._measurements) - 1)

        self._capture_button.setEnabled(True)
        self._load_button.setEnabled(True)
        self._clear_button.setEnabled(self._ref_freqs.size > 0)
        self._export_button.setEnabled(True)
        self._promote_button.setEnabled(self._stage == "capture")

    def _on_measurement_selected(self, row: int) -> None:
        self._selected_index = row
        self._export_button.setEnabled(row >= 0)
        self._promote_button.setEnabled(row >= 0 and self._stage == "capture")
        self._update_spectrum_preview()

    def _promote_selected_measurement(self) -> None:
        if self._selected_index < 0 or self._selected_index >= len(self._measurements):
            return
        measurement = self._measurements[self._selected_index]
        self._ref_freqs = measurement["freqs"]
        self._ref_db = measurement["db"]
        self._ref_path = measurement.get("path")
        self._stage = "match"
        self._stage_label.setText("Second pass: capture the opposite side against the selected reference.")
        self._status_label.setText(
            "Reference selected. Capture the other side with a sweep, then compare the match score."
        )
        self._clear_button.setEnabled(True)
        self._promote_button.setEnabled(False)
        self._match_best_score = 0.0
        self._match_score_label.setText("Match best score: —")
        self._spectrum.set_overlay(self._ref_freqs, self._display_db(self._ref_freqs, self._ref_db))
        self._spectrum.clear_overlay2()
        self._capture_button.setText("Capture opposing-side sweep")

    def _display_db(self, freqs: np.ndarray, db: np.ndarray) -> np.ndarray:
        if self._smoothing_oct is None:
            return db
        return smooth_low_frequency_noise(freqs, db, cutoff_hz=20000.0, smooth_oct=self._smoothing_oct)

    def _update_spectrum_preview(self) -> None:
        self._spectrum.clear_overlay()
        self._spectrum.clear_overlay2()

        if self._ref_freqs.size > 0:
            self._spectrum.set_overlay(self._ref_freqs, self._display_db(self._ref_freqs, self._ref_db))

        if self._selected_index >= 0 and self._selected_index < len(self._measurements):
            measurement = self._measurements[self._selected_index]
            freqs = measurement["freqs"]
            db = self._display_db(freqs, measurement["db"])
            if self._ref_freqs.size > 0 and measurement["side"] == "match_candidate":
                self._spectrum.set_overlay2(freqs, db)
            else:
                self._spectrum.set_overlay(freqs, db)

    def _update_match_score(self) -> None:
        if self._ref_freqs.size == 0:
            self._score_label.setText("Score: —")
            return

        mic_samples = self._engine.mic_ring.read_latest(_MIC_WINDOW)
        if mic_samples is None or mic_samples.size < 4:
            self._score_label.setText("Score: —")
            return

        mic_freqs, mic_db = compute_spectrum(mic_samples, self._engine.sample_rate)
        if mic_freqs.size == 0:
            self._score_label.setText("Score: —")
            return

        score = alignment_score_from_measurements(
            self._ref_freqs,
            self._ref_db,
            mic_freqs,
            mic_db,
        )
        self._score_label.setText(f"Score: {score:.2f}")

        if score > self._best_score:
            self._best_score = score
            self._best_freqs = mic_freqs
            self._best_db = mic_db
            self._best_path = str(
                self._temp_dir / f"live_balance_best_{self._run_id}.txt"
            )
            save_measurement_csv(self._best_path, self._best_freqs, self._best_db)
            self._export_button.setEnabled(True)
            self._best_score_label.setText(f"Best score: {self._best_score:.2f}")
            self._best_file_label.setText(f"Saved best measurement to {Path(self._best_path).name}")
        else:
            self._best_score_label.setText(
                f"Best score: {self._best_score:.2f}" if self._best_score > 0 else "Best score: —"
            )

        if score >= 0.95:
            self._status_label.setText("Excellent match — ready to capture.")
        elif score >= 0.75:
            self._status_label.setText("Good match — keep adjusting to improve.")
        elif score > 0.0:
            self._status_label.setText("Adjust insertion until the live curve aligns more closely.")
        else:
            self._status_label.setText("No strong feature alignment detected yet.")

    def _export_selected_measurement(self) -> None:
        if self._selected_index < 0 or self._selected_index >= len(self._measurements):
            return
        measurement = self._measurements[self._selected_index]
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export measurement",
            "measurement.txt",
            "REW text files (*.txt);;CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        try:
            save_measurement_csv(path, measurement["freqs"], measurement["db"])
            QMessageBox.information(
                self,
                "Exported",
                f"Measurement exported to {Path(path).name}",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def closeEvent(self, event) -> None:
        if self._capture_timer is not None:
            self._capture_timer.stop()
        self._timer.stop()
        super().closeEvent(event)
