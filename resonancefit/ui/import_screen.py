"""Shown when no calibration is available yet: open or drop the file you received."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from eq_helper.calibration.rescal import RESCAL_SUFFIX

from .common import Card, heading, hint, tr


class ImportScreen(QWidget):
    fileChosen = Signal(str)

    def __init__(self, branding, calibrations_folder, translocated: bool = False, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(False)  # the main window takes drops for every screen
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = Card()
        card.setMaximumWidth(620)
        row.addWidget(card, 4)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)

        title = heading(branding.title, 1)
        title.setAlignment(Qt.AlignCenter)
        card.body.addWidget(title)
        self.headline = heading(tr("Open the calibration file you received"), 2)
        self.headline.setAlignment(Qt.AlignCenter)
        card.body.addWidget(self.headline)
        self.explain = hint(tr(
            "Your calibration ends in {suffix}. Drag it onto this window, double-click it, "
            "or choose it below. You only need to do this once."
        ).format(suffix=RESCAL_SUFFIX))
        self.explain.setAlignment(Qt.AlignCenter)
        card.body.addWidget(self.explain)

        self.drop_zone = QLabel(tr("Drop the file here"))
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setAlignment(Qt.AlignCenter)
        self.drop_zone.setMinimumHeight(110)
        self.drop_zone.setStyleSheet("QLabel#dropZone { border: 2px dashed palette(mid); border-radius: 12px; }")
        card.body.addWidget(self.drop_zone)

        self.open_button = QPushButton(tr("Open calibration file…"))
        self.open_button.setObjectName("primary")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._browse)
        card.body.addWidget(self.open_button, 0, Qt.AlignCenter)

        self.note = hint(tr(
            "macOS moved this app to a protected location, so it can't see the files that came "
            "with it. Open the calibration file from your download once and it will be remembered."
        ) if translocated else tr("Or put the file in: {folder}").format(folder=calibrations_folder))
        self.note.setAlignment(Qt.AlignCenter)
        self.note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.note)

        self.error = QLabel()
        self.error.setObjectName("warning")
        self.error.setWordWrap(True)
        self.error.setAlignment(Qt.AlignCenter)
        card.body.addWidget(self.error)

    def show_error(self, text: str) -> None:
        self.error.setText(text)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Open calibration file"), "",
            tr("Calibration files (*{suffix});;All files (*)").format(suffix=RESCAL_SUFFIX))
        if path:
            self.fileChosen.emit(path)
