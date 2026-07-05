"""Dialog for live balance matching of a reference measurement and current mic input."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import tempfile
import uuid

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
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
from eq_helper.ui.spectrum_view import SpectrumView

_MIC_WINDOW = 2048
_UPDATE_INTERVAL_MS = 33  # ~30 FPS for live scoring


class LiveBalanceDialog(QDialog):
    """Live-match the current microphone input against a static reference."""

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

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Load a REW or CSV reference measurement and play pink "
                                "noise. The live mic spectrum updates at ~30 FPS and "
                                "the score uses the same alignment logic as balance generation."))

        file_row = QHBoxLayout()
        self._ref_label = QLabel("Reference: (none)")
        file_row.addWidget(self._ref_label, 1)
        self._browse_button = QPushButton("Load reference…")
        self._browse_button.clicked.connect(self._pick_reference)
        file_row.addWidget(self._browse_button)
        self._clear_button = QPushButton("Clear")
        self._clear_button.clicked.connect(self._clear_reference)
        self._clear_button.setEnabled(False)
        file_row.addWidget(self._clear_button)
        layout.addLayout(file_row)

        self._score_label = QLabel("Score: —")
        self._score_label.setStyleSheet("font-size: 1.2em; font-weight: bold;")
        layout.addWidget(self._score_label)

        self._best_score_label = QLabel("Best score: —")
        self._best_score_label.setStyleSheet("color: #555;")
        layout.addWidget(self._best_score_label)

        self._status_label = QLabel("Load a reference measurement and play pink noise in REW.")
        self._status_label.setStyleSheet("color: #666;")
        layout.addWidget(self._status_label)

        self._best_file_label = QLabel("")
        self._best_file_label.setStyleSheet("color: #666;")
        layout.addWidget(self._best_file_label)

        self._export_button = QPushButton("Export best measurement…")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export_best_measurement)
        layout.addWidget(self._export_button)

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
        self._export_button.setEnabled(False)
        self._best_score_label.setText("Best score: —")
        self._best_file_label.setText("")
        self._ref_label.setText(f"Reference: {Path(path).name}")
        self._clear_button.setEnabled(True)
        self._spectrum.set_overlay(freqs, db)
        self._update_match_score()

    def _clear_reference(self) -> None:
        self._ref_path = None
        self._ref_freqs = np.array([], dtype=np.float64)
        self._ref_db = np.array([], dtype=np.float64)
        self._best_score = 0.0
        self._best_freqs = np.array([], dtype=np.float64)
        self._best_db = np.array([], dtype=np.float64)
        self._best_path = None
        self._ref_label.setText("Reference: (none)")
        self._clear_button.setEnabled(False)
        self._export_button.setEnabled(False)
        self._spectrum.clear_overlay()
        self._score_label.setText("Score: —")
        self._best_score_label.setText("Best score: —")
        self._best_file_label.setText("")
        self._status_label.setText("Load a reference measurement and play pink noise in REW.")

    def _update_match_score(self) -> None:
        if self._ref_freqs.size == 0:
            self._score_label.setText("Score: —")
            self._status_label.setText("Load a reference measurement and play pink noise in REW.")
            return

        mic_samples = self._engine.mic_ring.read_latest(_MIC_WINDOW)
        if mic_samples is None or mic_samples.size < 4:
            self._score_label.setText("Score: —")
            self._status_label.setText("Waiting for mic input…")
            return

        mic_freqs, mic_db = compute_spectrum(mic_samples, self._engine.sample_rate)
        if mic_freqs.size == 0:
            self._score_label.setText("Score: —")
            self._status_label.setText("Waiting for mic input…")
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
                self._temp_dir / f"live_balance_best_{self._run_id}.csv"
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

    def _export_best_measurement(self) -> None:
        if self._best_path is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export best measurement",
            "best_measurement.csv",
            "CSV files (*.csv *.txt);;All files (*.*)",
        )
        if not path:
            return
        try:
            save_measurement_csv(path, self._best_freqs, self._best_db)
            QMessageBox.information(
                self,
                "Exported",
                f"Best measurement exported to {Path(path).name}",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
