"""Combined EQ library panel: a master→detail (drill-down) over the Profiles
browser and the EQ-filters editor.

Page 1 (browser) is the Profiles panel — device, preset switcher, EQ-file stack.
Double-clicking an EQ file there drills into page 2 (editor): a "‹ Back" bar
plus the EQ-filters editor for that file, which gets the panel's full height.
Back returns to the browser.

The two inner panels are reused as-is; this is just the navigation shell, so
the main window's existing signal wiring (editEqFileRequested, set_eq_file,
curvesChanged, …) is unaffected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from eq_helper.ui.eq_mode_panel import EQModePanel
from eq_helper.ui.profile_panel import ProfilePanel


class EqLibraryPanel(QWidget):
    def __init__(
        self,
        profile_panel: ProfilePanel,
        eq_panel: EQModePanel,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._profile_panel = profile_panel
        self._eq_panel = eq_panel

        self._stack = QStackedWidget(self)
        self._stack.addWidget(profile_panel)  # page 0: browser

        editor = QWidget()
        ev = QVBoxLayout(editor)
        ev.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self._back = QPushButton("‹ Back")
        self._back.setToolTip("Back to presets")
        self._crumb = QLabel("")
        self._crumb.setStyleSheet("font-weight: bold;")
        bar.addWidget(self._back)
        bar.addWidget(self._crumb, 1)
        ev.addLayout(bar)
        ev.addWidget(eq_panel, 1)
        self._stack.addWidget(editor)  # page 1: editor

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._back.clicked.connect(self.show_browser)
        profile_panel.editEqFileRequested.connect(self._on_edit_requested)
        self.show_browser()

    # ------------------------------------------------------------ navigation

    def show_browser(self) -> None:
        self._stack.setCurrentIndex(0)

    def show_editor(self) -> None:
        self._stack.setCurrentIndex(1)

    def current_is_browser(self) -> bool:
        return self._stack.currentIndex() == 0

    def _on_edit_requested(self, path: str) -> None:
        self._crumb.setText(f"Editing: {Path(path).stem}")
        self.show_editor()
