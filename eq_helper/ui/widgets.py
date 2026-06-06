"""Small reusable Qt widgets."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleSection(QWidget):
    """A click-to-collapse section: an arrow/title header above a content widget.

    Used to fold away less-used panels (Microphone, Frequency isolation) to free
    vertical space without removing them.
    """

    toggled = Signal(bool)  # emits the new expanded state

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._content = content
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._btn = QToolButton(self)
        self._btn.setText(title)
        self._btn.setCheckable(True)
        self._btn.setChecked(expanded)
        self._btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._btn.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self._btn.toggled.connect(self._on_toggled)

        layout.addWidget(self._btn)
        layout.addWidget(content)
        content.setVisible(expanded)

    def _on_toggled(self, expanded: bool) -> None:
        self._content.setVisible(expanded)
        self._btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self._btn.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._btn.setChecked(expanded)


class LabeledSlider(QWidget):
    """A slider with a text label and a live numeric readout.

    Operates in floating-point units; internally uses an integer slider
    with a configurable scale factor.
    """

    valueChanged = Signal(float)

    def __init__(
        self,
        label: str,
        minimum: float,
        maximum: float,
        value: float,
        *,
        step: float = 1.0,
        suffix: str = "",
        formatter: Optional[Callable[[float], str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._step = float(step)
        self._suffix = suffix
        self._formatter = formatter

        self._label = QLabel(label, self)
        self._readout = QLabel("", self)
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setMinimum(0)
        self._slider.setMaximum(int(round((self._max - self._min) / self._step)))
        self._slider.setSingleStep(1)
        self._slider.valueChanged.connect(self._on_slider_changed)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._label)
        row.addStretch()
        row.addWidget(self._readout)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addLayout(row)
        layout.addWidget(self._slider)

        self.set_value(value)

    def _on_slider_changed(self, int_val: int) -> None:
        value = self._min + int_val * self._step
        self._update_readout(value)
        self.valueChanged.emit(value)

    def _update_readout(self, value: float) -> None:
        if self._formatter is not None:
            text = self._formatter(value)
        else:
            text = f"{value:.2f}{self._suffix}"
        self._readout.setText(text)

    def value(self) -> float:
        return self._min + self._slider.value() * self._step

    def set_value(self, value: float) -> None:
        value = max(self._min, min(self._max, float(value)))
        int_val = int(round((value - self._min) / self._step))
        self._slider.blockSignals(True)
        self._slider.setValue(int_val)
        self._slider.blockSignals(False)
        self._update_readout(value)


class ThreeWaySwitch(QWidget):
    """A 3-button radio group (used for L / Both / R channel selection)."""

    selectionChanged = Signal(int)  # emits index (0/1/2)

    def __init__(
        self,
        labels: tuple[str, str, str],
        initial: int = 1,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._buttons: list[QPushButton] = []
        for i, text in enumerate(labels):
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            if i == initial:
                btn.setChecked(True)
            self._group.addButton(btn, i)
            layout.addWidget(btn)
            self._buttons.append(btn)
        self._group.idClicked.connect(self.selectionChanged.emit)

    def set_index(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def index(self) -> int:
        return self._group.checkedId()
