"""Dialog for manually tuning low-frequency noise smoothing."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eq_helper.dsp.balance_smoothing import smooth_low_frequency_noise


class BalanceSmoothingDialog(QDialog):
    def __init__(self, left_f: np.ndarray, left_db: np.ndarray, right_f: np.ndarray, right_db: np.ndarray, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tune Low-Frequency Smoothing")
        self.resize(800, 600)
        
        self.left_f = np.asarray(left_f, dtype=float)
        self.left_db = np.asarray(left_db, dtype=float)
        self.right_f = np.asarray(right_f, dtype=float)
        self.right_db = np.asarray(right_db, dtype=float)
        
        self.cutoff_hz = 0.0
        self.smooth_oct = 1.0
        
        layout = QVBoxLayout(self)
        
        # Plot
        self.plot_widget = pg.PlotWidget(title="Low-Frequency Smoothing Preview")
        self.plot_widget.setLogMode(x=True, y=False)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel("bottom", "Frequency", units="Hz")
        self.plot_widget.setLabel("left", "Magnitude", units="dB")
        self.plot_widget.setXRange(np.log10(20), np.log10(1000), padding=0)
        
        layout.addWidget(self.plot_widget, 1)
        
        self.plot_widget.plot(self.left_f, self.left_db, pen=pg.mkPen((0, 255, 255, 100), width=1), name="Left Raw")
        self.left_smooth_curve = self.plot_widget.plot(self.left_f, self.left_db, pen=pg.mkPen((0, 255, 255, 255), width=2), name="Left Smoothed")
        
        self.plot_widget.plot(self.right_f, self.right_db, pen=pg.mkPen((255, 165, 0, 100), width=1), name="Right Raw")
        self.right_smooth_curve = self.plot_widget.plot(self.right_f, self.right_db, pen=pg.mkPen((255, 165, 0, 255), width=2), name="Right Smoothed")
        
        # Controls
        ctrl_layout = QVBoxLayout()
        
        # Cutoff frequency
        self.cutoff_label = QLabel("Cutoff Frequency: 0 Hz (Disabled)")
        self.cutoff_slider = QSlider(Qt.Horizontal)
        self.cutoff_slider.setRange(0, 1000)
        self.cutoff_slider.setValue(0)
        self.cutoff_slider.valueChanged.connect(self._update_smoothing)
        
        ctrl_layout.addWidget(self.cutoff_label)
        ctrl_layout.addWidget(self.cutoff_slider)
        
        # Smoothing Amount (octaves)
        self.smooth_label = QLabel("Smoothing: 1/1 octaves")
        self.smooth_slider = QSlider(Qt.Horizontal)
        self.smooth_slider.setRange(1, 12)
        self.smooth_slider.setValue(1)
        self.smooth_slider.valueChanged.connect(self._update_smoothing)
        
        ctrl_layout.addWidget(self.smooth_label)
        ctrl_layout.addWidget(self.smooth_slider)
        
        layout.addLayout(ctrl_layout)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        
        # Initialize default value (let's suggest 300Hz by default)
        self.cutoff_slider.setValue(300)
        self._update_smoothing()
        
    def _update_smoothing(self) -> None:
        val = self.cutoff_slider.value()
        if val == 0:
            self.cutoff_hz = 0.0
            self.cutoff_label.setText("Cutoff Frequency: 0 Hz (Disabled)")
        else:
            self.cutoff_hz = float(val)
            self.cutoff_label.setText(f"Cutoff Frequency: {self.cutoff_hz:.0f} Hz")
            
        oct_val = self.smooth_slider.value()
        self.smooth_oct = 1.0 / oct_val
        self.smooth_label.setText(f"Smoothing: 1/{oct_val} octaves")
        
        l_smoothed = smooth_low_frequency_noise(self.left_f, self.left_db, self.cutoff_hz, smooth_oct=self.smooth_oct)
        r_smoothed = smooth_low_frequency_noise(self.right_f, self.right_db, self.cutoff_hz, smooth_oct=self.smooth_oct)
        
        self.left_smooth_curve.setData(self.left_f, l_smoothed)
        self.right_smooth_curve.setData(self.right_f, r_smoothed)

    def parameters(self) -> tuple[float, float]:
        return self.cutoff_hz, self.smooth_oct
