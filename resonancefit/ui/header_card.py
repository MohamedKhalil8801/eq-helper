"""Header: a slim bar with the IEM and its calibration, the target, the EQ switch and a ⋯ menu.

One caption line under the IEM says whose calibration it is and the accessories it was measured with
(use the same ones when you listen); the coupler and notes are in the IEM name's tooltip.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget,
)

from .common import theme_colors, tr

ACCESSORY_KINDS = {
    "tips": "Ear tips", "dac": "DAC / amp", "amp": "Amp", "cable": "Cable",
    "nozzle": "Nozzle / filter", "filter": "Nozzle / filter", "other": "Other",
}


def accessory_label(kind: str) -> str:
    return tr(ACCESSORY_KINDS.get(kind.lower(), kind.capitalize()))


def supplied_label(accessory, manifest) -> str:
    if accessory.supplied_by == "customer":
        return tr("yours")
    operator = manifest.operator.name if manifest.operator and manifest.operator.name else tr("your tuner")
    return tr("provided by {name}").format(name=operator)


def calibration_label(entry) -> str:
    m = entry.manifest
    return f"{m.customer.name} – {m.iem.display_name} ({m.created})"


class PowerSwitch(QAbstractButton):
    """The EQ's power switch, like on a piece of studio gear: a pill with a status light (amber when the EQ
    is on) and its state in words."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.toggled.connect(lambda _on: self.update())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        fm = self.fontMetrics()
        width = max(fm.horizontalAdvance(t) for t in (tr("EQ on"), tr("EQ off"), self.text()))
        return QSize(width + 44, max(30, fm.height() + 14))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        c = theme_colors(self.palette())
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        hover = self.underMouse()
        p.setPen(QColor(c["focus"]) if self.property("kbfocus") else Qt.NoPen)
        p.setBrush(QColor(c["hover"] if hover else c["control"]))
        p.drawRoundedRect(rect, radius, radius)
        on = self.isChecked()
        cx, cy = rect.left() + radius, rect.center().y()
        if on:  # the light, with a soft halo
            halo = QColor(c["accent"])
            halo.setAlpha(60)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QRectF(cx - 7, cy - 7, 14, 14))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["accent"] if on else c["border"]))
        p.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))
        p.setPen(QColor(c["text"] if on else c["hint"]))
        font = self.font()
        font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        p.drawText(rect.adjusted(radius + 12, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class HeaderCard(QFrame):
    calibrationChosen = Signal(str)
    removeRequested = Signal(str)  # calibration id

    def __init__(self, branding, parent=None):
        super().__init__(parent)
        self.setObjectName("bar")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 10, 10, 10)
        outer.setSpacing(10)

        who = QVBoxLayout()
        who.setSpacing(2)
        first = QHBoxLayout()
        first.setSpacing(10)
        self.iem = QLabel()
        self.iem.setObjectName("iem")
        self.iem.setTextInteractionFlags(Qt.TextSelectableByMouse)
        first.addWidget(self.iem)
        self.switcher = QComboBox()
        self.switcher.setAccessibleName(tr("Choose calibration"))
        self.switcher.setToolTip(tr("Which of your calibrations you're tuning"))
        self.switcher.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.switcher.currentIndexChanged.connect(self._chosen)
        first.addWidget(self.switcher)
        first.addStretch(1)
        who.addLayout(first)
        self.customer = QLabel()
        self.customer.setObjectName("caption")
        self.customer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        who.addWidget(self.customer)
        outer.addLayout(who, 1)

        self.trailing = QHBoxLayout()  # target, EQ switch: added by the main window
        self.trailing.setSpacing(8)
        outer.addLayout(self.trailing)
        self.more = QToolButton()
        self.more.setText("⋯")
        font = self.more.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        self.more.setFont(font)
        self.more.setAccessibleName(tr("More"))
        self.more.setToolTip(tr("Import a target, remove this calibration"))
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.more)
        self.more.setMenu(self.menu)
        outer.addWidget(self.more, 0, Qt.AlignVCenter)
        self.remove_action = self.menu.addAction(tr("Remove this calibration…"), self._remove)
        self.byline = f"{branding.product_name} {branding.byline}".strip()
        self._summary: list[str] = []
        self._updating = False

    # ── content ────────────────────────────────────────────────────────────
    def set_entries(self, entries, selected_id: str | None) -> None:
        self._updating = True
        self.switcher.clear()
        for entry in entries:
            self.switcher.addItem(calibration_label(entry), entry.calibration_id)
        index = self.switcher.findData(selected_id)
        self.switcher.setCurrentIndex(max(index, 0))
        self.switcher.setEnabled(len(entries) > 1)
        self._updating = False

    def add_trailing(self, widget: QWidget) -> None:
        self.trailing.addWidget(widget, 0, Qt.AlignVCenter)

    def add_action(self, text: str, slot):
        """An entry in the ⋯ menu, above Remove."""
        action = self.menu.addAction(text, slot)
        self.menu.removeAction(action)
        self.menu.insertAction(self.remove_action, action)
        return action

    def set_manifest(self, manifest) -> None:
        self.iem.setText(manifest.iem.display_name)
        serial = f", {tr('serial')} {manifest.iem.serial}" if manifest.iem.serial else ""
        who = tr("Calibrated for {name} on {date}").format(name=manifest.customer.name,
                                                           date=manifest.created) + serial
        gear = [f"{accessory_label(a.kind)}: {a.description} ({supplied_label(a, manifest)})"
                for a in manifest.accessories]
        self.customer.setText("  ·  ".join([who, *gear]))
        details = []
        if manifest.coupler:
            details.append(tr("Measured on: {coupler}").format(coupler=manifest.coupler))
        if manifest.notes:
            details.append(manifest.notes)
        tip = [tr("Use the same accessories when you listen: the calibration was measured with them."), *details]
        self.iem.setToolTip("\n".join(tip))
        self.customer.setToolTip("\n".join(tip))
        self._summary = [manifest.iem.display_name, who, *gear, *details]

    def text_summary(self) -> str:
        """Everything the header says, as plain text (tests, screen readers)."""
        return "\n".join(p for p in self._summary if p)

    def _remove(self) -> None:
        cid = self.switcher.currentData()
        if cid:
            self.removeRequested.emit(cid)

    def _chosen(self, index: int) -> None:
        if self._updating or index < 0:
            return
        cid = self.switcher.itemData(index)
        if cid:
            self.calibrationChosen.emit(cid)
