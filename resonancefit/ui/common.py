"""Small shared pieces for the customer app's screens.

Every user-visible string goes through :func:`tr` so it can be translated
later. Every colour comes from :data:`STUDIO` (light and dark), through
:func:`theme_colors`, :func:`brand_palette` and :func:`build_style`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

CONTEXT = "CustomerApp"


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


class Segments(QFrame):
    """Mutually exclusive choices drawn as one switch, the chosen one filled in the accent. Its API follows
    QTabBar's (``addTab``, ``tabData``, ``currentIndex``, ``currentChanged``…) so it can stand in for one."""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("segments")
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(2, 2, 2, 2)
        self._row.setSpacing(0)
        self._group = QButtonGroup(self)
        self._buttons: list[QPushButton] = []
        self._data: list = []
        self._current = -1

    def addTab(self, text: str) -> int:  # noqa: N802 - QTabBar's name
        button = QPushButton(text)
        button.setCheckable(True)
        self._group.addButton(button)
        self._row.addWidget(button)
        self._buttons.append(button)
        self._data.append(None)
        index = len(self._buttons) - 1
        button.clicked.connect(lambda _=False, b=button: self.setCurrentIndex(self._buttons.index(b)))
        if self._current < 0:
            self.setCurrentIndex(index)
        return index

    def removeTab(self, index: int) -> None:  # noqa: N802
        button = self._buttons.pop(index)
        self._data.pop(index)
        self._group.removeButton(button)
        button.deleteLater()
        if not self._buttons:
            self._current = -1
            self.currentChanged.emit(-1)
        elif index <= self._current:
            self._current = -1
            self.setCurrentIndex(max(0, index - 1))

    def count(self) -> int:
        return len(self._buttons)

    def button(self, index: int) -> QPushButton:
        return self._buttons[index]

    def tabText(self, index: int) -> str:  # noqa: N802
        return self._buttons[index].text()

    def setTabData(self, index: int, data) -> None:  # noqa: N802
        self._data[index] = data

    def tabData(self, index: int):  # noqa: N802
        return self._data[index] if 0 <= index < len(self._data) else None

    def setTabToolTip(self, index: int, tip: str) -> None:  # noqa: N802
        self._buttons[index].setToolTip(tip)

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._buttons):
            return
        self._buttons[index].setChecked(True)
        if index != self._current:
            self._current = index
            self.currentChanged.emit(index)


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


def _readable(candidates, background: str, ratio: float = 4.5) -> str:
    """First colour with at least ``ratio`` contrast (WCAG AA: 4.5 text, 3 controls) on ``background``."""
    for color in candidates:
        if contrast_ratio(color, background) >= ratio:
            return color
    return candidates[-1]


# "Studio gear": a near-black instrument, where amber means *your ear* (the resonance, the active ear,
# the chosen card); the target is cyan, the corrected sound green, everything else neutral. Surfaces step
# up in fill (bg → surface → raised → control) instead of drawing borders. Every pair used for text is at
# least 4.5:1 and every control edge or graphic at least 3:1 (tests/rf/test_first_launch.py checks).
STUDIO = {
    True: {  # dark
        "bg": "#0E0F11", "surface": "#16181B", "raised": "#1E2125", "control": "#262A30", "hover": "#30353C",
        "line": "#2A2E33", "border": "#727982", "text": "#E8E6E3", "hint": "#A0A6AD", "disabled": "#80868D",
        "accent": "#F2A93B", "on_accent": "#1A1206", "target": "#4FB3D9", "corrected": "#3CC79A",
        "upper": "#E58FC0", "warning": "#FF9466", "focus": "#E8E6E3"},
    False: {  # light
        "bg": "#ECEAE5", "surface": "#F7F6F3", "raised": "#FFFFFF", "control": "#E4E1DA", "hover": "#D9D5CD",
        "line": "#DCD8D1", "border": "#7A776F", "text": "#1B1C1E", "hint": "#53575D", "disabled": "#6B6E74",
        "accent": "#8A4D00", "on_accent": "#FFFFFF", "target": "#00689E", "corrected": "#00795A",
        "upper": "#A8306E", "warning": "#A83A12", "focus": "#1B1C1E"},
}


def theme_colors(palette: QPalette) -> dict:
    """The Studio tokens for the palette's light or dark scheme (picked by its window/base lightness),
    plus the names older code reads (``base``, ``primary``…)."""
    dark = palette.color(QPalette.Window).lightness() < 128
    c = dict(STUDIO[dark])
    c.update(dark=dark, base=c["surface"], primary=c["accent"], primary_text_on_primary=c["on_accent"])
    return c


def curve_colors(palette: QPalette) -> dict[str, str]:
    """The graph's colours: your IEM and its resonance in amber, the target cyan, the EQ'd sound green."""
    c = theme_colors(palette)
    return {"iem": c["accent"], "marker": c["accent"], "target": c["target"], "with_eq": c["corrected"],
            "upper": c["upper"]}


def brand_palette(palette: QPalette) -> QPalette:
    """The whole Qt palette from the Studio tokens: native widgets, selections, sliders and links use them
    instead of the system's colours (whose accent can be anything, e.g. a bright red)."""
    c = theme_colors(palette)
    branded = QPalette(palette)
    roles = ((QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "raised"),
             (QPalette.Button, "control"), (QPalette.ButtonText, "text"), (QPalette.Text, "text"),
             (QPalette.WindowText, "text"), (QPalette.PlaceholderText, "hint"), (QPalette.ToolTipBase, "raised"),
             (QPalette.ToolTipText, "text"), (QPalette.Highlight, "accent"), (QPalette.Accent, "accent"),
             (QPalette.HighlightedText, "on_accent"), (QPalette.Link, "accent"), (QPalette.Light, "hover"),
             (QPalette.Midlight, "control"), (QPalette.Mid, "line"), (QPalette.Dark, "bg"), (QPalette.Shadow, "bg"))
    for group in (QPalette.Active, QPalette.Inactive):
        for role, key in roles:
            branded.setColor(group, role, QColor(c[key]))
    for role, key in ((QPalette.Text, "disabled"), (QPalette.WindowText, "disabled"),
                      (QPalette.ButtonText, "disabled"), (QPalette.Button, "control"), (QPalette.Base, "surface"),
                      (QPalette.Window, "bg"), (QPalette.Highlight, "border")):
        branded.setColor(QPalette.Disabled, role, QColor(c[key]))
    return branded


def is_branded(palette: QPalette) -> bool:
    c = theme_colors(palette)
    return (palette.color(QPalette.Highlight).name() == c["accent"].lower()
            and palette.color(QPalette.Window).name() == c["bg"].lower())


ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "resonancefit" / "assets"
CHEVRON = {dark: (ASSETS / f"chevron-{'dark' if dark else 'light'}.svg").as_posix() for dark in (True, False)}
THEMES = {"system": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}


def apply_theme(name: str | None) -> None:
    """Light, dark, or follow the system (``"system"``, the default), in the Studio colours."""
    app = QApplication.instance()
    if app is None:
        return
    app.styleHints().setColorScheme(THEMES.get(name or "system", Qt.ColorScheme.Unknown))
    app.setPalette(QPalette())  # back to the style's palette for the scheme: ours would hide the change
    app.setPalette(brand_palette(app.palette()))


class KeyboardFocus(QObject):
    """Shows focus only to keyboard users: a widget that gets focus by Tab (or Backtab) has the property
    ``kbfocus`` until it loses it, and the stylesheet draws a neutral ring for it. Mouse clicks show none."""

    REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        kind = event.type()
        if kind in (QEvent.FocusIn, QEvent.FocusOut) and isinstance(obj, QWidget):
            on = kind == QEvent.FocusIn and event.reason() in self.REASONS
            if bool(obj.property("kbfocus")) != on:
                obj.setProperty("kbfocus", on)
                obj.style().unpolish(obj)
                obj.style().polish(obj)
                obj.update()
        return False


def install_keyboard_focus(app) -> KeyboardFocus:
    watcher = KeyboardFocus(app)
    app.installEventFilter(watcher)
    return watcher


def build_style(palette: QPalette) -> str:
    c = theme_colors(palette)
    accent, surface = QColor(c["accent"]), QColor(c["surface"])
    tint = _mix(accent, surface, 0.84)          # a selected row: the accent, faintly
    notice = _mix(QColor(c["warning"]), surface, 0.88)
    return f"""
QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px 6px; }}
QFrame#card {{ background: {c['surface']}; border: none; border-radius: 12px; }}
QFrame#bar {{ background: {c['surface']}; border: none; border-radius: 12px; }}
QLabel#hint {{ color: {c['hint']}; }}
QLabel#warning {{ color: {c['warning']}; }}
QLabel#caption {{ color: {c['hint']}; font-size: 9pt; }}
QLabel#title {{ font-size: 12pt; font-weight: 600; }}
QLabel#iem {{ font-size: 13pt; font-weight: 600; }}
QLabel#bigReadout {{ color: {c['accent']}; font-weight: 600; }}
QLabel#otherReadout {{ color: {c['text']}; font-weight: 600; }}
QFrame#notice {{ border: 1px solid {c['warning']}; border-radius: 10px; background: {notice}; }}
QFrame#notice[kind="ok"] {{ border-color: {c['border']}; background: {c['surface']}; }}
QFrame#notice QLabel {{ background: transparent; }}
QLabel#noticeTitle {{ font-weight: 600; }}
QLabel#chip {{ background: {c['control']}; border-radius: 9px; padding: 2px 9px; }}
QLabel#emptyTitle {{ font-size: 13pt; font-weight: 600; }}

QPushButton, QToolButton {{ background: {c['control']}; color: {c['text']}; border: 2px solid transparent;
    border-radius: 8px; padding: 4px 12px; min-height: 20px; }}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {c['line']}; }}
QPushButton:checked {{ background: {tint}; color: {c['accent']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['disabled']}; background: transparent;
    border: 2px solid {c['control']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['on_accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {_mix(accent, QColor(c['text']), 0.15)}; }}
QPushButton#primary:checked {{ background: {_mix(accent, QColor(c['bg']), 0.25)}; color: {c['on_accent']}; }}
QPushButton#primary:disabled {{ background: transparent; color: {c['disabled']}; border: 2px solid {c['control']}; }}
QPushButton[large="true"] {{ font-size: 11pt; font-weight: 600; padding: 12px 24px; border-radius: 10px; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QFrame#segments {{ background: {c['control']}; border-radius: 9px; }}
QFrame#segments QPushButton {{ background: transparent; color: {c['hint']}; border-radius: 7px; padding: 3px 12px; }}
QFrame#segments QPushButton:hover {{ color: {c['text']}; }}
QFrame#segments QPushButton:checked {{ background: {c['accent']}; color: {c['on_accent']}; font-weight: 600; }}
QFrame#segments QPushButton:disabled {{ color: {c['disabled']}; }}

QComboBox, QLineEdit, QSpinBox {{ background: {c['control']}; color: {c['text']}; border: 2px solid transparent;
    border-radius: 8px; padding: 3px 8px; min-height: 20px; selection-background-color: {c['accent']};
    selection-color: {c['on_accent']}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ background: {c['hover']}; }}
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{ color: {c['disabled']}; background: transparent;
    border: 2px solid {c['control']}; }}
QLineEdit:focus {{ border-color: {c['border']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({CHEVRON[c['dark']]}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']};
    selection-background-color: {c['control']}; selection-color: {c['text']}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QMenu {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['control']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['control']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {c['border']}; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: {c['text']}; }}
QSlider::handle:horizontal:disabled {{ background: {c['border']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {c['hint']}; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c['control']}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {c['border']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget#tunings::item {{ border-radius: 8px; margin: 1px 0; color: {c['text']}; }}
QListWidget#tunings::item:hover {{ background: {c['raised']}; }}
QListWidget#tunings::item:selected {{ background: {tint}; color: {c['text']}; }}

*[kbfocus="true"] {{ border: 2px solid {c['focus']}; }}
QListWidget[kbfocus="true"], QTabBar[kbfocus="true"] {{ border: 2px solid {c['focus']}; border-radius: 8px; }}
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
