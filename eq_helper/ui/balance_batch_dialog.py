"""Dialog for batch balance-EQ generation: a folder of L/R measurements, the
reference side, and an L/R-invert flag (pre-set from config channel-swap)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
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

from eq_helper.dsp.balance_eq import detect_measurement_sets


class BalanceBatchDialog(QDialog):
    """Pick a measurements folder + reference side; generates every
    reference×other balance EQ. OK enabled once the folder has ≥1 Left and ≥1
    Right measurement."""

    def __init__(
        self,
        *,
        initial_invert: bool = False,
        swap_detected: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generate balance EQ candidates")
        self._folder: Optional[str] = None
        self._left_count = 0
        self._right_count = 0

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Pick the folder with your Left/Right measurements (named like "
                "'L 1', 'R 2'). One balance EQ is generated for every "
                "reference×other pairing, each as its own preset."
            )
        )

        row = QHBoxLayout()
        self._folder_label = QLabel("Folder: (none)")
        row.addWidget(self._folder_label, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_folder)
        row.addWidget(browse)
        layout.addLayout(row)

        self._found_label = QLabel("")
        layout.addWidget(self._found_label)

        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("Reference side:"))
        self.ref_left = QRadioButton("Left")
        self.ref_right = QRadioButton("Right")
        self.ref_left.setChecked(True)
        ref_row.addWidget(self.ref_left)
        ref_row.addWidget(self.ref_right)
        ref_row.addStretch(1)
        layout.addLayout(ref_row)

        self.invert_check = QCheckBox("Invert L/R (config swaps channels)")
        self.invert_check.setChecked(initial_invert)
        if swap_detected:
            self.invert_check.setText(
                "Invert L/R (channel swap detected in this preset)"
            )
        layout.addWidget(self.invert_check)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update_ok_enabled()

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Measurements folder")
        if path:
            self.set_folder(path)

    def set_folder(self, path: str) -> None:
        self._folder = path
        self._folder_label.setText(f"Folder: {path}")
        left, right = detect_measurement_sets(path)
        self._left_count, self._right_count = len(left), len(right)
        self._found_label.setText(
            f"Found {self._left_count} Left, {self._right_count} Right → "
            f"{self._left_count * self._right_count} candidate(s)."
        )
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(
            bool(self._folder) and self._left_count > 0 and self._right_count > 0
        )

    # --------------------------------------------------------- accessors

    def folder(self) -> Optional[str]:
        return self._folder

    def reference(self) -> str:
        return "L" if self.ref_left.isChecked() else "R"

    def invert(self) -> bool:
        return self.invert_check.isChecked()
