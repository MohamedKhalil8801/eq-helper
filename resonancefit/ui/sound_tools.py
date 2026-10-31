"""The volume slider (in dB, safe defaults) every volume control shares."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from ..audio import amp_to_db
from .common import tr

VOLUME_RANGE_DB = (-60, 0)


class VolumeSlider(QWidget):
    """Volume in dB (safe defaults); shared look for every volume control."""

    def __init__(self, audio, parent=None):
        super().__init__(parent)
        self.audio = audio
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(tr("Volume"))
        row.addWidget(label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(*VOLUME_RANGE_DB)
        self.slider.setAccessibleName(tr("Volume"))
        self.slider.valueChanged.connect(self._moved)
        label.setBuddy(self.slider)
        row.addWidget(self.slider, 1)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(56)
        row.addWidget(self.value_label)
        audio.on_change(self.sync)
        self.sync()

    def setValue(self, db: int) -> None:  # noqa: N802 - Qt-style API
        self.slider.setValue(int(db))

    def value(self) -> int:
        return self.slider.value()

    def _moved(self, db: int) -> None:
        amp = 0.0 if db <= VOLUME_RANGE_DB[0] else 10 ** (db / 20)
        if abs(amp - self.audio.volume) > 1e-9:
            self.audio.set_volume(amp)
        self.value_label.setText(f"{db} dB")

    def sync(self) -> None:
        db = int(round(max(VOLUME_RANGE_DB[0], min(VOLUME_RANGE_DB[1], amp_to_db(self.audio.volume)))))
        self.slider.blockSignals(True)
        self.slider.setValue(db)
        self.slider.blockSignals(False)
        self.value_label.setText(f"{db} dB")
