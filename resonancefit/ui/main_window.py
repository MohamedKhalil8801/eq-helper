"""The customer app's main window: import screen or header + tabs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from eq_helper.calibration.model_cache import load_measurements
from eq_helper.calibration.rescal import RESCAL_SUFFIX, Calibration, RescalError
from eq_helper.dsp.resonance import Measurements

from ..audio import AudioService
from ..library import LibraryEntry
from ..paths import is_translocated
from .common import APP_STYLE, Worker, tr
from .header_card import HeaderCard
from .import_screen import ImportScreen


@dataclass(frozen=True)
class LoadedCalibration:
    entry: LibraryEntry
    calibration: Calibration
    measurements: Measurements

    @property
    def manifest(self):
        return self.calibration.manifest

    @property
    def calibration_id(self) -> str:
        return self.entry.calibration_id


def _rescal_paths(mime) -> list[Path]:
    if not mime.hasUrls():
        return []
    return [Path(u.toLocalFile()) for u in mime.urls()
            if u.isLocalFile() and u.toLocalFile().lower().endswith(RESCAL_SUFFIX)]


class MainWindow(QMainWindow):
    calibrationLoaded = Signal(object)  # LoadedCalibration
    audioReady = Signal(bool)

    def __init__(self, ctx, audio: AudioService | None = None, start_audio: bool = True):
        super().__init__()
        self.ctx = ctx
        self.audio = audio or AudioService()
        self.current: LoadedCalibration | None = None
        self.loading = False
        self._load_token = 0
        self._workers: set[Worker] = set()
        self.setWindowTitle(ctx.branding.title)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)
        self.resize(1180, 860)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.import_screen = ImportScreen(ctx.branding, ctx.paths.calibrations_folder,
                                          is_translocated(ctx.paths.app_dir))
        self.import_screen.fileChosen.connect(self.open_calibration_file)
        self.stack.addWidget(self.import_screen)

        self.main_view = QWidget()
        layout = QVBoxLayout(self.main_view)
        layout.setContentsMargins(12, 12, 12, 12)
        self.header = HeaderCard(ctx.branding)
        self.header.calibrationChosen.connect(self.select_calibration)
        layout.addWidget(self.header)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        self.stack.addWidget(self.main_view)
        self._build_tabs()

        self.stop_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.stop_shortcut.setContext(Qt.WindowShortcut)
        self.stop_shortcut.activated.connect(self.audio.stop_all)

        self.audioReady.connect(self._on_audio_ready)
        if start_audio:
            self.audio.start(self.audioReady.emit)
        for path, reason in ctx.scan_report.problems:
            self.statusBar().showMessage(tr("Couldn't read {name}: {reason}").format(name=path.name, reason=reason),
                                         15000)
        self.refresh()

    # ── tabs (filled by later screens) ─────────────────────────────────────
    def _build_tabs(self) -> None:
        from .tuning_panel import TuningPanel
        self.tune_tab = QWidget()
        tune = QHBoxLayout(self.tune_tab)
        tune.setContentsMargins(0, 8, 0, 0)
        self.tuning = TuningPanel(self.ctx, self.audio)
        self.tuning.eqChanged.connect(self.audio.set_eq)
        self.calibrationLoaded.connect(self.tuning.set_calibration)
        tune.addWidget(self.tuning, 1)
        self.tabs.addTab(self.tune_tab, tr("Tune"))

    def _on_audio_ready(self, ok: bool) -> None:
        if not ok:
            self.statusBar().showMessage(
                tr("Sound is not available: {reason}").format(reason=self.audio.error or tr("no output device")))

    # ── which screen ───────────────────────────────────────────────────────
    @property
    def showing_import_screen(self) -> bool:
        return self.stack.currentWidget() is self.import_screen

    def refresh(self) -> None:
        entries = self.ctx.library.list()
        if not entries:
            self.current = None
            self.stack.setCurrentWidget(self.import_screen)
            return
        selected = self.ctx.library.selected
        self.header.set_entries(entries, selected.calibration_id)
        self.stack.setCurrentWidget(self.main_view)
        self._load(selected)

    # ── opening and switching ──────────────────────────────────────────────
    def open_calibration_file(self, path) -> bool:
        path = Path(path)
        try:
            entry = self.ctx.library.import_file(path)
        except (RescalError, OSError) as exc:
            message = tr("{name} isn't a calibration this app can use: {reason}").format(name=path.name, reason=exc)
            self.import_screen.show_error(message)
            self.statusBar().showMessage(message, 15000)
            return False
        self.import_screen.show_error("")
        self.ctx.library.select(entry.calibration_id)
        self.refresh()
        return True

    def select_calibration(self, calibration_id: str) -> None:
        self.ctx.library.select(calibration_id)
        self.refresh()

    def _load(self, entry: LibraryEntry) -> None:
        if self.current is not None and self.current.calibration_id == entry.calibration_id \
                and self.current.entry.path == entry.path and not self.loading:
            self.header.set_manifest(entry.manifest)
            return
        self._load_token += 1
        token = self._load_token
        self.loading = True
        self.header.set_manifest(entry.manifest)
        self.statusBar().showMessage(tr("Preparing your calibration…"))
        library, cache = self.ctx.library, self.ctx.paths.model_cache_dir

        def work():
            calibration = library.load(entry.calibration_id)
            return LoadedCalibration(entry, calibration, load_measurements(calibration, cache))

        worker = Worker(work, self)
        worker.finished_ok.connect(lambda result: self._loaded(token, result))
        worker.failed.connect(lambda message: self._load_failed(token, entry, message))
        worker.finished.connect(lambda: self._workers.discard(worker))
        self._workers.add(worker)
        worker.start()

    def _loaded(self, token: int, loaded: LoadedCalibration) -> None:
        if token != self._load_token:
            return
        self.current = loaded
        self.loading = False
        self.statusBar().clearMessage()
        self.calibrationLoaded.emit(loaded)

    def _load_failed(self, token: int, entry: LibraryEntry, message: str) -> None:
        if token != self._load_token:
            return
        self.loading = False
        self.statusBar().showMessage(tr("Couldn't open this calibration: {reason}").format(reason=message))

    # ── drag and drop ──────────────────────────────────────────────────────
    def dragEnterEvent(self, event):
        if _rescal_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _rescal_paths(event.mimeData())
        for path in paths:
            self.open_calibration_file(path)
        if paths:
            event.acceptProposedAction()

    # ── shutdown ───────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self.audio.stop_all()
        for worker in list(self._workers):
            worker.wait(5000)
        super().closeEvent(event)
