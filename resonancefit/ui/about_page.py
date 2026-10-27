"""About: who made it, your calibration sheet, licences."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .common import Card, heading, hint, tr

_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
LICENSE_PATH = _ROOT / "LICENSE"

THIRD_PARTY = """Built with:
• Qt for Python (PySide6) — LGPL v3
• NumPy, SciPy — BSD licence
• pyqtgraph — MIT licence
• sounddevice + PortAudio — MIT licence
• soundfile + libsndfile — BSD / LGPL v2.1
"""


class AboutPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        branding = ctx.branding
        layout = QVBoxLayout(self)
        card = Card()
        self.title = heading(branding.title, 1)
        card.body.addWidget(self.title)
        if branding.contact_url:
            contact = QLabel(f'<a href="{branding.contact_url}">{branding.contact_url}</a>')
            contact.setTextFormat(Qt.RichText)
            contact.setOpenExternalLinks(True)
            card.body.addWidget(contact)
        card.body.addWidget(hint(tr("Your IEMs were measured and calibrated individually. This app turns that "
                                    "calibration into an EQ for your ears and your favourite target.")))
        layout.addWidget(card)

        sheet = Card(tr("Calibration sheet"))
        self.sheet_hint = hint("")
        sheet.body.addWidget(self.sheet_hint)
        row = QHBoxLayout()
        self.sheet_combo = QComboBox()
        self.sheet_combo.setAccessibleName(tr("Calibration sheet"))
        row.addWidget(self.sheet_combo, 1)
        self.open_sheet = QPushButton(tr("Open"))
        self.open_sheet.clicked.connect(self._open_sheet)
        row.addWidget(self.open_sheet)
        sheet.body.addLayout(row)
        layout.addWidget(sheet)

        lic = Card(tr("Licences"))
        self.licences = QTextBrowser()
        self.licences.setOpenExternalLinks(True)
        try:
            licence = LICENSE_PATH.read_text(encoding="utf-8")
        except OSError:
            licence = "Apache License, Version 2.0 — https://www.apache.org/licenses/LICENSE-2.0"
        self.licences.setPlainText(THIRD_PARTY + "\n" + licence)
        lic.body.addWidget(self.licences, 1)
        layout.addWidget(lic, 1)
        self.refresh_sheets()

    def _sheet_folders(self) -> list[Path]:
        paths = self.ctx.paths
        folders = [paths.calibrations_folder, paths.data_dir / "Sheets"]
        if paths.app_dir is not None:
            folders.insert(0, paths.app_dir)
        return folders

    def refresh_sheets(self) -> None:
        self.sheet_combo.clear()
        seen = set()
        for folder in self._sheet_folders():
            if not folder.is_dir():
                continue
            for pdf in sorted(folder.glob("*.pdf")):
                if pdf.resolve() not in seen:
                    seen.add(pdf.resolve())
                    self.sheet_combo.addItem(pdf.name, str(pdf))
        found = self.sheet_combo.count() > 0
        self.sheet_hint.setText(tr("The one-page summary of your measurement.") if found else tr(
            "Your calibration sheet came with your download as a PDF. Put it next to the app or in the "
            "calibrations folder to open it from here."))
        self.sheet_combo.setVisible(found)
        self.open_sheet.setVisible(found)

    def _open_sheet(self) -> None:
        path = self.sheet_combo.currentData()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
