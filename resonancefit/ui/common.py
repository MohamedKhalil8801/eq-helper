"""Small shared pieces for the customer app's screens.

Every user-visible string goes through :func:`tr` so it can be translated
later. Colours come from the palette (light and dark both work); the few
accent colours used for curves are chosen to stay readable on either.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

CONTEXT = "CustomerApp"

# Curve colours: "Your IEM" / "Target" / "With your EQ" (colour-blind safe trio).
COLOR_IEM = "#E69F00"
COLOR_TARGET = "#0072B2"
COLOR_WITH_EQ = "#009E73"
COLOR_MARKER = "#D55E00"
COLOR_UPPER = "#CC79A7"

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


def _luminance(color: str) -> float:
    c = QColor(color)
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(a: QColor, b: QColor, t: float) -> str:
    return QColor(round(a.red() * (1 - t) + b.red() * t), round(a.green() * (1 - t) + b.green() * t),
                  round(a.blue() * (1 - t) + b.blue() * t)).name()


def _readable(candidates, background: str) -> str:
    """First colour with at least 4.5:1 contrast (WCAG AA) on ``background``."""
    for color in candidates:
        if contrast_ratio(color, background) >= 4.5:
            return color
    return candidates[-1]


def theme_colors(palette: QPalette) -> dict[str, str]:
    """Colour tokens for the current light or dark palette, all AA-readable."""
    base, text = palette.color(QPalette.Base), palette.color(QPalette.Text)
    dark = base.lightness() < 128
    bg = base.name()
    hint = _readable([_mix(text, base, t) for t in (0.38, 0.3, 0.22, 0.12, 0.0)], bg)
    warning = _readable(["#FFB366", "#FFC98F"] if dark else ["#A64B00", "#8A3E00"], bg)
    primary = "#3A9AD9" if dark else "#0072B2"
    on_primary = _readable(["#FFFFFF", "#000000"], primary)
    return {"base": bg, "text": text.name(), "hint": hint, "warning": warning, "primary": primary,
            "primary_text_on_primary": on_primary, "border": _mix(text, base, 0.78), "dark": dark}


def build_style(palette: QPalette) -> str:
    c = theme_colors(palette)
    return f"""
QFrame#card {{ border: 1px solid {c['border']}; border-radius: 10px; background: {c['base']}; }}
QLabel#hint {{ color: {c['hint']}; }}
QLabel#warning {{ color: {c['warning']}; }}
QLabel#chip {{ border: 1px solid {c['border']}; border-radius: 9px; padding: 1px 8px; }}
QLabel#chip[kind="yours"] {{ border-color: {COLOR_TARGET}; }}
QLabel#chip[kind="operator"] {{ border-color: {COLOR_WITH_EQ}; }}
QLabel#chip[kind="default"] {{ border-color: {COLOR_IEM}; }}
QLabel#bigReadout {{ color: {c['text']}; }}
QPushButton#primary {{ background: {c['primary']}; color: {c['primary_text_on_primary']}; font-weight: bold;
    border: 1px solid {c['primary']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton#primary:checked {{ background: {_mix(QColor(c['primary']), QColor('#000000'), 0.25)}; }}
QPushButton#primary:disabled {{ background: {c['border']}; border-color: {c['border']}; }}
QPushButton[large="true"] {{ font-size: 11pt; font-weight: bold; padding: 12px 24px; border-radius: 8px;
    border: 1px solid {c['border']}; background: {c['base']}; color: {c['text']}; }}
QPushButton[large="true"]:hover {{ border-color: {c['primary']}; }}
QPushButton[large="true"]:disabled {{ color: {c['hint']}; }}
QPushButton#primary[large="true"] {{ background: {c['primary']}; color: {c['primary_text_on_primary']};
    border-color: {c['primary']}; }}
QLabel#emptyTitle {{ font-size: 13pt; font-weight: bold; }}
QPushButton:focus, QToolButton:focus, QComboBox:focus, QSlider:focus, QSpinBox:focus,
QLineEdit:focus, QListWidget:focus, QRadioButton:focus, QCheckBox:focus, QTabBar:focus {{
    outline: 2px solid {c['primary']}; }}
QPushButton:focus {{ border: 2px solid {c['primary']}; }}
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


class MainThreadGC(QObject):
    """Run Python's garbage collector only on the GUI thread.

    If a collection happens to start in a worker thread, it can free Qt
    widgets there and crash. Automatic collection is turned off and this
    timer collects on the GUI thread instead, by generation as needed.
    """

    def __init__(self, interval_ms: int = 1000, parent: QObject | None = None):
        super().__init__(parent)
        import gc
        from PySide6.QtCore import QTimer
        self._gc = gc
        self.threshold = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)

    def check(self) -> None:
        l0, l1, l2 = self._gc.get_count()
        if l0 > self.threshold[0]:
            self._gc.collect(0)
            if l1 > self.threshold[1]:
                self._gc.collect(1)
                if l2 > self.threshold[2]:
                    self._gc.collect(2)
