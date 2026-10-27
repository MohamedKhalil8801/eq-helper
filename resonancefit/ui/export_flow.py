"""Export: "Where will you listen?" → app cards → files + that app's setup steps.

Every export is recorded in history with the app it was made for. "Other /
Advanced" convolution opens the full options dialog; "Predicted response"
writes squig.link / PEQdB-compatible curves.
"""

from __future__ import annotations

import atexit
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from eq_helper.dsp.predicted_export import write_predicted
from eq_helper.dsp.resonance import ChannelEQ
from eq_helper.exports.catalog import AppExport, export_for_app
from eq_helper.exports.formats.base import ExportMeta, prepend_readme, shared_only
from eq_helper.widgets.convolution_export_dialog import ConvolutionExportDialog

from .app_cards import AccuracyWorker, AppCard, ListeningPicker, balance_text, listening_label
from .common import Card, Worker, heading, hint, tr

PREDICTED_APP_ID = "predicted-response"
_LIVE_THREADS: set = set()  # keep running threads alive past their widgets


def _keep(thread) -> None:
    _LIVE_THREADS.add(thread)
    thread.finished.connect(lambda: _LIVE_THREADS.discard(thread))


@atexit.register
def _join_threads() -> None:
    for thread in list(_LIVE_THREADS):
        thread.requestInterruption()
        thread.wait(10000)


@dataclass
class ExportJob:
    channel_eq: ChannelEQ
    meta: ExportMeta
    base_name: str
    iem_name: str
    record: Callable  # (reason, note, files, app) -> entry
    settings: object = None


class ExportFlow(QDialog):
    def __init__(self, job: ExportJob, catalog, platform: str, installed=frozenset(), device_rate=None,
                 folder_provider: Callable[[], str | None] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Export your EQ"))
        self.resize(820, 760)
        self.job, self.catalog, self.platform = job, catalog, platform
        self.installed = set(installed)
        self.device_rate = device_rate
        self.folder_provider = folder_provider or self._ask_folder
        self.cards: dict[str, AppCard] = {}
        self.page = "choose"
        self._accuracy: AccuracyWorker | None = None

        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        choose = QWidget()
        layout = QVBoxLayout(choose)
        self.picker = ListeningPicker(platform)
        self.picker.chosen.connect(self._show_apps)
        layout.addWidget(self.picker)
        self.apps_heading = heading("", 3)
        layout.addWidget(self.apps_heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.scroll, 1)
        predicted = Card()
        row = QHBoxLayout()
        row.addWidget(hint(tr("For squig.link or PEQdB studio: your IEM's predicted response, "
                              "left and right, as text files.")), 1)
        self.predicted_with_eq = QCheckBox(tr("Also with the EQ applied"))
        row.addWidget(self.predicted_with_eq)
        button = QPushButton(tr("Export predicted response…"))
        button.clicked.connect(self._predicted_clicked)
        row.addWidget(button)
        predicted.body.addLayout(row)
        layout.addWidget(predicted)
        self.status = hint("")
        layout.addWidget(self.status)
        self.stack.addWidget(choose)

        done = QWidget()
        done_layout = QVBoxLayout(done)
        self.done_title = heading(tr("Done"), 2)
        done_layout.addWidget(self.done_title)
        self.done_label = QLabel()
        self.done_label.setWordWrap(True)
        self.done_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        done_layout.addWidget(self.done_label)
        done_layout.addStretch(1)
        buttons = QHBoxLayout()
        self.open_folder = QPushButton(tr("Open folder"))
        self.open_folder.clicked.connect(self._open_folder)
        buttons.addWidget(self.open_folder)
        back = QPushButton(tr("Export for another app"))
        back.clicked.connect(self._back)
        buttons.addWidget(back)
        buttons.addStretch(1)
        close = QPushButton(tr("Close"))
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        done_layout.addLayout(buttons)
        self.stack.addWidget(done)
        self._last_folder: Path | None = None

        self.picker.choose("this_computer")

    # ── choosing ───────────────────────────────────────────────────────────
    def choose(self, where: str, os_code: str | None = None) -> None:
        self.picker.choose(where, os_code)

    def card_ids(self) -> list[str]:
        return list(self.cards)

    def _show_apps(self, where: str, os_code: str) -> None:
        installed = self.installed if where == "this_computer" else set()
        apps = self.catalog.for_listening(where, os_code or None, installed)
        self.apps_heading.setText(tr("Apps for: {where}").format(where=listening_label(where, os_code)))
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 8, 0)
        self.cards = {}
        jobs = []
        for app in apps:
            card = AppCard(app, app.id in installed, self.job.channel_eq, self.device_rate)
            card.exportRequested.connect(self.run_export)
            layout.addWidget(card)
            self.cards[app.id] = card
            for fmt in app.formats:
                if fmt.format == "parametric":
                    eq = card_eq(app, self.job.channel_eq)
                    jobs.append((app.id, eq, fmt.options))
                    break
        layout.addStretch(1)
        self.scroll.setWidget(holder)
        self._start_accuracy(jobs)

    def _start_accuracy(self, jobs) -> None:
        if self._accuracy is not None:
            self._accuracy.requestInterruption()
        if not jobs:
            return
        worker = AccuracyWorker(jobs)
        cards = self.cards

        def show(app_id, text):
            card = cards.get(app_id)
            if card is not None and cards is self.cards:
                try:
                    card.set_accuracy(text)
                except RuntimeError:  # card already deleted
                    pass
        worker.result.connect(show)
        _keep(worker)
        self._accuracy = worker
        worker.start()

    # ── exporting ──────────────────────────────────────────────────────────
    def export_app(self, app, format_index: int, folder, rate: int | None = None) -> AppExport:
        """Write the files for ``app`` into ``folder`` and record it in history."""
        result = self._write(app, format_index, Path(folder), rate)
        self.job.record("export", "", [str(p) for p in result.files], app.id)
        return result

    def _write(self, app, format_index: int, folder: Path, rate: int | None) -> AppExport:
        overrides = {"sample_rate": int(rate)} if rate else None
        return export_for_app(app, self.job.channel_eq, folder, self.job.base_name, self.job.meta,
                              format_index, overrides, self.device_rate)

    def run_export(self, app_id: str, format_index: int) -> None:
        app = self.catalog.get(app_id)
        fmt = app.formats[format_index]
        if app.advanced and fmt.format == "convolution":
            self._advanced_convolution(app)
            return
        folder = self.folder_provider()
        if not folder:
            return
        card = self.cards.get(app_id)
        rate = card.rate_combo.currentData() if card is not None and card.rate_combo is not None else None
        self.status.setText(tr("Exporting for {app}…").format(app=app.name))
        self.setEnabled(False)
        worker = Worker(lambda: self._write(app, format_index, Path(folder), rate))
        worker.finished_ok.connect(lambda result: self._exported(app, Path(folder), result))
        worker.failed.connect(self._failed)
        _keep(worker)
        worker.start()

    def _exported(self, app, folder: Path, result: AppExport) -> None:
        self.setEnabled(True)
        self.status.setText("")
        self.job.record("export", "", [str(p) for p in result.files], app.id)
        self._show_done(app, folder, result.files, result.balance_db)

    def _failed(self, message: str) -> None:
        self.setEnabled(True)
        self.status.setText(tr("Export failed: {reason}").format(reason=message))

    def _advanced_convolution(self, app) -> None:
        settings = self.job.settings
        stored = dict(settings.get("advanced_convolution") or {}) if settings is not None else {}
        started = time.time()
        dialog = ConvolutionExportDialog(self.job.channel_eq, stored, self)
        if not dialog.exec():
            return
        if settings is not None:
            settings.set("advanced_convolution", dialog.options())
        files = [Path(p) for p in dialog.saved_paths]
        if files:
            for readme in files[0].parent.glob("*README.txt"):
                try:
                    if readme.stat().st_mtime >= started - 1:
                        prepend_readme(readme, self.job.meta)
                except OSError:
                    pass
        self.job.record("export", "", [str(p) for p in files], app.id)
        self._show_done(app, files[0].parent if files else None, files, None)

    def export_predicted(self, folder, with_eq: bool = False) -> list[Path]:
        meta = self.job.meta.as_dict()
        files = write_predicted(self.job.channel_eq, folder, self.job.iem_name, meta, with_eq=False)
        if with_eq:
            files += write_predicted(self.job.channel_eq, folder, self.job.iem_name, meta, with_eq=True)
        self.job.record("export", "", [str(p) for p in files], PREDICTED_APP_ID)
        return files

    def _predicted_clicked(self) -> None:
        folder = self.folder_provider()
        if not folder:
            return
        try:
            files = self.export_predicted(folder, self.predicted_with_eq.isChecked())
        except OSError as exc:
            self._failed(str(exc))
            return
        self._show_done(None, Path(folder), files, None)

    # ── done page ──────────────────────────────────────────────────────────
    def _show_done(self, app, folder: Path | None, files, balance_db) -> None:
        self._last_folder = folder
        where, _os = self.picker.current()
        lines = [tr("Saved {n} file(s){where}:").format(
            n=len(files), where=tr(" to {folder}").format(folder=folder) if folder else "")]
        lines += [f"  • {Path(p).name}" for p in files]
        if app is not None:
            if balance_db is not None:
                lines += ["", balance_text(balance_db)]
            if app.setup_steps:
                lines += ["", tr("Next, in {app}:").format(app=app.name)]
                if where == "phone":
                    lines += [tr("First copy the file(s) to your phone — by USB, a cloud drive, or by "
                                 "emailing them to yourself.")]
                lines += [f"{n}. {step}" for n, step in enumerate(app.setup_steps, 1)]
        self.done_title.setText(tr("Exported for {app}").format(app=app.name) if app else tr("Exported"))
        self.done_label.setText("\n".join(lines))
        self.open_folder.setEnabled(folder is not None)
        self.stack.setCurrentIndex(1)
        self.page = "done"

    def done_text(self) -> str:
        return self.done_label.text()

    def _back(self) -> None:
        self.stack.setCurrentIndex(0)
        self.page = "choose"

    def _open_folder(self) -> None:
        if self._last_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_folder)))

    def _ask_folder(self) -> str | None:
        settings = self.job.settings
        start = (settings.get("export_dir") if settings is not None else None) or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, tr("Choose a folder for the files"), start)
        if folder and settings is not None:
            settings.set("export_dir", folder)
        return folder or None

    def done(self, result) -> None:
        if self._accuracy is not None:
            self._accuracy.requestInterruption()
            self._accuracy.wait(3000)
        super().done(result)


def card_eq(app, channel_eq):
    """The EQ an app actually receives (shared-only apps get the L/R average)."""
    if app.lr == "shared":
        return shared_only(channel_eq, app.balance_control).channel_eq
    return channel_eq

