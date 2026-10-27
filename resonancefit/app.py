"""Start the customer app.

``resonancefit [file.rescal ...]`` — calibration files given on the command
line (double-click / file association) are imported and selected; if the app
is already running they are handed to that window instead.
``resonancefit --self-test`` — headless check used by CI on every build.
"""

from __future__ import annotations

import sys
from pathlib import Path

from eq_helper.winquirks import disable_wmi_platform_queries

# MUST run before the heavy imports: numpy/scipy/sounddevice call
# platform.system() at import time, which can hang forever on a wedged
# Windows WMI service (same guard as EQ Helper).
disable_wmi_platform_queries()

from eq_helper.calibration.rescal import RESCAL_SUFFIX  # noqa: E402

ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "resonancefit" / "assets" / "icon.png"


def calibration_args(argv) -> list[Path]:
    """The ``.rescal`` files named on the command line (flags ignored)."""
    return [Path(a) for a in list(argv)[1:]
            if not a.startswith("-") and a.lower().endswith(RESCAL_SUFFIX)]


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--self-test" in argv:
        from .selftest import run_self_test
        return run_self_test(argv[:1])

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

    pending: list[str] = []  # macOS may deliver files before the window exists
    window: MainWindow | None = None

    def on_file(path: str) -> None:
        if window is None:
            pending.append(path)
        else:
            window.open_files([path])

    app.installEventFilter(FileOpenFilter(on_file, app))
    ctx = AppContext.create(branding=branding)
    window = MainWindow(ctx)
    instance.listen(window.open_files)
    window.open_files([*files, *pending], activate=False)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
