"""Settings: output device, Equalizer APO, folders, version."""

from __future__ import annotations

from importlib import metadata

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..apo_apply import ApoUnwritable
from .common import Card, hint, tr
from .sound_bar import OutputPicker


def app_version() -> str:
    try:
        return metadata.version("eq-helper")
    except metadata.PackageNotFoundError:
        return "dev"


class SettingsPage(QWidget):
    walkthroughRequested = Signal()
    apoDeviceChangeRequested = Signal()

    def __init__(self, ctx, audio, apo, parent=None):
        super().__init__(parent)
        self.ctx, self.audio, self.apo = ctx, audio, apo
        layout = QVBoxLayout(self)

        out = Card(tr("Output"))
        self.output = OutputPicker(ctx, audio, tr("Play sound on"))
        self.device_combo = self.output.combo
        out.body.addWidget(self.output)
        out.body.addWidget(hint(tr("Choose the output your IEMs are plugged into. The app remembers it.")))
        layout.addWidget(out)

        apo_card = Card(tr("Equalizer APO"))
        self.apo_status = QLabel()
        self.apo_status.setWordWrap(True)
        apo_card.body.addWidget(self.apo_status)
        row = QHBoxLayout()
        self.apo_change = QPushButton(tr("Change APO output…"))
        self.apo_change.clicked.connect(self.apoDeviceChangeRequested)
        row.addWidget(self.apo_change)
        self.apo_off = QPushButton(tr("Turn the EQ off in Equalizer APO"))
        self.apo_off.clicked.connect(self._apo_off)
        row.addWidget(self.apo_off)
        row.addStretch(1)
        apo_card.body.addLayout(row)
        layout.addWidget(apo_card)

        files = Card(tr("Files"))
        row = QHBoxLayout()
        self.open_calibrations = QPushButton(tr("Open calibrations folder"))
        self.open_calibrations.clicked.connect(self._open_calibrations)
        row.addWidget(self.open_calibrations)
        self.open_data = QPushButton(tr("Open app data folder"))
        self.open_data.clicked.connect(lambda: self._open_folder(self.ctx.paths.data_dir))
        row.addWidget(self.open_data)
        row.addStretch(1)
        files.body.addLayout(row)
        files.body.addWidget(hint(tr("New calibration files put in the calibrations folder are picked up "
                                     "the next time the app starts.")))
        layout.addWidget(files)

        help_card = Card(tr("Help"))
        row = QHBoxLayout()
        walkthrough = QPushButton(tr("Show the walkthrough again"))
        walkthrough.setObjectName("walkthroughButton")
        walkthrough.clicked.connect(self.walkthroughRequested)
        row.addWidget(walkthrough)
        row.addStretch(1)
        help_card.body.addLayout(row)
        self.version_label = hint(tr("{product} version {version}").format(
            product=ctx.branding.product_name, version=app_version()))
        help_card.body.addWidget(self.version_label)
        layout.addWidget(help_card)
        layout.addStretch(1)
        self.refresh_devices()
        self.refresh_apo()

    # ── output device ──────────────────────────────────────────────────────
    def refresh_devices(self) -> None:
        self.output.refresh()

    # ── APO ────────────────────────────────────────────────────────────────
    def refresh_apo(self) -> None:
        available = self.apo.available()
        if available:
            device = self.apo.device or tr("not chosen yet")
            state = tr("on") if self.apo.is_on() else tr("off")
            self.apo_status.setText(tr("Equalizer APO is installed. Output: {device}. Your EQ there is {state}.")
                                    .format(device=device, state=state))
        elif self.ctx.paths.platform == "windows":
            self.apo_status.setText(tr("Equalizer APO isn't available on this computer. Install it to apply your "
                                       "EQ to everything you play, or export for another app."))
        else:
            self.apo_status.setText(tr("Applying directly to Equalizer APO is only on Windows. Use “Export for my "
                                       "EQ app…” instead."))
        self.apo_change.setVisible(available)
        self.apo_off.setVisible(available)
        self.apo_off.setEnabled(available and self.apo.is_on())

    def _apo_off(self) -> None:
        try:
            self.apo.turn_off()
        except ApoUnwritable as problem:
            QMessageBox.warning(self, tr("Equalizer APO couldn't be changed"), tr(problem.fix))
        self.refresh_apo()

    # ── folders ────────────────────────────────────────────────────────────
    def _open_calibrations(self) -> None:
        folder = self.ctx.paths.calibrations_folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._open_folder(folder)

    @staticmethod
    def _open_folder(folder) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
