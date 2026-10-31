"""Header: whose calibration this is, the IEM and accessories it was measured with."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .common import Card, chip, heading, hint, tr

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


class HeaderCard(Card):
    calibrationChosen = Signal(str)
    removeRequested = Signal(str)  # calibration id

    def __init__(self, branding, parent=None):
        super().__init__(parent=parent)
        top = QHBoxLayout()
        left = QVBoxLayout()
        self.iem = heading("", 1)
        self.iem.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(self.iem)
        self.customer = QLabel()
        self.customer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(self.customer)
        top.addLayout(left, 1)

        right = QVBoxLayout()
        right.setAlignment(Qt.AlignRight | Qt.AlignTop)
        brand_row = QHBoxLayout()
        brand_row.addStretch(1)
        logo = branding.logo_path
        if logo is not None and logo.is_file():
            pix = QPixmap(str(logo))
            if not pix.isNull():
                image = QLabel()
                image.setPixmap(pix.scaledToHeight(32, Qt.SmoothTransformation))
                brand_row.addWidget(image)
        self.brand = QLabel(f"<b>{branding.product_name}</b> <span>{branding.byline}</span>")
        self.brand.setTextFormat(Qt.RichText)
        brand_row.addWidget(self.brand)
        right.addLayout(brand_row)
        # calibration and target, one under the other: label, choice, its button
        self.choices = QGridLayout()
        self.choices.setHorizontalSpacing(8)
        self.choices.setColumnStretch(1, 1)
        self.switcher_label = QLabel(tr("Calibration"))
        self.choices.addWidget(self.switcher_label, 0, 0, Qt.AlignRight)
        self.switcher = QComboBox()
        self.switcher.setAccessibleName(tr("Choose calibration"))
        self.switcher.setMinimumContentsLength(28)
        self.switcher_label.setBuddy(self.switcher)
        self.switcher.currentIndexChanged.connect(self._chosen)
        self.choices.addWidget(self.switcher, 0, 1)
        self.remove_button = QPushButton(tr("Remove…"))
        self.remove_button.setToolTip(tr("Remove this calibration from the app. Its file stays where it is."))
        self.remove_button.clicked.connect(self._remove)
        self.choices.addWidget(self.remove_button, 0, 2)
        choices_box = QWidget()
        choices_box.setLayout(self.choices)
        self.choices.setContentsMargins(0, 0, 0, 0)
        right.addWidget(choices_box)
        top.addLayout(right)
        self.body.addLayout(top)

        self.accessory_row = QHBoxLayout()
        self.accessory_row.setSpacing(6)
        self.body.addLayout(self.accessory_row)
        self.details = hint("")
        self.body.addWidget(self.details)
        self._updating = False
        self._chips: list[QLabel] = []

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

    def add_choice(self, label: QLabel, widget: QWidget, button: QWidget | None = None) -> None:
        """Another row under the calibration (the target), in the same columns."""
        row = self.choices.rowCount()
        self.choices.addWidget(label, row, 0, Qt.AlignRight)
        self.choices.addWidget(widget, row, 1)
        if button is not None:
            self.choices.addWidget(button, row, 2)

    def set_manifest(self, manifest) -> None:
        self.iem.setText(manifest.iem.display_name)
        serial = f", {tr('serial')} {manifest.iem.serial}" if manifest.iem.serial else ""
        self.customer.setText(tr("Calibrated for {name} on {date}").format(
            name=manifest.customer.name, date=manifest.created) + serial)
        for widget in self._chips:
            self.accessory_row.removeWidget(widget)
            widget.deleteLater()
        self._chips = []
        for acc in manifest.accessories:
            kind = "yours" if acc.supplied_by == "customer" else "operator"
            label = chip(f"{accessory_label(acc.kind)}: {acc.description} ({supplied_label(acc, manifest)})", kind)
            label.setToolTip(tr("Use the same accessories when you listen: the calibration was measured with them."))
            self.accessory_row.addWidget(label)
            self._chips.append(label)
        stretch = QLabel()
        self.accessory_row.addWidget(stretch, 1)
        self._chips.append(stretch)
        extra = []
        if manifest.coupler:
            extra.append(tr("Measured on: {coupler}").format(coupler=manifest.coupler))
        if manifest.notes:
            extra.append(manifest.notes)
        self.details.setText("\n".join(extra))
        self.details.setVisible(bool(extra))

    def text_summary(self) -> str:
        """Everything the header shows, as plain text (tests, screen readers)."""
        parts = [self.iem.text(), self.customer.text(), self.details.text()]
        parts += [c.text() for c in self._chips]
        return "\n".join(p for p in parts if p)

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
