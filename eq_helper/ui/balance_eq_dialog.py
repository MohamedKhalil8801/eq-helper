"""Dialog to collect the two measurements + reference side for a balance EQ."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

_FILTER = "Measurements (*.txt *.csv);;All files (*.*)"


class BalanceEqDialog(QDialog):
    """Pick the Left and Right measurement files and the reference side.

    Pure data collection — generation happens in the caller. ``OK`` stays
    disabled until both measurement files are chosen.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generate balance EQ")
        self._left: Optional[str] = None
        self._right: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Import the Left and Right measurements (REW text export or "
                "AutoEQ/CSV). The chosen reference side is left untouched; the "
                "other side is corrected to match it."
            )
        )

        self._left_label = QLabel("Left measurement: (none)")
        self._right_label = QLabel("Right measurement: (none)")
        layout.addLayout(self._file_row(self._left_label, self._pick_left))
        layout.addLayout(self._file_row(self._right_label, self._pick_right))

        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("Reference side:"))
        self.ref_left = QRadioButton("Left")
        self.ref_right = QRadioButton("Right")
        self.ref_left.setChecked(True)
        ref_row.addWidget(self.ref_left)
        ref_row.addWidget(self.ref_right)
        ref_row.addStretch(1)
        layout.addLayout(ref_row)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update_ok_enabled()

    # ------------------------------------------------------------ file rows

    def _file_row(self, label: QLabel, on_browse) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(label, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(on_browse)
        row.addWidget(btn)
        return row

    def _pick_left(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Left measurement", "", _FILTER)
        if path:
            self.set_left_path(path)

    def _pick_right(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Right measurement", "", _FILTER)
        if path:
            self.set_right_path(path)

    # ------------------------------------------------- programmatic setters

    def set_left_path(self, path: str) -> None:
        self._left = path
        self._left_label.setText(f"Left measurement: {Path(path).name}")
        self._update_ok_enabled()

    def set_right_path(self, path: str) -> None:
        self._right = path
        self._right_label.setText(f"Right measurement: {Path(path).name}")
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(
            bool(self._left) and bool(self._right)
        )

    # ------------------------------------------------------------ accessors

    def left_path(self) -> Optional[str]:
        return self._left

    def right_path(self) -> Optional[str]:
        return self._right

    def reference(self) -> str:
        return "L" if self.ref_left.isChecked() else "R"
