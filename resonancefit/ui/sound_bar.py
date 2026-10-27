"""Always-visible sound controls: output device, volume, stop."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .common import tr
from .sound_tools import VolumeSlider


class OutputPicker(QWidget):
    """Output device choice, remembered by name; every picker stays in sync."""

    def __init__(self, ctx, audio, label: str | None = None, parent=None):
        super().__init__(parent)
        self.ctx, self.audio = ctx, audio
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        text = QLabel(label or tr("Output"))
        row.addWidget(text)
        self.combo = QComboBox()
        self.combo.setAccessibleName(tr("Output device"))
        self.combo.setMinimumContentsLength(18)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.activated.connect(self._chosen)
        text.setBuddy(self.combo)
        row.addWidget(self.combo, 1)
        audio.on_change(self.sync)
        self.refresh()

    def refresh(self) -> None:
        remembered = self.ctx.settings.get("output_device")
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(tr("System default"), None)
        for device_id, name in self.audio.output_devices():
            self.combo.addItem(name, device_id)
        self.combo.blockSignals(False)
        index = self.combo.findText(remembered) if remembered else -1
        if index > 0 and self.audio.device_id != self.combo.itemData(index):
            self.audio.set_output_device(self.combo.itemData(index))
        self.sync()

    def sync(self) -> None:
        index = self.combo.findData(self.audio.device_id) if self.audio.device_id is not None else 0
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(max(index, 0))
        self.combo.blockSignals(False)

    def _chosen(self, index: int) -> None:
        self.audio.stop_all()
        self.audio.set_output_device(self.combo.itemData(index))
        self.ctx.settings.set("output_device", self.combo.itemText(index) if index > 0 else None)


class SoundBar(QFrame):
    def __init__(self, ctx, audio, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        self.output = OutputPicker(ctx, audio)
        row.addWidget(self.output, 2)
        self.volume = VolumeSlider(audio)
        row.addWidget(self.volume, 3)
        self.stop_button = QPushButton(tr("■ Stop all sound (Space)"))
        self.stop_button.clicked.connect(audio.stop_all)
        row.addWidget(self.stop_button)
