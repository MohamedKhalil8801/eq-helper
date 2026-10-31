"""The customer app's main window: import screen or header + tabs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QProgressBar, QPushButton, QMessageBox,
    QScrollArea, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
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
EQ_WATCH_MS = 3000  # how often the active window re-checks whether our EQ is on in Equalizer APO


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
from ..updater import UpdateError
from .about_page import AboutPage
from .common import (
    Worker, apply_theme, brand_palette, build_style, install_keyboard_focus, is_branded, side_name, tr,
)
from .export_flow import PREDICTED_APP_ID, ExportFlow, ExportJob
from .header_card import HeaderCard, calibration_label
from .import_screen import ImportScreen
from .tunings_panel import CURRENT, TuningsPanel, describe_entry, entry_name, tuning_detail
from .settings_page import SettingsPage
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
    """A slim strip above the tabs. Where the app updates itself: "Downloading X… 40%", then "X is ready"
    with Install and restart (it also installs when the app closes). Elsewhere: "X is available" with
    Download (the link). What's new and Not now in every state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBanner")
        self.setStyleSheet("#updateBanner { border: 1px solid palette(highlight); border-radius: 6px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        self.text = QLabel()
        self.text.setWordWrap(True)
        texts.addWidget(self.text)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        texts.addWidget(self.progress)
        row.addLayout(texts, 1)
        self.install = QPushButton(tr("Install and restart"))
        self.install.setObjectName("primary")
        self.install.setToolTip(tr("Closes the app, installs the new version and opens it again."))
        row.addWidget(self.install)
        self.retry = QPushButton(tr("Try again"))
        self.retry.setObjectName("primary")
        row.addWidget(self.retry)
        self.download = QPushButton(tr("Download"))
        row.addWidget(self.download)
        self.details = QPushButton(tr("What's new"))
        row.addWidget(self.details)
        self.dismiss = QPushButton(tr("Not now"))
        row.addWidget(self.dismiss)
        self.result = None
        self.state = ""  # "manual", "downloading", "ready" or "failed"
        self.hide()

    def _show(self, state: str, text: str, buttons: tuple) -> None:
        self.state = state
        self.text.setText(text)
        for button in (self.install, self.retry, self.download):
            button.setVisible(button in buttons)
        self.download.setObjectName("primary" if state == "manual" else "")
        self.download.setText(tr("Download") if state == "manual" else tr("Download it yourself"))
        self.download.style().unpolish(self.download)
        self.download.style().polish(self.download)
        self.progress.setVisible(state == "downloading")
        self.details.setVisible(bool(self.result is not None and self.result.page_url))
        self.dismiss.setToolTip(
            tr("Hide this until a newer version comes out. Settings still shows it.") if state == "manual" else
            tr("Hide this for now. The new version still installs when you close the app.")
            if state in ("downloading", "ready") else tr("Hide this for now."))

    def show_manual(self, product: str, latest: str, current: str) -> None:
        self._show("manual", tr("{product} {latest} is available (you have {current}). Your calibrations, history "
                                "and settings are kept when you update.").format(
            product=product, latest=latest, current=current), (self.download,))

    def show_downloading(self, product: str, latest: str, done: int, total: int) -> None:
        percent = int(100 * done / total) if total else 0
        self._show("downloading", tr("Downloading {product} {latest}… {percent}%").format(
            product=product, latest=latest, percent=percent), ())
        self.progress.setRange(0, 1000 if total else 0)
        self.progress.setValue(int(1000 * done / total) if total else 0)

    def show_ready(self, product: str, latest: str, failure: str = "") -> None:
        text = tr("{product} {latest} is ready. It installs when you close the app, or now: the app restarts and "
                  "your calibrations, history and settings are kept.").format(product=product, latest=latest)
        if failure:
            text = tr("Installing {latest} didn't work last time ({reason}). Your current version is untouched. "
                      "Try again, or download it yourself.").format(latest=latest, reason=failure)
        self._show("ready", text, (self.install, self.download) if failure else (self.install,))

    def show_failed(self, latest: str, reason: str) -> None:
        self._show("failed", tr("Couldn't download {latest} ({reason}).").format(latest=latest, reason=reason),
                   (self.retry, self.download))


class _DownloadProgress(QObject):
    """Hands a worker thread's download progress to the GUI thread, once per tenth of a percent at most."""

    changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = -1

    def report(self, done: int, total: int) -> None:
        step = int(1000 * done / total) if total else done >> 20
        if step != self._last:
            self._last = step
            self.changed.emit(done, total)


class MainWindow(QMainWindow):
    calibrationLoaded = Signal(object)  # LoadedCalibration
    audioReady = Signal(bool)

    def __init__(self, ctx, audio: AudioService | None = None, start_audio: bool = True,
                 apo: ApoApplier | None = None, association: FileAssociation | None = None, updates=None):
        super().__init__()
        self.ctx = ctx
        self.updates = updates
        self._update_worker: Worker | None = None
        self._download_worker: Worker | None = None
        self._cancel_download = False
        self._update_ready = None      # updater.Staged: downloaded, installs on close
        self._update_started = False   # "Install and restart" started the install
        self._guided = False
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
        apply_theme(ctx.settings.get("theme"))
        self._brand()
        app = QApplication.instance()
        if app is not None:  # "System" follows the computer switching between light and dark
            app.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
            self._focus_watch = install_keyboard_focus(app)  # focus rings for keyboard users only
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
        self.update_banner.install.clicked.connect(self.install_update_now)
        self.update_banner.retry.clicked.connect(lambda: self.download_update(self.update_banner.result))
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
        self.header.removeRequested.connect(self.remove_calibration)
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
        self.stop_shortcut.activated.connect(self.toggle_sound)

        self.audioReady.connect(self._on_audio_ready)
        if start_audio:
            self.audio.start(self.audioReady.emit)
        for path, reason in ctx.scan_report.problems:
            self.statusBar().showMessage(tr("Couldn't read {name}: {reason}").format(name=path.name, reason=reason),
                                         15000)
        self._run(lambda: detect_installed(self.catalog, ctx.paths.platform), self._apps_detected)
        self.refresh()
        self._launch_update_check()

    def toggle_sound(self) -> None:
        """Space, as in EQ Helper: stops whatever plays; when nothing does, plays again (the Tune page's last
        sound, or the guided test's card)."""
        if self.audio.playing is not None:
            self.audio.stop_all()
            return
        if self.stack.currentWidget() is not self.main_view or self.tabs.currentIndex() != 0:
            return
        if self.tuning.guided_active:
            self.tuning.guided.toggle_play()
        else:
            self.tuning.replay()

    def _system_scheme_changed(self, _scheme) -> None:
        if (self.ctx.settings.get("theme") or "system") == "system":
            apply_theme("system")

    def _brand(self) -> None:
        """One palette everywhere: the app's accent instead of the system's (again after a light/dark
        switch, which brings the system's back)."""
        app = QApplication.instance()
        if app is not None and not is_branded(app.palette()):
            app.setPalette(brand_palette(app.palette()))
        self.setStyleSheet(build_style(self.palette()))

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
        self._guided = active
        for widget in (self.header, self.tabs.tabBar(), self.sidebar):
            widget.setVisible(not active)
        self.update_banner.setVisible(not active and getattr(self, "_banner_shown", False))

    # ── updates ────────────────────────────────────────────────────────────
    def open_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @property
    def _updater(self):
        """Downloads and installs updates, where this copy of the app can replace itself."""
        updater = self.updates.updater if self.updates is not None else None
        return updater if updater is not None and updater.can_install else None

    def _reveal_banner(self) -> None:
        if self._guided:
            self._banner_shown = True  # shown when the guided test ends
        else:
            self.update_banner.show()

    def _launch_update_check(self) -> None:
        """At launch only: check unless today's check already succeeded (a failed one is retried). A
        development build never checks."""
        if self.updates is None or not self.updates.enabled:
            return
        if self._updater is not None:
            self._updater.tidy()
            ready = self._updater.ready()
            if ready is not None:
                self._downloaded(ready)
        if self.updates.automatic and self.updates.due():
            self.check_for_updates(manual=False)
        else:
            self._show_update(self.updates.last_result(), manual=False)

    def check_for_updates(self, manual: bool = True) -> None:
        if self.updates is None or not self.updates.enabled or self._update_worker is not None:
            return
        self.settings_page.set_checking()
        self._update_worker = self._run(self.updates.check, lambda result: self._update_checked(result, manual))

    def _update_checked(self, result, manual: bool) -> None:
        self._update_worker = None
        self._show_update(result, manual)

    def _show_update(self, result, manual: bool) -> None:
        self.settings_page.show_update_result(result)
        if result is None or not (result.ok and result.available):
            if self._update_ready is None and self._download_worker is None:
                self.update_banner.hide()
            return
        self.update_banner.result = result
        if self._updater is not None:
            self.download_update(result)
            return
        if not manual and self.updates.dismissed(result.latest):
            return
        self.update_banner.show_manual(self.ctx.branding.product_name, result.latest, self.updates.current_version)
        self._reveal_banner()

    def download_update(self, result) -> None:
        """Download ``result`` in the background (once: a finished download is kept until installed)."""
        updater = self._updater
        if updater is None or result is None or self._download_worker is not None:
            return
        if self._update_ready is not None and self._update_ready.version == result.latest:
            self._downloaded(self._update_ready)
            return
        self._cancel_download = False
        progress = _DownloadProgress(self)
        progress.changed.connect(lambda done, total: self._download_progress(result, done, total))
        worker = self._run(lambda: updater.download(result, progress.report, lambda: self._cancel_download),
                           self._downloaded)
        worker.failed.connect(lambda reason: self._download_failed(result, reason))
        self._download_worker = worker
        self._download_progress(result, 0, result.size)
        self._reveal_banner()

    def _download_progress(self, result, done: int, total: int) -> None:
        if self._download_worker is None:
            return
        self.update_banner.show_downloading(self.ctx.branding.product_name, result.latest, done, total)
        self.settings_page.show_update_downloading(result.latest, done, total)

    def _downloaded(self, staged) -> None:
        self._download_worker = None
        self._update_ready = staged
        failure = self._updater.last_failure() if self._updater is not None else ""
        if self.update_banner.result is None or self.update_banner.result.latest != staged.version:
            last = self.updates.last_result()
            self.update_banner.result = last if last is not None and last.latest == staged.version else None
        self.update_banner.show_ready(self.ctx.branding.product_name, staged.version, failure)
        self.settings_page.show_update_ready(staged.version, failure)
        self._reveal_banner()

    def _download_failed(self, result, reason: str) -> None:
        self._download_worker = None
        if self._cancel_download:
            return
        self.update_banner.show_failed(result.latest, reason)
        self.settings_page.show_update_download_failed(result.latest, reason)
        self._reveal_banner()

    def install_update_now(self) -> None:
        """Install and restart: the new version installs itself once this window has closed."""
        updater = self._updater
        if updater is None or self._update_ready is None:
            return
        try:
            started = updater.install(relaunch=True)
        except UpdateError as exc:
            self.update_banner.show_ready(self.ctx.branding.product_name, self._update_ready.version, str(exc))
            return
        if started:
            self._update_started = True
            self.close()

    def _install_on_close(self) -> None:
        """Closing the app installs a downloaded update (without opening the app again)."""
        updater = self._updater
        if updater is None or self._update_started or self._update_ready is None:
            return
        try:
            self._update_started = updater.install(relaunch=False)
        except UpdateError:
            pass  # still downloaded: offered again at the next launch

    def _dismiss_update(self) -> None:
        if self.update_banner.result is not None and self.update_banner.state == "manual":
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
        self.tuning.eqToggled.connect(self.audio.set_eq_enabled)
        for widget in (*self.tuning.target_widgets(), self.tuning.power_button):
            self.header.add_trailing(widget)
        self.header.add_action(tr("Import a target…"), self.tuning.browse_target)
        self.tuning.eqChanged.connect(lambda _eq: self._sync_tunings())
        self._eq_watch = QTimer(self)  # Equalizer APO can be switched on or off outside the app at any time
        self._eq_watch.setInterval(EQ_WATCH_MS)
        self._eq_watch.timeout.connect(lambda: self.sync_other_eqs() if self.isActiveWindow() else None)
        self._eq_watch.start()
        self.calibrationLoaded.connect(self.tuning.set_calibration)
        tune.addWidget(self.tuning, 1)

        self.tunings = TuningsPanel()
        self.tunings.saveRequested.connect(lambda note: self.record_history("manual", note))
        self.tunings.switchRequested.connect(self.reapply)
        self.tunings.currentRequested.connect(self.back_to_unsaved)
        self.tunings.exportRequested.connect(self.open_export)
        self.tunings.applyRequested.connect(self.apply_to_apo)
        self.tunings.changed.connect(self._sync_tunings)
        self.tunings.set_apply_target(self.apo.name)
        self.tunings.set_apply_available(self.apo.available())
        self.app_names = {app.id: app.name for app in self.catalog.apps}
        self.app_names[PREDICTED_APP_ID] = tr("Predicted response")
        self._unsaved = None  # (Tuning, target) of the last tuning that matched no saved one
        self.sidebar = self.tunings
        self.sidebar.setFixedWidth(330)
        tune.addWidget(self.sidebar)
        self.tuning.guidedActiveChanged.connect(self._guided_focus)
        self.calibrationLoaded.connect(self._calibration_ready)
        tune_scroll = QScrollArea()
        tune_scroll.setWidgetResizable(True)
        tune_scroll.setFrameShape(QScrollArea.NoFrame)
        tune_scroll.setWidget(self.tune_tab)
        self.tune_tab.setMinimumHeight(700)  # below this, scroll instead of squeezing the graph
        self.tabs.addTab(tune_scroll, tr("Tune"))

        self.songs_page = TestSongsPage(self.ctx, self.audio)
        self.calibrationLoaded.connect(self.songs_page.set_calibration)
        self.tabs.addTab(self.songs_page, tr("Test audio"))

        self.settings_page = SettingsPage(self.ctx, self.audio, self.apo, self.updates)
        self.settings_page.updateCheckRequested.connect(lambda: self.check_for_updates(manual=True))
        self.settings_page.installUpdateRequested.connect(self.install_update_now)
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
            self._brand()
        elif event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.sync_other_eqs()  # back from Equalizer APO's own window, say
        super().changeEvent(event)

    def _apps_detected(self, installed) -> None:
        self.installed = set(installed)
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
    def _sync_tunings(self) -> None:
        """The Tunings list: the unsaved tuning (if any) on top, then the saved ones, the one you hear
        selected."""
        session = self.tuning.session
        if self.history is None or session is None or session.target is None:
            self.tunings.set_rows(None, [], None)
            return
        snapshot = session.snapshot()
        found = self.history.find(snapshot, session.target)
        if found is None:
            self._unsaved = (snapshot, session.target)
        elif self._unsaved is not None and self.history.find(*self._unsaved) is not None:
            self._unsaved = None  # it's been saved since
        current = None
        if self._unsaved is not None:
            title = tr("Current · unsaved") if found is None else tr("Last unsaved")
            current = (title, tuning_detail(self._unsaved[0]))
        in_use = self.history.in_use
        saved = [(e.id, e.note or e.target_name or tr("Tuning"), tuning_detail(e.tuning),
                  tr("in use") if e.id == in_use else "", describe_entry(e, self.app_names, e.id == in_use))
                 for e in self.history.entries()]
        self.tunings.set_rows(current, saved, CURRENT if found is None else found.id)

    def back_to_unsaved(self) -> None:
        if self._unsaved is None:
            return
        tuning, target = self._unsaved
        try:
            self.tuning.apply_tuning(tuning, target)
        except ValueError as exc:
            self.statusBar().showMessage(tr("Couldn't go back to it: {reason}").format(reason=exc), 15000)

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
            self.tunings.show_status(tr("Applied to {app} on {device}. Everything you play there now uses this "
                                       "tuning.").format(app=self.apo.name, device=self.apo.device))
        elif getattr(self.apo, "loaded", True):
            self.tunings.show_status(tr("Applied in {app}. Everything you play now uses this tuning.").format(
                app=self.apo.name))
        else:
            self.tunings.show_status(tr("Saved as the {app} preset “{preset}”. {app} isn't running: start it and "
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
        self.tuning.set_eq_on(self.audio.eq_enabled)

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
        self.tunings.set_store(self.history)
        self._unsaved = None
        self._sync_tunings()

    def record_history(self, reason: str, note: str = "", files=(), app=None):
        """Save the current tuning to history (export / apply / manual)."""
        session = self.tuning.session
        if session is None or session.target is None or self.history is None:
            return None
        snapshot = session.snapshot()
        known = self.history.find(snapshot, session.target) is not None
        entry = self.history.save(snapshot, session.target, reason, note, files, app)
        self._sync_tunings()
        if reason == "manual":
            if known:
                self.tunings.show_status(tr("Already saved as “{name}”.").format(name=entry_name(entry)))
            else:
                self.tunings.show_status(tr("Saved as “{name}”. Click it any time to come back to it.").format(
                    name=entry_name(entry)))
        return entry

    def reapply(self, entry_id: str) -> None:
        tuning, target = self.history.reapply(entry_id)
        try:
            self.tuning.apply_tuning(tuning, target)
        except ValueError as exc:
            self.statusBar().showMessage(tr("Couldn't switch to this tuning: {reason}").format(reason=exc), 15000)

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

    def confirm_remove(self, label: str) -> bool:
        answer = QMessageBox.question(
            self, tr("Remove this calibration?"),
            tr("Remove “{name}” from {product}? The calibration file itself isn't deleted: open it again "
               "to bring it back, with its history.").format(
                name=label, product=self.ctx.branding.product_name))
        return answer == QMessageBox.Yes

    def remove_calibration(self, calibration_id: str) -> None:
        entry = next((e for e in self.ctx.library.list() if e.calibration_id == calibration_id), None)
        if entry is None or not self.confirm_remove(calibration_label(entry)):
            return
        self.audio.stop_all()
        self.ctx.library.remove(calibration_id)
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
        self.tuning.flush()
        self.audio.stop_all()
        self._cancel_download = True  # an unfinished download starts over next time
        for worker in list(self._workers):
            worker.wait(5000)
        self._install_on_close()
        super().closeEvent(event)
