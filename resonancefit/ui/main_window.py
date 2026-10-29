"""The customer app's main window: import screen or header + tabs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QPushButton, QMessageBox, QScrollArea, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from eq_helper.calibration.model_cache import load_measurements
from eq_helper.calibration.rescal import RESCAL_SUFFIX, Calibration, RescalError
from eq_helper.dsp.predicted_export import safe_file_part
from eq_helper.dsp.resonance import Measurements
from eq_helper.exports.catalog import load_catalog
from eq_helper.exports.detection import APO_APP_ID, detect_installed
from eq_helper.exports.formats.base import ExportMeta

from ..apo_apply import ApoApplier, ApoUnwritable
from ..easyeffects_apply import EasyEffectsApplier, EasyEffectsProblem

APPLY_PROBLEMS = (ApoUnwritable, EasyEffectsProblem)


def system_eq_for(platform: str, settings):
    """The system-wide EQ the app applies to directly: Equalizer APO (Windows), EasyEffects (Linux)."""
    if platform == "linux":
        return EasyEffectsApplier(platform, settings)
    return ApoApplier(platform, settings)
from ..audio import AudioService
from ..history import HistoryStore
from ..library import LibraryEntry
from ..os_integration import FileAssociation
from ..paths import is_translocated
from .about_page import AboutPage
from .app_catalog_page import AppCatalogPage
from .common import Worker, build_style, side_name, tr
from .export_flow import PREDICTED_APP_ID, ExportFlow, ExportJob
from .header_card import HeaderCard
from .history_panel import HistoryPanel, describe_entry, entry_name
from .import_screen import ImportScreen
from .listen_save import ListenSaveCard
from .settings_page import SettingsPage
from .sound_tools import SoundToolsPage
from .sound_bar import SoundBar
from .test_songs import TestSongsPage
from .walkthrough import Walkthrough

ICON = Path(__file__).resolve().parents[1] / "assets" / "icon.png"


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


class UpdateBanner(QFrame):
    """A slim strip above the tabs: "Version X is available" with Download / What's new / dismiss."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBanner")
        self.setStyleSheet("#updateBanner { border: 1px solid palette(highlight); border-radius: 6px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        self.text = QLabel()
        self.text.setWordWrap(True)
        row.addWidget(self.text, 1)
        self.download = QPushButton(tr("Download"))
        self.download.setObjectName("primary")
        row.addWidget(self.download)
        self.details = QPushButton(tr("What's new"))
        row.addWidget(self.details)
        self.dismiss = QPushButton(tr("Not now"))
        self.dismiss.setToolTip(tr("Hide this until a newer version comes out. Settings still shows it."))
        row.addWidget(self.dismiss)
        self.result = None
        self.hide()


class MainWindow(QMainWindow):
    calibrationLoaded = Signal(object)  # LoadedCalibration
    audioReady = Signal(bool)

    def __init__(self, ctx, audio: AudioService | None = None, start_audio: bool = True,
                 apo: ApoApplier | None = None, association: FileAssociation | None = None, updates=None):
        super().__init__()
        self.ctx = ctx
        self.updates = updates
        self._update_worker: Worker | None = None
        self.audio = audio or AudioService()
        self.current: LoadedCalibration | None = None
        self.history: HistoryStore | None = None
        self.loading = False
        self._load_token = 0
        self._workers: set[Worker] = set()
        self.catalog = load_catalog()
        self.installed: set[str] = set()
        # "apo": the system-wide EQ applied to directly (Equalizer APO or EasyEffects)
        self.apo = apo if apo is not None else system_eq_for(ctx.paths.platform, ctx.settings)
        self.association = association if association is not None else FileAssociation.detect(
            ctx.paths.platform, ctx.branding, ICON)
        self.setWindowTitle(ctx.branding.title)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_style(self.palette()))
        self.resize(1180, 860)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.update_banner = UpdateBanner()
        banner_box = QHBoxLayout()
        banner_box.setContentsMargins(12, 8, 12, 0)
        banner_box.addWidget(self.update_banner)
        outer.addLayout(banner_box)
        self.update_banner.download.clicked.connect(
            lambda: self.open_url(self.update_banner.result.download_url or self.update_banner.result.page_url))
        self.update_banner.details.clicked.connect(lambda: self.open_url(self.update_banner.result.page_url))
        self.update_banner.dismiss.clicked.connect(self._dismiss_update)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(central)
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
        self._launch_update_check()

    def _run(self, fn, on_done) -> Worker:
        worker = Worker(fn, self)
        worker.finished_ok.connect(on_done)
        worker.finished.connect(lambda: self._workers.discard(worker))
        self._workers.add(worker)
        worker.start()
        return worker

    def _guided_focus(self, active: bool) -> None:
        """The guided test has the window to itself: only it and the sound bar (output, volume, Stop
        all sound) stay; the header, tabs, sidebar (EQ switch, saving, history) and update banner hide."""
        if active:
            self._banner_shown = self.update_banner.isVisibleTo(self)
        for widget in (self.header, self.tabs.tabBar(), self.sidebar):
            widget.setVisible(not active)
        self.update_banner.setVisible(not active and getattr(self, "_banner_shown", False))

    # ── updates (notify only) ──────────────────────────────────────────────
    def open_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _launch_update_check(self) -> None:
        """At launch only: check unless today's check already succeeded (a failed one is retried)."""
        if self.updates is None:
            return
        if self.updates.automatic and self.updates.due():
            self.check_for_updates(manual=False)
        else:
            self._show_update(self.updates.last_result(), manual=False)

    def check_for_updates(self, manual: bool = True) -> None:
        if self.updates is None or self._update_worker is not None:
            return
        self.settings_page.set_checking()
        self._update_worker = self._run(self.updates.check, lambda result: self._update_checked(result, manual))

    def _update_checked(self, result, manual: bool) -> None:
        self._update_worker = None
        self._show_update(result, manual)

    def _show_update(self, result, manual: bool) -> None:
        self.settings_page.show_update_result(result)
        if result is None or not (result.ok and result.available):
            self.update_banner.hide()
            return
        if not manual and self.updates.dismissed(result.latest):
            return
        self.update_banner.result = result
        self.update_banner.text.setText(tr("{product} {latest} is available (you have {current}). Your "
                                           "calibrations, history and settings are kept when you update.").format(
            product=self.ctx.branding.product_name, latest=result.latest, current=self.updates.current_version))
        self.update_banner.details.setVisible(bool(result.page_url))
        self.update_banner.show()

    def _dismiss_update(self) -> None:
        if self.update_banner.result is not None:
            self.updates.dismiss(self.update_banner.result.latest)
        self.update_banner.hide()

    # ── tabs ───────────────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        from .tuning_panel import TuningPanel
        self.tune_tab = QWidget()
        tune = QHBoxLayout(self.tune_tab)
        tune.setContentsMargins(0, 8, 0, 0)
        self.tuning = TuningPanel(self.ctx, self.audio)
        self.tuning.eqChanged.connect(self.audio.set_eq)
        self.tuning.apoOffRequested.connect(self.turn_off_apo)
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
        self.sidebar = QWidget()
        self.sidebar.setLayout(side)
        side.setContentsMargins(0, 0, 0, 0)
        self.sidebar.setFixedWidth(330)
        tune.addWidget(self.sidebar)
        self.tuning.guidedActiveChanged.connect(self._guided_focus)
        self.calibrationLoaded.connect(self._calibration_ready)
        self.listen.exportRequested.connect(self.open_export)
        self.listen.applyRequested.connect(self.apply_to_apo)
        self.listen.switchRequested.connect(self.switch_apo_tuning)
        self.listen.set_apply_target(self.apo.name)
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
        self.tabs.addTab(self.songs_page, tr("Test audio"))
        self.sound_tools = SoundToolsPage(self.audio)
        self.tabs.addTab(self.sound_tools, tr("Sound tools"))

        self.catalog_page = AppCatalogPage(self.catalog, self.ctx.paths.platform)
        self.catalog_page.exportRequested.connect(self.open_export)
        self.tabs.addTab(self.catalog_page, tr("Where can I use my EQ?"))

        self.settings_page = SettingsPage(self.ctx, self.audio, self.apo, self.updates)
        self.settings_page.updateCheckRequested.connect(lambda: self.check_for_updates(manual=True))
        self.settings_page.openUrlRequested.connect(self.open_url)
        self.settings_page.apoChanged.connect(self.sync_other_eqs)
        self.settings_page.apoDeviceChangeRequested.connect(self.change_apo_device)
        self.settings_page.walkthroughRequested.connect(self.show_walkthrough)
        self.tabs.addTab(self.settings_page, tr("Settings"))
        self.about_page = AboutPage(self.ctx)
        self.tabs.addTab(self.about_page, tr("About"))
        self.audio.on_change(self._sync_eq_toggle)

    # ── first launch and listening safety ──────────────────────────────────
    def show_walkthrough(self) -> None:
        first = not self.ctx.settings.get("walkthrough_seen")
        Walkthrough(self.ctx.branding.product_name, self).exec()
        self.ctx.settings.set("walkthrough_seen", True)
        if first:
            self.offer_file_association()

    def ask_file_association(self) -> bool:
        answer = QMessageBox.question(
            self, tr("Open calibration files with {product}?").format(product=self.ctx.branding.product_name),
            tr("Open {suffix} calibration files with {product} when you double-click them? You can change this "
               "later in your system settings.").format(suffix=RESCAL_SUFFIX, product=self.ctx.branding.product_name))
        return answer == QMessageBox.Yes

    def offer_file_association(self) -> None:
        """Once, in release builds on Windows/Linux: offer double-click opening (per user, no admin)."""
        if self.ctx.settings.get("file_association_offered") or not self.association.supported:
            return
        if not self.ctx.settings.get("walkthrough_seen"):
            return  # after the walkthrough, not on top of it
        self.ctx.settings.set("file_association_offered", True)
        try:
            if not self.association.registered() and self.ask_file_association():
                self.association.register()
        except OSError as exc:
            self.statusBar().showMessage(tr("Couldn't set up double-click opening: {reason}").format(reason=exc), 10000)

    def confirm_quiet_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Start quiet"))
        box.setText(tr("Turn your volume down before the first sound."))
        box.setInformativeText(tr("Test tones can be piercing at the resonance. Start low and raise the "
                                  "volume slowly; press Space at any time to stop all sound.\n\n"
                                  "Turn off any other EQ first (your music app's, sound enhancements, "
                                  "Equalizer APO, your dongle's): you'd hear it on top of this one."))
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
        self.sync_other_eqs()

    def sync_other_eqs(self) -> None:
        """Tell the tuning panel which system-wide EQs are here, and whether ours is on in APO."""
        found = [app.name for app in self.catalog.apps
                 if app.id in self.installed and app.scope == "system"]
        try:
            apo_on = self.apo.available() and self.apo.is_on()
        except OSError:
            apo_on = False
        self.tuning.set_other_eqs(found, apo_on, self.apo.name)

    def turn_off_apo(self) -> None:
        try:
            self.apo.turn_off()
        except APPLY_PROBLEMS as problem:
            self.show_apo_problem(problem)
        self.settings_page.refresh_apo()
        self.sync_other_eqs()

    # ── Equalizer APO ──────────────────────────────────────────────────────
    def _sync_saved_tunings(self) -> None:
        entries = self.history.entries() if self.history is not None else []
        in_use = self.history.in_use if self.history is not None else None
        items = [(e.id, entry_name(e) + ("  ✓ " + tr("in use") if e.id == in_use else ""),
                  describe_entry(e, self.history_panel.app_names, e.id == in_use)) for e in entries]
        self.listen.set_saved_tunings(items)

    def choose_apo_device(self, devices: list[str]) -> str | None:
        device, ok = QInputDialog.getItem(
            self, tr("Choose your output"),
            tr("Which output do you listen to your IEMs on? You only choose this once."), devices, 0, False)
        return device if ok and device else None

    def show_apo_problem(self, problem) -> None:
        QMessageBox.warning(self, tr("{app} couldn't be changed").format(app=self.apo.name), tr(problem.fix))

    def apply_to_apo(self) -> bool:
        job = self.export_job()
        if job is None or not self.apo.available():
            return False
        if self.apo.uses_devices and (not self.apo.device or self.apo.device not in self.apo.devices()):
            devices = self.apo.devices()
            if not devices:
                QMessageBox.information(self, tr(self.apo.name), tr(self.apo.no_devices_help()))
                return False
            device = self.choose_apo_device(devices)
            if not device:
                return False
            self.apo.set_device(device)
        try:
            path = self.apo.apply(job.channel_eq, job.iem_name, job.meta.tuning)
        except APPLY_PROBLEMS as problem:
            self.show_apo_problem(problem)
            return False
        self.record_history("apply", files=[str(path)], app=getattr(self.apo, "app_id", APO_APP_ID))
        self.settings_page.refresh_apo()
        self.sync_other_eqs()
        if self.apo.uses_devices:
            self.listen.show_status(tr("Applied to {app} on {device}. Everything you play there now uses this "
                                       "tuning.").format(app=self.apo.name, device=self.apo.device))
        elif getattr(self.apo, "loaded", True):
            self.listen.show_status(tr("Applied in {app}. Everything you play now uses this tuning.").format(
                app=self.apo.name))
        else:
            self.listen.show_status(tr("Saved as the {app} preset “{preset}”. {app} isn't running: start it and "
                                       "choose that preset.").format(app=self.apo.name, preset=self.apo.preset))
        return True

    def change_apo_device(self) -> None:
        devices = self.apo.devices() if self.apo.available() else []
        if not devices:
            QMessageBox.information(self, tr(self.apo.name), tr(self.apo.no_devices_help()))
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
            tuning += "; " + tr("upper resonance {values}").format(
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
        snapshot = session.snapshot()
        known = self.history.find(snapshot, session.target) is not None
        entry = self.history.save(snapshot, session.target, reason, note, files, app)
        self.history_panel.refresh()
        if reason == "manual":
            if known:
                self.listen.show_status(tr("Already saved as “{name}”: it's at the top of History.").format(
                    name=entry_name(entry)))
            else:
                self.listen.show_status(tr("Saved as “{name}”. You can come back to it from History.").format(
                    name=entry_name(entry)))
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

    def open_files(self, paths, activate: bool = True) -> None:
        """Files from the command line, another launch or macOS Finder."""
        for path in paths:
            if str(path).lower().endswith(RESCAL_SUFFIX):
                self.open_calibration_file(path)
        if activate:
            self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
            self.show()
            self.raise_()
            self.activateWindow()

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
        QTimer.singleShot(0, self.offer_file_association)

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
