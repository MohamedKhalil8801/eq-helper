"""Dialog to collect the two measurements + reference side for a balance EQ."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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

    def __init__(
        self,
        *,
        initial_invert: bool = False,
        swap_detected: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
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

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self.method_reference = QRadioButton("Reference side")
        self.method_midpoint = QRadioButton("Midpoint (both → average)")
        self._method_group = QButtonGroup(self)  # independent of the ref group
        self._method_group.addButton(self.method_reference)
        self._method_group.addButton(self.method_midpoint)
        self.method_reference.setChecked(True)
        self.method_reference.toggled.connect(self._on_method_changed)
        method_row.addWidget(self.method_reference)
        method_row.addWidget(self.method_midpoint)
        method_row.addStretch(1)
        layout.addLayout(method_row)

        ref_row = QHBoxLayout()
        self._ref_label = QLabel("Reference side:")
        ref_row.addWidget(self._ref_label)
        self.ref_left = QRadioButton("Left")
        self.ref_right = QRadioButton("Right")
        self._ref_group = QButtonGroup(self)
        self._ref_group.addButton(self.ref_left)
        self._ref_group.addButton(self.ref_right)
        self.ref_left.setChecked(True)
        ref_row.addWidget(self.ref_left)
        ref_row.addWidget(self.ref_right)
        ref_row.addStretch(1)
        layout.addLayout(ref_row)

        self.invert_check = QCheckBox("Invert L/R (config swaps channels)")
        self.invert_check.setChecked(initial_invert)
        if swap_detected:
            self.invert_check.setText("Invert L/R (channel swap detected)")
        layout.addWidget(self.invert_check)

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

    def method(self) -> str:
        return "midpoint" if self.method_midpoint.isChecked() else "reference"

    def invert(self) -> bool:
        return self.invert_check.isChecked()

    def _on_method_changed(self) -> None:
        # The reference side is irrelevant to the midpoint method.
        ref_relevant = self.method_reference.isChecked()
        for w in (self._ref_label, self.ref_left, self.ref_right):
            w.setEnabled(ref_relevant)
