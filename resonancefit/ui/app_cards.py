"""EQ app cards and the "Where will you listen?" picker, shared by the export
flow and the "Where can I use my EQ?" page."""

from __future__ import annotations

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QToolButton,
    QVBoxLayout, QWidget,
)

from eq_helper.dsp.convolution import SAMPLE_RATES
from eq_helper.exports.formats.base import get_format, shared_only

from .common import Card, chip, heading, hint, tr

OS_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "android": "Android", "ios": "iPhone / iPad"}
PRICE_LABELS = {"free": "Free", "paid": "Paid", "freemium": "Free, with paid extras"}
SCOPE_LABELS = {"system": "System-wide: everything you play", "player": "Only inside this player"}
FORMAT_BUTTONS = {"graphic_eq": "Export EQ file", "convolution": "Export convolution files",
                  "parametric": "Export parametric EQ"}


def os_name(code: str) -> str:
    return tr(OS_NAMES.get(code, code))


def listening_label(where: str, os_code: str | None = None) -> str:
    if where == "this_computer":
        return tr("This computer — {os}").format(os=os_name(os_code))
    if where == "phone":
        return tr("Phone or tablet — {os}").format(os=os_name(os_code))
    if where == "hardware":
        return tr("Dongle or DAP")
    return tr("Another computer — {os}").format(os=os_name(os_code))


def balance_text(balance_db: float) -> str:
    side = tr("right") if balance_db > 0 else tr("left")
    return tr("Set the app's balance {amount:.1f} dB towards the {side}.").format(amount=abs(balance_db), side=side)


# ── picker ──────────────────────────────────────────────────────────────────

class ListeningPicker(QWidget):
    chosen = Signal(str, str)  # where, os ("" for hardware)

    def __init__(self, platform: str, parent=None):
        super().__init__(parent)
        self.platform = platform
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(heading(tr("Where will you listen?"), 2))
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QRadioButton] = {}

        def row(key, text, combo=None):
            line = QHBoxLayout()
            button = QRadioButton(text)
            self.group.addButton(button)
            self.buttons[key] = button
            button.toggled.connect(lambda on: on and self._emit())
            line.addWidget(button)
            if combo is not None:
                line.addWidget(combo)
                combo.currentIndexChanged.connect(lambda _i, k=key: self.buttons[k].isChecked() and self._emit())
            line.addStretch(1)
            layout.addLayout(line)

        row("this_computer", listening_label("this_computer", platform))
        self.phone_os = QComboBox()
        for code in ("android", "ios"):
            self.phone_os.addItem(os_name(code), code)
        self.phone_os.setAccessibleName(tr("Phone system"))
        row("phone", tr("Phone or tablet"), self.phone_os)
        row("hardware", tr("Dongle or DAP"))
        self.computer_os = QComboBox()
        for code in ("windows", "macos", "linux"):
            self.computer_os.addItem(os_name(code), code)
        self.computer_os.setAccessibleName(tr("Computer system"))
        row("other_computer", tr("Another computer"), self.computer_os)

    def current(self) -> tuple[str, str]:
        where = next((k for k, b in self.buttons.items() if b.isChecked()), "this_computer")
        os_code = {"this_computer": self.platform, "phone": self.phone_os.currentData(),
                   "other_computer": self.computer_os.currentData()}.get(where, "")
        return where, os_code or ""

    def choose(self, where: str, os_code: str | None = None) -> None:
        combo = {"phone": self.phone_os, "other_computer": self.computer_os}.get(where)
        if combo is not None and os_code:
            combo.blockSignals(True)
            combo.setCurrentIndex(max(combo.findData(os_code), 0))
            combo.blockSignals(False)
        button = self.buttons[where]
        if button.isChecked():
            self._emit()
        else:
            button.setChecked(True)

    def _emit(self) -> None:
        self.chosen.emit(*self.current())


# ── cards ───────────────────────────────────────────────────────────────────

class AppCard(Card):
    exportRequested = Signal(str, int)  # app id, format index

    def __init__(self, app, installed: bool, channel_eq=None, device_rate: int | None = None,
                 can_export: bool = True, parent=None):
        super().__init__(parent=parent)
        self.app = app
        title = QHBoxLayout()
        title.addWidget(heading(app.name, 3))
        self.installed_badge = chip(tr("Installed"), "operator")
        self.installed_badge.setVisible(installed)
        title.addWidget(self.installed_badge)
        title.addStretch(1)
        self.price = chip(tr(PRICE_LABELS.get(app.price, app.price)))
        title.addWidget(self.price)
        self.body.addLayout(title)
        self.description = hint(app.description)
        self.body.addWidget(self.description)
        facts = QHBoxLayout()
        self.scope = chip(tr(SCOPE_LABELS.get(app.scope, app.scope)))
        facts.addWidget(self.scope)
        self.lr = chip(tr(app.lr_label), "yours" if app.lr == "full" else "default")
        facts.addWidget(self.lr)
        facts.addStretch(1)
        self.body.addLayout(facts)

        self.compromise = QLabel()
        self.compromise.setWordWrap(True)
        self.compromise.setObjectName("warning")
        self.compromise.hide()
        self.body.addWidget(self.compromise)
        if channel_eq is not None and app.lr == "shared":
            reduced = shared_only(channel_eq, app.balance_control)
            lines = [tr("This app can't correct each ear separately, so both ears get the average "
                        "of your left and right EQ.")]
            if reduced.balance_db is not None:
                lines.append(balance_text(reduced.balance_db))
            self.compromise.setText(" ".join(lines))
            self.compromise.show()

        self.accuracy = hint("")
        self.accuracy.hide()
        self.body.addWidget(self.accuracy)

        actions = QHBoxLayout()
        self.rate_combo = None
        self.export_buttons: list[QPushButton] = []
        if can_export:
            for index, fmt in enumerate(app.formats):
                if fmt.format == "convolution" and fmt.options.get("sample_rate") == "device" \
                        and self.rate_combo is None:
                    actions.addWidget(QLabel(tr("Sample rate")))
                    self.rate_combo = QComboBox()
                    self.rate_combo.setAccessibleName(tr("Sample rate"))
                    for rate in SAMPLE_RATES:
                        self.rate_combo.addItem(f"{rate / 1000:g} kHz", rate)
                    self.rate_combo.setCurrentIndex(max(self.rate_combo.findData(int(device_rate or 48000)), 0))
                    self.rate_combo.setToolTip(tr("Use the rate your output device runs at; "
                                                  "it's pre-filled from your current output."))
                    actions.addWidget(self.rate_combo)
                if app.advanced and fmt.format == "convolution":
                    label = tr("Convolution (all options)…")
                elif len(app.formats) == 1:
                    label = tr("Export for this app")
                else:
                    label = fmt.label or tr(FORMAT_BUTTONS.get(fmt.format, fmt.format))
                button = QPushButton(label)
                if index == 0:
                    button.setObjectName("primary")
                button.clicked.connect(lambda _c=False, i=index: self.exportRequested.emit(app.id, i))
                actions.addWidget(button)
                self.export_buttons.append(button)
        actions.addStretch(1)
        link = app.links.get("store") or app.links.get("site")
        self.download_button = None
        if link:
            self.download_button = QPushButton(tr("Download"))
            self.download_button.setToolTip(link)
            self.download_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(link)))
            actions.addWidget(self.download_button)
        self.body.addLayout(actions)

        if app.setup_steps:
            self.steps_toggle = QToolButton()
            self.steps_toggle.setText(tr("How to set it up"))
            self.steps_toggle.setCheckable(True)
            self.steps_toggle.setAutoRaise(True)
            self.steps = QLabel("\n".join(f"{n}. {step}" for n, step in enumerate(app.setup_steps, 1)))
            self.steps.setWordWrap(True)
            self.steps.hide()
            self.steps_toggle.toggled.connect(self.steps.setVisible)
            self.body.addWidget(self.steps_toggle)
            self.body.addWidget(self.steps)

    def set_accuracy(self, text: str) -> None:
        self.accuracy.setText(text)
        self.accuracy.setVisible(bool(text))

    def text_summary(self) -> str:
        labels = [self.price, self.description, self.scope, self.lr, self.compromise, self.accuracy]
        return "\n".join(label.text() for label in labels if label.text())


class AccuracyWorker(QThread):
    """Fit each parametric app's EQ once to show "within ±x dB up to y kHz"."""

    result = Signal(str, str)  # app id, text

    def __init__(self, jobs):
        super().__init__()
        self._jobs = jobs  # [(app_id, channel_eq, options)]

    def run(self):
        fmt = get_format("parametric")
        for app_id, eq, options in self._jobs:
            if self.isInterruptionRequested():
                return
            try:
                fits = fmt.fit(eq, options)
            except Exception:  # noqa: BLE001 - accuracy is informational
                continue
            parts = [peq.accuracy.describe() for peq in fits.values()]
            worst = parts[0] if len(set(parts)) == 1 else "; ".join(
                f"{side}: {peq.accuracy.describe()}" for side, peq in fits.items())
            self.result.emit(app_id, tr("Parametric accuracy: {text}").format(text=worst))
