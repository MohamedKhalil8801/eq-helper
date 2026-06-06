"""Microphone panel: monitoring, recording, and playback controls."""

from __future__ import annotations

import os
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eq_helper.audio.engine import AudioEngine


def _default_recordings_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".eq_helper" / "recordings"


class MicPanel(QGroupBox):
    """Microphone controls: monitor toggle, record, play back, save."""

    itemsChanged = Signal()

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__("Microphone", parent)
        self._engine = engine
        self._recorded_buf: Optional[np.ndarray] = None
        self._is_recording = False
        self._is_playing = False
        self._rec_start_time: float = 0.0

        layout = QVBoxLayout(self)

        # Monitor toggle
        self.monitor_check = QCheckBox("Monitor (hear mic)")
        self.monitor_check.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(self.monitor_check)

        # Record / Stop / Play row
        rec_row = QHBoxLayout()
        self.btn_record = QPushButton("Record")
        self.btn_stop = QPushButton("Stop")
        self.btn_play = QPushButton("Play")
        self.btn_stop.setEnabled(False)
        self.btn_play.setEnabled(False)
        self.btn_record.clicked.connect(self._on_record)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_play.clicked.connect(self._on_play)
        rec_row.addWidget(self.btn_record)
        rec_row.addWidget(self.btn_stop)
        rec_row.addWidget(self.btn_play)
        layout.addLayout(rec_row)

        # Save / Clear row
        save_row = QHBoxLayout()
        self.btn_save = QPushButton("Save As\u2026")
        self.btn_clear = QPushButton("Clear")
        self.btn_save.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_clear.clicked.connect(self._on_clear)
        save_row.addWidget(self.btn_save)
        save_row.addWidget(self.btn_clear)
        layout.addLayout(save_row)

        # Status label
        self._status = QLabel("Ready")
        layout.addWidget(self._status)

        # Timer for updating the recording elapsed time display.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(200)
        self._status_timer.timeout.connect(self._update_status)

    def cleanup(self) -> None:
        """Stop timers so pytest-qt teardown doesn't crash."""
        self._status_timer.stop()

    # ------------------------------------------------------------ settings

    def apply_settings(self, settings: dict) -> None:
        self.monitor_check.setChecked(bool(settings.get("mic_monitoring", False)))

    def collect_settings(self, settings: dict) -> None:
        settings["mic_monitoring"] = self.monitor_check.isChecked()

    # -------------------------------------------------------------- slots

    def _on_monitor_toggled(self, checked: bool) -> None:
        self._engine.set_mic_monitoring(checked)

    def _on_record(self) -> None:
        if self._is_playing:
            self._stop_playback()
        self._engine.start_recording()
        self._is_recording = True
        self._rec_start_time = self._now()
        self.btn_record.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_play.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self._status.setText("Recording\u2026")
        self._status_timer.start()

    def _on_stop(self) -> None:
        if self._is_recording:
            buf = self._engine.stop_recording()
            self._is_recording = False
            self._status_timer.stop()
            if buf is not None and buf.shape[0] > 0:
                self._recorded_buf = buf
                dur = buf.shape[0] / self._engine.sample_rate
                self._status.setText(f"Recorded {dur:.1f}s")
                self.btn_play.setEnabled(True)
                self.btn_save.setEnabled(True)
                self.btn_clear.setEnabled(True)
            else:
                self._status.setText("Recording empty")
            self.btn_record.setEnabled(True)
            self.btn_stop.setEnabled(False)
        elif self._is_playing:
            self._stop_playback()

    def _on_play(self) -> None:
        if self._recorded_buf is None:
            return
        self._engine.start_playback(self._recorded_buf, loop=True)
        self._is_playing = True
        self.btn_play.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_record.setEnabled(False)
        self._status.setText("Playing (loop)")

    def _stop_playback(self) -> None:
        self._engine.stop_playback()
        self._is_playing = False
        self.btn_play.setEnabled(self._recorded_buf is not None)
        self.btn_stop.setEnabled(False)
        self.btn_record.setEnabled(True)
        dur = (
            self._recorded_buf.shape[0] / self._engine.sample_rate
            if self._recorded_buf is not None
            else 0
        )
        self._status.setText(f"Recorded {dur:.1f}s")

    def _on_save(self) -> None:
        if self._recorded_buf is None:
            return
        rec_dir = _default_recordings_dir()
        rec_dir.mkdir(parents=True, exist_ok=True)
        default_name = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save recording",
            str(rec_dir / default_name),
            "WAV files (*.wav)",
        )
        if not path:
            return
        self._write_wav(path, self._recorded_buf, self._engine.sample_rate)
        self._status.setText(f"Saved: {Path(path).name}")
        self.itemsChanged.emit()

    def _on_clear(self) -> None:
        if self._is_playing:
            self._stop_playback()
        self._recorded_buf = None
        self.btn_play.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self._status.setText("Ready")
        self.itemsChanged.emit()

    # -------------------------------------------------------------- util

    def _update_status(self) -> None:
        if self._is_recording:
            elapsed = self._now() - self._rec_start_time
            self._status.setText(f"Recording\u2026 {elapsed:.1f}s")

    @staticmethod
    def _now() -> float:
        import time
        return time.monotonic()

    @staticmethod
    def _write_wav(path: str, buf: np.ndarray, sample_rate: int) -> None:
        """Write a mono float32 buffer to a 16-bit PCM WAV file."""
        pcm = np.clip(buf, -1.0, 1.0)
        pcm16 = (pcm * 32767).astype(np.int16)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16.tobytes())
