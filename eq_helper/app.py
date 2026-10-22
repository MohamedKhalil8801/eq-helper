"""QApplication bootstrap for EQ Helper."""

from __future__ import annotations

import sys

from eq_helper.winquirks import disable_wmi_platform_queries

# MUST run before the heavy imports below: numpy/scipy/sounddevice call
# platform.system()/machine() at import time, which without this can block
# forever in a WMI query on a wedged Windows (the "exe starts but no window
# ever appears after changing audio devices" hang).
disable_wmi_platform_queries()

from PySide6.QtWidgets import QApplication  # noqa: E402

from eq_helper.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EQ Helper")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
