"""Settings: output device, system-wide EQ (Equalizer APO / EasyEffects), folders, version."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..apo_apply import ApoUnwritable
from ..easyeffects_apply import EasyEffectsProblem
from ..version import app_version
from .common import Card, hint, tr
from .sound_bar import OutputPicker


class SettingsPage(QWidget):
    walkthroughRequested = Signal()
    updateCheckRequested = Signal()
    apoChanged = Signal()
    openUrlRequested = Signal(str)
    apoDeviceChangeRequested = Signal()

    def __init__(self, ctx, audio, apo, updates=None, parent=None):
        super().__init__(parent)
        self.updates = updates
        self.ctx, self.audio, self.apo = ctx, audio, apo
        layout = QVBoxLayout(self)

        out = Card(tr("Output"))
        self.output = OutputPicker(ctx, audio, tr("Play sound on"))
        self.device_combo = self.output.combo
        out.body.addWidget(self.output)
        out.body.addWidget(hint(tr("Choose the output your IEMs are plugged into. The app remembers it.")))
        layout.addWidget(out)

        apo_card = Card(tr(apo.name))
        self.apo_status = QLabel()
        self.apo_status.setWordWrap(True)
        apo_card.body.addWidget(self.apo_status)
        row = QHBoxLayout()
        self.apo_change = QPushButton(tr("Change APO output…"))
        self.apo_change.clicked.connect(self.apoDeviceChangeRequested)
        row.addWidget(self.apo_change)
        self.apo_off = QPushButton(tr("Turn the EQ off in {app}").format(app=apo.name))
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
        layout.addWidget(help_card)

        upd = Card(tr("Version and updates"))
        version = updates.current_version if updates is not None else app_version()
        self.version_label = QLabel(tr("{product} version {version}").format(
            product=ctx.branding.product_name, version=version))
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        upd.body.addWidget(self.version_label)
        self.update_status = QLabel()
        self.update_status.setWordWrap(True)
        upd.body.addWidget(self.update_status)
        self.update_last_checked = hint("")
        upd.body.addWidget(self.update_last_checked)
        row = QHBoxLayout()
        self.check_updates_button = QPushButton(tr("Check for updates now"))
        self.check_updates_button.clicked.connect(self.updateCheckRequested)
        row.addWidget(self.check_updates_button)
        self.update_download = QPushButton(tr("Download the new version"))
        self.update_download.setObjectName("primary")
        self.update_download.clicked.connect(lambda: self.openUrlRequested.emit(self._download_url))
        row.addWidget(self.update_download)
        self.update_details = QPushButton(tr("What's new"))
        self.update_details.clicked.connect(lambda: self.openUrlRequested.emit(self._page_url))
        row.addWidget(self.update_details)
        row.addStretch(1)
        upd.body.addLayout(row)
        self.auto_update_check = QCheckBox(tr("Check once a day when the app opens"))
        self.auto_update_check.setToolTip(tr("Asks GitHub for the latest release; nothing about you or your "
                                             "calibration is sent. Nothing is installed automatically."))
        self.auto_update_check.setChecked(updates.automatic if updates is not None else False)
        self.auto_update_check.toggled.connect(lambda on: self.ctx.settings.set("update_check_automatic", bool(on)))
        upd.body.addWidget(self.auto_update_check)
        for widget in (self.update_status, self.update_last_checked, self.check_updates_button,
                       self.auto_update_check):
            widget.setVisible(updates is not None)
        self._download_url = self._page_url = ""
        layout.addWidget(upd)
        self.show_update_result(updates.last_result() if updates is not None else None)
        layout.addStretch(1)
        self.refresh_devices()
        self.refresh_apo()

    # ── updates ────────────────────────────────────────────────────────────
    def set_checking(self) -> None:
        self.check_updates_button.setEnabled(False)
        self.update_status.setText(tr("Checking for updates…"))

    def show_update_result(self, result) -> None:
        self.check_updates_button.setEnabled(True)
        if result is None:
            self.update_status.setText(tr("Not checked yet."))
        elif not result.ok:
            self.update_status.setText(tr("Couldn't check for updates ({reason}). It will try again the next "
                                          "time the app opens.").format(reason=result.error))
        elif result.available:
            self.update_status.setText(tr("Version {latest} is available. You have {current}.").format(
                latest=result.latest, current=self.updates.current_version))
        else:
            self.update_status.setText(tr("You have the latest version."))
        available = result is not None and result.ok and result.available
        self._download_url = (result.download_url or result.page_url) if available else ""
        self._page_url = result.page_url if available else ""
        self.update_download.setVisible(available)
        self.update_details.setVisible(available and bool(result.page_url))
        last = self.updates.last_success if self.updates is not None else None
        self.update_last_checked.setText(tr("Last successful check: {when}").format(
            when=last.strftime("%d %b %Y, %H:%M") if last else tr("Never")))

    # ── output device ──────────────────────────────────────────────────────
    def refresh_devices(self) -> None:
        self.output.refresh()

    # ── APO ────────────────────────────────────────────────────────────────
    def refresh_apo(self) -> None:
        available = self.apo.available()
        name = self.apo.name
        if available:
            state = tr("on") if self.apo.is_on() else tr("off")
            if self.apo.uses_devices:
                device = self.apo.device or tr("not chosen yet")
                self.apo_status.setText(tr("{app} is installed. Output: {device}. Your EQ there is {state}.")
                                        .format(app=name, device=device, state=state))
            else:
                self.apo_status.setText(tr("{app} is installed. Your EQ there is {state}. Applying replaces "
                                           "the effects running in {app} with your EQ; pick your own preset "
                                           "in {app} to go back.").format(app=name, state=state))
        else:
            self.apo_status.setText(tr(self.apo.unavailable_text()))
        self.apo_change.setVisible(available and self.apo.uses_devices)
        self.apo_off.setVisible(available)
        self.apo_off.setEnabled(available and self.apo.is_on())

    def _apo_off(self) -> None:
        try:
            self.apo.turn_off()
        except (ApoUnwritable, EasyEffectsProblem) as problem:
            QMessageBox.warning(self, tr("{app} couldn't be changed").format(app=self.apo.name), tr(problem.fix))
        self.refresh_apo()
        self.apoChanged.emit()

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
