"""Small shared pieces for the customer app's screens.

Every user-visible string goes through :func:`tr` so it can be translated
later. Colours come from the palette (light and dark both work); the few
accent colours used for curves are chosen to stay readable on either.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

CONTEXT = "CustomerApp"

# Curve colours: "Your IEM" / "Target" / "With your EQ" (colour-blind safe trio).
COLOR_IEM = "#E69F00"
COLOR_TARGET = "#0072B2"
COLOR_WITH_EQ = "#009E73"
COLOR_MARKER = "#D55E00"

SIDE_NAMES = {"L": "Left", "R": "Right", "M": "Both ears"}


def tr(text: str) -> str:
    return QCoreApplication.translate(CONTEXT, text)


def side_name(side: str) -> str:
    return tr(SIDE_NAMES.get(side, side))


class Card(QFrame):
    """A rounded, softly bordered panel with an optional heading."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        self.title = None
        if title:
            self.title = heading(title)
            self.body.addWidget(self.title)


def heading(text: str, level: int = 2) -> QLabel:
    label = QLabel(text)
    label.setObjectName("h%d" % level)
    label.setWordWrap(True)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * {1: 1.6, 2: 1.25, 3: 1.05}.get(level, 1.0))
    font.setBold(True)
    label.setFont(font)
    label.setAccessibleName(text)
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    return label


def chip(text: str, kind: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("chip")
    label.setProperty("kind", kind)
    return label


APP_STYLE = """
QFrame#card { border: 1px solid palette(mid); border-radius: 10px; background: palette(base); }
QLabel#hint { color: palette(placeholder-text); }
QLabel#chip { border: 1px solid palette(mid); border-radius: 9px; padding: 1px 8px; }
QLabel#chip[kind="yours"] { border-color: #0072B2; }
QLabel#chip[kind="operator"] { border-color: #009E73; }
QLabel#chip[kind="default"] { border-color: #E69F00; }
QLabel#warning { color: #B35900; }
QPushButton#primary { font-weight: bold; padding: 6px 14px; }
"""


class Worker(QThread):
    """Run ``fn()`` off the GUI thread; emits ``finished_ok(result)`` or ``failed(message)``."""

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent: QObject | None = None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - shown to the customer in plain words
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit(result)
