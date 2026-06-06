"""QApplication bootstrap for EQ Helper."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from eq_helper.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EQ Helper")
    window = MainWindow()
    window.show()
    return app.exec()
