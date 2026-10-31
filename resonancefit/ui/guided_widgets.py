"""Painted pieces of the guided test: the stage, transport bar, option cards, answer buttons, progress.

Everything is drawn with QPainter from :func:`stage_colors`, which reads the
app's Studio tokens (:func:`~.common.theme_colors`, dark or light), so a
theme change only needs a repaint.

The card's "playing" animation is deliberately blind: :func:`visualizer_shape`
depends only on the overall output level and the time, never on the option,
so every option looks alike at equal loudness and the choice is made by ear.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from .common import theme_colors

GRID_STEP = 120
CARD_MARGIN = 10          # room around a card for its glow; two margins make the gap between cards
CARD_HEIGHT = 146
CARD_RADIUS = 16
GLOW = 10
BARS = 56
SELECT_MS = 200


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.Window).lightness() < 128


def stage_colors(palette: QPalette) -> dict[str, QColor]:
    """The stage's colours from the app's tokens: the page colour behind, cards one step up, amber for the
    card you've chosen."""
    c = theme_colors(palette)
    dark = c["dark"]
    ink = QColor(c["text"])

    def wash(alpha: int) -> QColor:
        return QColor(ink.red(), ink.green(), ink.blue(), alpha)

    return {"bg": QColor(c["bg"]), "grid": wash(10 if dark else 14), "card_top": QColor(c["raised"]),
            "card_bottom": QColor(c["surface"]), "border": QColor(c["line"]), "text": ink,
            "muted": QColor(c["hint"]), "idle": wash(40), "track": wash(30 if dark else 24),
            "fill": wash(140 if dark else 120), "inner": QColor(0, 0, 0, 70 if dark else 10),
            "focus": QColor(c["focus"]), "accent": QColor(c["accent"]), "warning": QColor(c["warning"]),
            "glow": 1.0 if dark else 0.45}


def accent_colors(palette: QPalette, count: int) -> list[QColor]:
    """Every card shares the one accent: amber marks the card you've chosen, nothing tells them apart."""
    return [QColor(theme_colors(palette)["accent"]) for _ in range(count)]


def visualizer_shape(level: float, t: float) -> tuple[np.ndarray, np.ndarray]:
    """(bar heights, line heights), each ``BARS`` values in 0…1, for a card that's playing.

    Only the overall ``level`` (0…1) and the time ``t`` (seconds) go in: a
    fixed falling profile with a slow drift, the same for every option.
    """
    level = float(min(max(level, 0.0), 1.0))
    x = np.linspace(0.0, 1.0, BARS)
    profile = 0.25 + 0.75 * np.exp(-2.4 * x)
    drift = (0.16 * np.sin(2 * np.pi * (3.1 * x + 0.35 * t)) + 0.10 * np.sin(2 * np.pi * (7.3 * x - 0.6 * t))
             + 0.07 * np.sin(2 * np.pi * (13.7 * x + 1.3 * t)))
    bars = np.clip(level * profile * (1.0 + drift), 0.0, 1.0)
    line = np.clip(level * (0.55 + 0.08 * np.sin(2 * np.pi * (1.4 * x + 0.12 * t))
                            + 0.05 * np.sin(2 * np.pi * (2.9 * x - 0.21 * t))), 0.0, 1.0)
    return bars, line


def _with_alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _focus_ring(painter: QPainter, rect: QRectF, radius: float, color: QColor) -> None:
    pen = QPen(_with_alpha(color, 0.7), 1.5)
    pen.setDashPattern([3.0, 2.0])
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), radius + 3, radius + 3)


class _SpaceIgnoring(QAbstractButton):
    """Space never clicks these: it stops all sound (the key reaches the view / window)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Space:
            event.ignore()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Space:
            event.ignore()
            return
        super().keyReleaseEvent(event)


class StageBackground(QWidget):
    """Near-black (or light grey) stage with a very faint grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _event) -> None:
        colors = stage_colors(self.palette())
        painter = QPainter(self)
        painter.fillRect(self.rect(), colors["bg"])
        painter.setPen(QPen(colors["grid"], 1))
        w, h = self.width(), self.height()
        x0 = (w // 2) % GRID_STEP
        for x in range(x0, w, GRID_STEP):
            painter.drawLine(x, 0, x, h)
        for y in range(GRID_STEP // 2, h, GRID_STEP):
            painter.drawLine(0, y, w, y)


class TransportBar(_SpaceIgnoring):
    """The slim Play / Pause bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playing = False
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def set_playing(self, playing: bool, text: str) -> None:
        self.playing = playing
        self.setText(text)
        self.setAccessibleName(text)
        self.update()

    def paintEvent(self, _event) -> None:
        colors = stage_colors(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(colors["border"], 1))
        painter.setBrush(_with_alpha(colors["card_bottom"], 0.9 if self.underMouse() else 0.6))
        painter.drawRoundedRect(rect, 8, 8)
        color = colors["text"] if self.isEnabled() else colors["muted"]
        font = self.font()
        metrics = self.fontMetrics()
        text_w = metrics.horizontalAdvance(self.text())
        icon = 10.0
        total = icon + 8 + text_w
        x = rect.center().x() - total / 2
        cy = rect.center().y()
        painter.setPen(QPen(color, 1.2))
        if self.playing:
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(x + 1, cy - 5, 2.6, 10))
            painter.drawRect(QRectF(x + 6, cy - 5, 2.6, 10))
        else:
            path = QPainterPath(QPointF(x + 1, cy - 5.5))
            path.lineTo(x + icon, cy)
            path.lineTo(x + 1, cy + 5.5)
            path.closeSubpath()
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(x + icon + 8, rect.top(), text_w + 2, rect.height()), Qt.AlignVCenter, self.text())
        if self.hasFocus() and self.property("kbfocus"):  # keyboard focus only
            _focus_ring(painter, rect.adjusted(3, 3, -3, -3), 6, colors["focus"])


class OptionCard(_SpaceIgnoring):
    """One option ("Play A"). Selected + playing: accent border, glow, tint and the blind visualizer."""

    def __init__(self, letter: str, label: str, parent=None):
        super().__init__(parent)
        self.letter = letter
        self.setText(label)
        self.setAccessibleName(label)
        self.accent = stage_colors(self.palette())["accent"]
        self.selected = False
        self.playing = False
        self.heard = False
        self.ruled_out = False
        self.position: float | None = None  # where the card sits in its row, low (0) to high (1)
        self.shape: tuple[np.ndarray, np.ndarray] | None = None
        self._emphasis = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SELECT_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._emphasis_changed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(96, CARD_HEIGHT + 2 * CARD_MARGIN)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(220, CARD_HEIGHT + 2 * CARD_MARGIN)

    def _emphasis_changed(self, value) -> None:
        self._emphasis = float(value)
        self.update()

    def set_accent(self, color: QColor) -> None:
        self.accent = QColor(color)
        self.update()

    def set_position(self, position: float | None) -> None:
        """Shown as a dot on a short track at the bottom: the cards' order (low to high) at a glance."""
        self.position = position
        self.update()

    def set_selected(self, selected: bool, animate: bool = True) -> None:
        if selected == self.selected:
            return
        self.selected = selected
        target = 1.0 if selected else 0.0
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._emphasis)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._emphasis = target
            self.update()

    def set_playing(self, playing: bool) -> None:
        self.playing = playing
        self.update()

    def set_heard(self, heard: bool) -> None:
        self.heard = heard
        self.update()

    def set_ruled_out(self, ruled_out: bool) -> None:
        """Crossed out by the listener: drawn faded, with a ✕ (still playable)."""
        self.ruled_out = ruled_out
        self.update()

    def set_level(self, level: float, t: float) -> None:
        """Animate from the overall level only (see :func:`visualizer_shape`)."""
        self.shape = visualizer_shape(level, t)
        self.update()

    def clear_shape(self) -> None:
        self.shape = None
        self.update()

    def card_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN)

    def paintEvent(self, _event) -> None:
        colors = stage_colors(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.card_rect()
        e = self._emphasis
        live = self.selected and self.playing
        if self.ruled_out:
            painter.setOpacity(0.4)

        glow = e * (1.0 if live else 0.3) * colors["glow"]
        if glow > 0.01:
            painter.setBrush(Qt.NoBrush)
            for i in range(GLOW, 0, -1):
                fade = (1.0 - i / (GLOW + 1)) ** 2
                painter.setPen(QPen(_with_alpha(self.accent, 0.22 * fade * glow), 2.0))
                painter.drawRoundedRect(rect.adjusted(-i, -i, i, i), CARD_RADIUS + i, CARD_RADIUS + i)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, colors["card_top"])
        gradient.setColorAt(1.0, colors["card_bottom"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        if e > 0.01:
            tint = QLinearGradient(rect.bottomLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.35))
            tint.setColorAt(0.0, _with_alpha(self.accent, 0.20 * e * (1.0 if live else 0.7)))
            tint.setColorAt(1.0, _with_alpha(self.accent, 0.0))
            painter.setBrush(tint)
            painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

        border = QColor(colors["border"])
        if e > 0.01:
            a, b = border, self.accent
            border = QColor(round(a.red() + (b.red() - a.red()) * e), round(a.green() + (b.green() - a.green()) * e),
                            round(a.blue() + (b.blue() - a.blue()) * e), round(a.alpha() + (230 - a.alpha()) * e))
        painter.setPen(QPen(border, 1.0 + 0.5 * e))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), CARD_RADIUS, CARD_RADIUS)

        painter.setPen(self.accent if e > 0.5 else colors["text"] if self.isEnabled() else colors["muted"])
        font = self.font()
        if len(self.text()) <= 2:  # a card number, which is also its key: big enough to read at a glance
            font.setPointSizeF(font.pointSizeF() * 1.8)
            font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(16, 14, -16, -14), Qt.AlignLeft | Qt.AlignTop, self.text())
        if self.heard or self.ruled_out:
            painter.setPen(colors["muted"])
            painter.setFont(self.font())
            painter.drawText(rect.adjusted(16, 14, -16, -14), Qt.AlignRight | Qt.AlignTop,
                             "✕" if self.ruled_out else "✓")

        if self.position is not None:
            track_y = rect.bottom() - 14
            painter.setPen(QPen(colors["idle"], 2.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(rect.left() + 16, track_y), QPointF(rect.right() - 16, track_y))
            x = rect.left() + 16 + self.position * (rect.width() - 32)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.accent if e > 0.5 else colors["muted"])
            painter.drawEllipse(QPointF(x, track_y), 3.5, 3.5)

        area = rect.adjusted(16, rect.height() * 0.38, -16, -24)
        baseline = area.top() + area.height() * 0.62
        if self.shape is None or e < 0.01:
            painter.setPen(QPen(colors["idle"], 1.0))
            painter.drawLine(QPointF(area.left(), baseline), QPointF(area.right(), baseline))
        else:
            self._paint_visualizer(painter, area, baseline, e * (1.0 if live else 0.55))

        if self.hasFocus() and self.property("kbfocus"):  # keyboard focus only
            _focus_ring(painter, rect, CARD_RADIUS, colors["focus"])

    def _paint_visualizer(self, painter: QPainter, area: QRectF, baseline: float, strength: float) -> None:
        bars, line = self.shape
        height = baseline - area.top()
        step = area.width() / len(bars)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_with_alpha(self.accent, 0.75 * strength))
        for i, value in enumerate(bars):
            h = max(1.0, value * height)
            painter.drawRect(QRectF(area.left() + i * step, baseline - h, max(1.0, step * 0.55), h))
        path = QPainterPath()
        for i, value in enumerate(line):
            point = QPointF(area.left() + (i + 0.3) * step, baseline - value * height)
            if i == 0:
                path.moveTo(point)
            else:
                prev = path.currentPosition()
                mid = (prev.x() + point.x()) / 2
                path.cubicTo(QPointF(mid, prev.y()), QPointF(mid, point.y()), point)
        painter.setPen(QPen(_with_alpha(self.accent, min(1.0, 1.1 * strength)), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class AnswerButton(_SpaceIgnoring):
    """The wide answer ("Louder: A") takes the selected card's accent; the narrow one stays neutral."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.accent: QColor | None = None
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self.setAccessibleName(text)
        self.update()

    def set_accent(self, color: QColor | None) -> None:
        self.accent = None if color is None else QColor(color)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 40, 48)

    def minimumSizeHint(self) -> QSize:
        return QSize(min(self.sizeHint().width(), 140), 48)

    def paintEvent(self, _event) -> None:
        colors = stage_colors(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.45)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        accent = self.accent if (self.accent is not None and self.isEnabled()) else None
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, colors["card_top"])
        gradient.setColorAt(1.0, colors["card_bottom"])
        painter.setPen(QPen(_with_alpha(accent, 0.85) if accent is not None else colors["border"], 1.0))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 12, 12)
        inner = rect.adjusted(8, 6, -8, -6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_with_alpha(accent, 0.12) if accent is not None else colors["inner"])
        if self.isDown() or self.underMouse():
            painter.setBrush(_with_alpha(accent if accent is not None else colors["text"], 0.18))
        painter.drawRoundedRect(inner, 8, 8)
        painter.setPen(colors["text"])
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignCenter, self.text())
        if self.hasFocus() and self.property("kbfocus"):  # keyboard focus only
            painter.setOpacity(1.0)
            _focus_ring(painter, rect.adjusted(3, 3, -3, -3), 9, colors["focus"])


class ProgressLine(QWidget):
    """Thin, fully rounded overall progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SELECT_MS * 2)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._shown_changed)
        self._shown = 0.0

    def set_value(self, value: float) -> None:
        self.value = float(min(max(value, 0.0), 1.0))
        self.setAccessibleDescription(f"{round(self.value * 100)} %")
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(self.value)
        self._anim.start()

    def _shown_changed(self, value) -> None:
        self._shown = float(value)
        self.update()

    def paintEvent(self, _event) -> None:
        colors = stage_colors(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(colors["track"])
        painter.drawRoundedRect(rect, radius, radius)
        if self._shown > 0:
            done = QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * self._shown), rect.height())
            painter.setBrush(colors["fill"])
            painter.drawRoundedRect(done, radius, radius)


def level_from_samples(samples, volume: float) -> float:
    """0…1 loudness of recent output, relative to the volume setting (a full tone ≈ 1)."""
    if samples is None or len(samples) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 0.0:
        return 0.0
    reference = max(volume, 1e-6) / math.sqrt(2.0)
    return float(min(max(1.0 + 20.0 * math.log10(rms / reference) / 30.0, 0.0), 1.0))

