"""The customer app's main window: import screen or header + tabs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QMainWindow, QMessageBox, QScrollArea, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from eq_helper.calibration.model_cache import load_measurements
from eq_helper.calibration.rescal import RESCAL_SUFFIX, Calibration, RescalError
from eq_helper.dsp.predicted_export import safe_file_part
from eq_helper.dsp.resonance import Measurements
from eq_helper.exports.catalog import load_catalog
from eq_helper.exports.detection import APO_APP_ID, detect_installed
from eq_helper.exports.formats.base import ExportMeta

from ..apo_apply import ApoApplier, ApoUnwritable
from ..audio import AudioService
from ..history import HistoryStore
from ..library import LibraryEntry
from ..paths import is_translocated
from .about_page import AboutPage
from .app_catalog_page import AppCatalogPage
from .common import Worker, build_style, side_name, tr
from .export_flow import PREDICTED_APP_ID, ExportFlow, ExportJob
from .header_card import HeaderCard
from .history_panel import HistoryPanel, friendly_date
from .import_screen import ImportScreen
from .listen_save import ListenSaveCard
from .settings_page import SettingsPage
from .sound_tools import SoundToolsPage
from .sound_bar import SoundBar
from .test_songs import TestSongsPage
from .walkthrough import Walkthrough


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

    def __init__(self, ctx, audio: AudioService | None = None, start_audio: bool = True,
                 apo: ApoApplier | None = None):
        super().__init__()
        self.ctx = ctx
        self.audio = audio or AudioService()
        self.current: LoadedCalibration | None = None
        self.history: HistoryStore | None = None
        self.loading = False
        self._load_token = 0
        self._workers: set[Worker] = set()
        self.catalog = load_catalog()
        self.installed: set[str] = set()
        self.apo = apo if apo is not None else ApoApplier(ctx.paths.platform, ctx.settings)
        self.setWindowTitle(ctx.branding.title)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_style(self.palette()))
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
        self.sound_bar = SoundBar(ctx, self.audio)
        layout.addWidget(self.sound_bar)
        self.stack.addWidget(self.main_view)
        self._build_tabs()
        self.audio.gate = self._quiet_gate

        self.stop_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.stop_shortcut.setContext(Qt.WindowShortcut)
        self.stop_shortcut.activated.connect(self.audio.stop_all)

        self.audioReady.connect(self._on_audio_ready)
        if start_audio:
            self.audio.start(self.audioReady.emit)
        for path, reason in ctx.scan_report.problems:
            self.statusBar().showMessage(tr("Couldn't read {name}: {reason}").format(name=path.name, reason=reason),
                                         15000)
        self._run(lambda: detect_installed(self.catalog, ctx.paths.platform), self._apps_detected)
        self.refresh()

    def _run(self, fn, on_done) -> Worker:
        worker = Worker(fn, self)
        worker.finished_ok.connect(on_done)
        worker.finished.connect(lambda: self._workers.discard(worker))
        self._workers.add(worker)
        worker.start()
        return worker

    # ── tabs ───────────────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        from .tuning_panel import TuningPanel
        self.tune_tab = QWidget()
        tune = QHBoxLayout(self.tune_tab)
        tune.setContentsMargins(0, 8, 0, 0)
        self.tuning = TuningPanel(self.ctx, self.audio)
        self.tuning.eqChanged.connect(self.audio.set_eq)
        self.calibrationLoaded.connect(self.tuning.set_calibration)
        tune.addWidget(self.tuning, 1)

        side = QVBoxLayout()
        self.listen = ListenSaveCard()
        self.listen.eqToggled.connect(self.audio.set_eq_enabled)
        self.listen.saveRequested.connect(lambda note: self.record_history("manual", note))
        side.addWidget(self.listen)
        self.history_panel = HistoryPanel()
        self.history_panel.reapplyRequested.connect(self.reapply)
        side.addWidget(self.history_panel, 1)
        sidebar = QWidget()
        sidebar.setLayout(side)
        side.setContentsMargins(0, 0, 0, 0)
        sidebar.setFixedWidth(330)
        tune.addWidget(sidebar)
        self.calibrationLoaded.connect(self._calibration_ready)
        self.listen.exportRequested.connect(self.open_export)
        self.listen.applyRequested.connect(self.apply_to_apo)
        self.listen.switchRequested.connect(self.switch_apo_tuning)
        self.listen.set_apply_available(self.apo.available())
        self.history_panel.changed.connect(self._sync_saved_tunings)
        self.history_panel.app_names = {app.id: app.name for app in self.catalog.apps}
        self.history_panel.app_names[PREDICTED_APP_ID] = tr("Predicted response")
        tune_scroll = QScrollArea()
        tune_scroll.setWidgetResizable(True)
        tune_scroll.setFrameShape(QScrollArea.NoFrame)
        tune_scroll.setWidget(self.tune_tab)
        self.tune_tab.setMinimumHeight(700)  # below this, scroll instead of squeezing the graph
        self.tabs.addTab(tune_scroll, tr("Tune"))

        self.songs_page = TestSongsPage(self.ctx, self.audio)
        self.calibrationLoaded.connect(self.songs_page.set_calibration)
        self.tabs.addTab(self.songs_page, tr("Test songs"))
        self.sound_tools = SoundToolsPage(self.audio)
        self.tabs.addTab(self.sound_tools, tr("Sound tools"))

        self.catalog_page = AppCatalogPage(self.catalog, self.ctx.paths.platform)
        self.catalog_page.exportRequested.connect(self.open_export)
        self.tabs.addTab(self.catalog_page, tr("Where can I use my EQ?"))

        self.settings_page = SettingsPage(self.ctx, self.audio, self.apo)
        self.settings_page.apoDeviceChangeRequested.connect(self.change_apo_device)
        self.settings_page.walkthroughRequested.connect(self.show_walkthrough)
        self.tabs.addTab(self.settings_page, tr("Settings"))
        self.about_page = AboutPage(self.ctx)
        self.tabs.addTab(self.about_page, tr("About"))
        self.audio.on_change(self._sync_eq_toggle)

    # ── first launch and listening safety ──────────────────────────────────
    def show_walkthrough(self) -> None:
        Walkthrough(self.ctx.branding.product_name, self).exec()
        self.ctx.settings.set("walkthrough_seen", True)

    def confirm_quiet_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Start quiet"))
        box.setText(tr("Turn your volume down before the first sound."))
        box.setInformativeText(tr("Test tones can be piercing at the resonance. Start low and raise the "
                                  "volume slowly; press Space at any time to stop all sound."))
        play = box.addButton(tr("Volume is down, play"), QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is play

    def _quiet_gate(self) -> bool:
        if self.ctx.settings.get("quiet_warning_seen"):
            return True
        if not self.confirm_quiet_start():
            return False
        self.ctx.settings.set("quiet_warning_seen", True)
        return True

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.setStyleSheet(build_style(self.palette()))
        super().changeEvent(event)

    def _apps_detected(self, installed) -> None:
        self.installed = set(installed)
        self.catalog_page.set_installed(self.installed)

    # ── Equalizer APO ──────────────────────────────────────────────────────
    def _sync_saved_tunings(self) -> None:
        entries = self.history.entries() if self.history is not None else []
        items = []
        for e in entries:
            sides = " ".join(f"{s} {hz:.0f}" for s, hz in sorted(e.tuning.primary.items()))
            label = f"{friendly_date(e.date)}: {e.target_name}, {sides}"
            items.append((e.id, label + (f" ({e.note})" if e.note else "")))
        self.listen.set_saved_tunings(items)

    def choose_apo_device(self, devices: list[str]) -> str | None:
        device, ok = QInputDialog.getItem(
            self, tr("Choose your output"),
            tr("Which output do you listen to your IEMs on? You only choose this once."), devices, 0, False)
        return device if ok and device else None

    def show_apo_problem(self, problem: ApoUnwritable) -> None:
        QMessageBox.warning(self, tr("Equalizer APO couldn't be changed"), tr(problem.fix))

    def apply_to_apo(self) -> bool:
        job = self.export_job()
        if job is None or not self.apo.available():
            return False
        if not self.apo.device or self.apo.device not in self.apo.devices():
            devices = self.apo.devices()
            if not devices:
                QMessageBox.information(self, tr("Equalizer APO"), tr(self.apo.no_devices_help()))
                return False
            device = self.choose_apo_device(devices)
            if not device:
                return False
            self.apo.set_device(device)
        try:
            path = self.apo.apply(job.channel_eq, job.iem_name, job.meta.tuning)
        except ApoUnwritable as problem:
            self.show_apo_problem(problem)
            return False
        self.record_history("apply", files=[str(path)], app=APO_APP_ID)
        self.settings_page.refresh_apo()
        self.listen.show_status(tr("Applied to Equalizer APO on {device}. Everything you play there now "
                                   "uses this tuning.").format(device=self.apo.device))
        return True

    def change_apo_device(self) -> None:
        devices = self.apo.devices() if self.apo.available() else []
        if not devices:
            QMessageBox.information(self, tr("Equalizer APO"), tr(self.apo.no_devices_help()))
            return
        device = self.choose_apo_device(devices)
        if device:
            self.apo.set_device(device)
        self.settings_page.refresh_apo()

    def _sync_eq_toggle(self) -> None:
        on = self.audio.eq_enabled
        if self.listen.eq_toggle.isChecked() != on:
            self.listen.eq_toggle.blockSignals(True)
            self.listen.eq_toggle.setChecked(on)
            self.listen.eq_toggle.blockSignals(False)
            self.listen._toggled_text(on)

    def switch_apo_tuning(self, entry_id: str) -> None:
        self.reapply(entry_id)
        self.apply_to_apo()

    # ── export ─────────────────────────────────────────────────────────────
    def export_job(self) -> ExportJob | None:
        session, eq = self.tuning.session, self.tuning.channel_eq
        if self.current is None or session is None or eq is None:
            return None
        manifest = self.current.manifest
        sides = ", ".join(f"{side_name(s)} {hz:.0f} Hz" for s, hz in sorted(session.primary.items()))
        tuning = tr("{sides}; target {target}").format(sides=sides, target=session.target_name)
        if session.upper_enabled and session.upper:
            tuning += "; " + tr("upper treble {values}").format(
                values=", ".join(f"{side_name(s)} {hz:.0f} Hz" for s, hz in sorted(session.upper.items())))
        meta = ExportMeta(manifest.customer.name, manifest.iem.display_name, date.today().isoformat(), tuning)
        peaks = " ".join(f"{s}{hz:.0f}" for s, hz in sorted(session.primary.items()))
        base = safe_file_part(f"{manifest.iem.display_name} - {session.target_name} - {peaks}")
        return ExportJob(eq, meta, base, manifest.iem.display_name, self.record_history, self.ctx.settings)

    def open_export(self) -> None:
        job = self.export_job()
        if job is None:
            self.statusBar().showMessage(tr("Choose a target first, then export."), 8000)
            return
        flow = ExportFlow(job, self.catalog, self.ctx.paths.platform, self.installed,
                          self.audio.device_rate, parent=self)
        flow.exec()
        flow.deleteLater()  # destroyed on the GUI thread, never by a GC pass elsewhere

    # ── history ────────────────────────────────────────────────────────────
    def _calibration_ready(self, loaded) -> None:
        self.history = HistoryStore(self.ctx.paths.history_dir, loaded.calibration_id)
        self.history_panel.set_store(self.history)

    def record_history(self, reason: str, note: str = "", files=(), app=None):
        """Save the current tuning to history (export / apply / manual)."""
        session = self.tuning.session
        if session is None or session.target is None or self.history is None:
            return None
        entry = self.history.save(session.snapshot(), session.target, reason, note, files, app)
        self.history_panel.refresh()
        if reason == "manual":
            self.listen.show_status(tr("Saved. You can come back to it from History."))
        return entry

    def reapply(self, entry_id: str) -> None:
        tuning, target = self.history.reapply(entry_id)
        try:
            self.tuning.apply_tuning(tuning, target)
        except ValueError as exc:
            self.statusBar().showMessage(tr("Couldn't reapply this tuning: {reason}").format(reason=exc), 15000)
            return
        self.statusBar().showMessage(tr("Tuning reapplied."), 5000)

    def _on_audio_ready(self, ok: bool) -> None:
        if ok:
            self.settings_page.refresh_devices()
            self.sound_bar.output.refresh()
        else:
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
        if not self.ctx.settings.get("walkthrough_seen"):
            QTimer.singleShot(0, self.show_walkthrough)

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
