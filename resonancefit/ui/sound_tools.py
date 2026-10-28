"""Sound tools: sine, focus sweep, full sweep, pink and grey noise, volume and L/R routing."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..audio import amp_to_db
from .common import Card, hint, tr

VOLUME_RANGE_DB = (-60, 0)

# (kind, idle label, playing label)
SOUNDS = (("tone", "▶ Tone", "■ Stop tone"),
          ("focus", "〰 Sweep around it", "■ Stop sweep"),
          ("sweep", "〰 Full sweep 20 Hz–20 kHz", "■ Stop sweep"),
          ("pink", "≈ Pink noise", "■ Stop noise"),
          ("grey", "≈ Grey noise", "■ Stop noise"))


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


class SoundToolsPage(QWidget):
    def __init__(self, audio, parent=None):
        super().__init__(parent)
        self.audio = audio
        layout = QVBoxLayout(self)
        layout.addWidget(hint(tr("Test sounds for checking your EQ by ear. Start quiet.")))

        card = Card(tr("Where the sound plays"))
        routes = QHBoxLayout()
        self.route_group = QButtonGroup(self)
        self.route_buttons = {}
        for key, text in (("L", tr("Left")), ("both", tr("Both")), ("R", tr("Right"))):
            button = QPushButton(text)
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, k=key: self.audio.route(k))
            self.route_group.addButton(button)
            self.route_buttons[key] = button
            routes.addWidget(button)
        routes.addStretch(1)
        card.body.addLayout(routes)
        self.volume = VolumeSlider(audio)
        card.body.addWidget(self.volume)
        layout.addWidget(card)

        tones = Card(tr("Sounds"))
        grid = QGridLayout()
        grid.addWidget(QLabel(tr("Frequency")), 0, 0)
        self.freq_slider = QSlider(Qt.Horizontal)
        self.freq_slider.setRange(0, 1000)  # log 20 Hz–20 kHz
        self.freq_slider.setAccessibleName(tr("Tone frequency"))
        self.freq_slider.valueChanged.connect(self._slider_moved)
        grid.addWidget(self.freq_slider, 0, 1)
        self.freq_spin = QSpinBox()
        self.freq_spin.setRange(20, 20000)
        self.freq_spin.setSuffix(" Hz")
        self.freq_spin.setValue(1000)
        self.freq_spin.valueChanged.connect(self._spin_changed)
        grid.addWidget(self.freq_spin, 0, 2)
        tones.body.addLayout(grid)
        # each button starts its sound and stops it when pressed again (as on the Tune tab)
        buttons = QHBoxLayout()
        self.sound_buttons: dict[str, QPushButton] = {}
        for kind, idle, _playing in SOUNDS:
            button = QPushButton(tr(idle))
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=kind: self._toggle(k))
            buttons.addWidget(button)
            self.sound_buttons[kind] = button
        for kind, *texts in SOUNDS:  # wide enough for either of its labels: no jumping
            button = self.sound_buttons[kind]
            button.setMinimumWidth(max(button.fontMetrics().horizontalAdvance(tr(t)) for t in texts) + 28)
        self.sine_button = self.sound_buttons["tone"]
        self.focus_button = self.sound_buttons["focus"]
        self.sweep_button = self.sound_buttons["sweep"]
        self.pink_button = self.sound_buttons["pink"]
        self.grey_button = self.sound_buttons["grey"]
        self.grey_button.setToolTip(tr("Like pink noise, with the treble and bass lifted the way your hearing "
                                       "needs for every part to sound about equally loud."))
        buttons.addStretch(1)
        tones.body.addLayout(buttons)
        tones.body.addWidget(hint(tr("Press a playing sound's button again to stop it (or press Space).")))
        layout.addWidget(tones)
        layout.addStretch(1)
        self._spin_changed(self.freq_spin.value())
        audio.on_change(self._sync_route)
        audio.on_change(self._sync_buttons)
        self._sync_route()
        self._sync_buttons()

    def _toggle(self, kind: str) -> None:
        if self.audio.playing == kind:
            self.audio.stop_all()
        elif kind == "tone":
            self.audio.play_tone(self.freq_spin.value())
        elif kind == "focus":
            self.audio.play_focus_sweep(self.freq_spin.value())
        elif kind == "sweep":
            self.audio.play_full_sweep()
        else:
            self.audio.play_noise(kind)
        self._sync_buttons()  # the quiet-start warning may have said no

    def _sync_buttons(self) -> None:
        for kind, idle, active in SOUNDS:
            button = self.sound_buttons[kind]
            on = self.audio.playing == kind
            button.setChecked(on)
            button.setText(tr(active) if on else tr(idle))

    def _slider_moved(self, pos: int) -> None:
        hz = int(round(20 * 1000 ** (pos / 1000)))
        self.freq_spin.blockSignals(True)
        self.freq_spin.setValue(hz)
        self.freq_spin.blockSignals(False)
        self._retune(hz)

    def _spin_changed(self, hz: int) -> None:
        self.freq_slider.blockSignals(True)
        self.freq_slider.setValue(int(round(1000 * math.log(hz / 20) / math.log(1000))))
        self.freq_slider.blockSignals(False)
        self._retune(hz)

    def _retune(self, hz: int) -> None:
        if self.audio.playing == "tone":
            self.audio.set_tone(hz)

    def _sync_route(self) -> None:
        key = self.audio.side if self.audio.side in ("L", "R") else "both"
        self.route_buttons[key].setChecked(True)
