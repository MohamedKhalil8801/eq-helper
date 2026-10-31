"""Start the customer app.

``resonancefit [file.rescal ...]`` — calibration files given on the command
line (double-click / file association) are imported and selected; if the app
is already running they are handed to that window instead.
``resonancefit --self-test`` — headless check used by CI on every build.
``resonancefit --rf-install-update …`` — a downloaded update installing itself
over the old app once it has closed (updater.py).
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from eq_helper.winquirks import disable_wmi_platform_queries

# MUST run before the heavy imports: numpy/scipy/sounddevice call
# platform.system() at import time, which can hang forever on a wedged
# Windows WMI service (same guard as EQ Helper).
disable_wmi_platform_queries()

from eq_helper.calibration.rescal import RESCAL_SUFFIX  # noqa: E402

ERROR_LOG_MAX = 256 * 1024  # bytes: the log starts over beyond this
ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "resonancefit" / "assets" / "icon.png"


def calibration_args(argv) -> list[Path]:
    """The ``.rescal`` files named on the command line (flags ignored)."""
    return [Path(a) for a in list(argv)[1:]
            if not a.startswith("-") and a.lower().endswith(RESCAL_SUFFIX)]


def log_errors(path: Path, version: str = "") -> None:
    """Write uncaught errors to ``path`` (then report them as before). Errors in Qt slots otherwise
    vanish, leaving a button that silently does nothing; the log says what went wrong."""
    previous = sys.excepthook

    def hook(kind, value, tb):
        try:
            if path.exists() and path.stat().st_size > ERROR_LOG_MAX:
                path.unlink()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as log:
                log.write(f"\n{datetime.now().isoformat(timespec='seconds')} {version}\n")
                log.write("".join(traceback.format_exception(kind, value, tb)))
        except OSError:
            pass
        previous(kind, value, tb)

    sys.excepthook = hook


def _install_window(work) -> None:
    """While "Install and restart" replaces the files: a small window, so the wait isn't blank."""
    import threading

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

    from eq_helper.calibration.branding import load_branding

    from .ui.common import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme("system")
    name = load_branding().product_name
    window = QWidget()
    window.setWindowTitle(name)
    window.setWindowFlag(Qt.WindowStaysOnTopHint)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.addWidget(QLabel(f"Installing the new version of {name}…"))
    bar = QProgressBar()
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    layout.addWidget(bar)
    window.resize(380, window.sizeHint().height())
    window.show()
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    timer = QTimer()
    timer.timeout.connect(lambda: None if thread.is_alive() else app.quit())
    timer.start(100)
    app.exec()
    thread.join()


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from .updater import INSTALL_FLAG
    if INSTALL_FLAG in argv:
        from .updater import run_install
        return run_install(argv, show_progress=_install_window)
    if "--self-test" in argv:
        from .selftest import run_self_test
        return run_self_test(argv)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from eq_helper.calibration.branding import load_branding

    from .context import AppContext
    from .single_instance import FileOpenFilter, SingleInstance, server_name
    from .ui.common import MainThreadGC
    from .ui.main_window import MainWindow

    branding = load_branding()
    app = QApplication(argv)
    app.setApplicationName(branding.product_name)
    app.setApplicationDisplayName(branding.product_name)
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))
    collector = MainThreadGC(parent=app)  # noqa: F841 - lives as long as the app

    files = calibration_args(argv)
    instance = SingleInstance(server_name(branding.file_stem), app)
    if instance.forward(files):
        return 0  # the running window opens the files and comes to the front

    from .paths import AppPaths
    from .updater import wait_for_running_install
    wait_for_running_install(AppPaths.detect().updates_dir)  # an update finishing as the app closed

    pending: list[str] = []  # macOS may deliver files before the window exists
    window: MainWindow | None = None

    def on_file(path: str) -> None:
        if window is None:
            pending.append(path)
        else:
            window.open_files([path])

    app.installEventFilter(FileOpenFilter(on_file, app))
    ctx = AppContext.create(branding=branding)
    from .updater import AppLocation, Updater
    from .updates import UpdateChecker
    from .version import app_version
    version = app_version()
    log_errors(ctx.paths.data_dir / "errors.log", version)
    location = AppLocation.detect(ctx.paths.platform, bool(getattr(sys, "frozen", False)), sys.executable,
                                  os.environ)
    updates = UpdateChecker(ctx.settings, version, ctx.paths.platform,
                            updater=Updater(location, ctx.paths.updates_dir, version))
    window = MainWindow(ctx, updates=updates)
    instance.listen(window.open_files)
    window.open_files([*files, *pending], activate=False)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
