"""Draw the app icon (a flat response rising into one resonance peak) and
write icon.png / icon.ico / icon.icns into resonancefit/assets.

    python packaging/make_icons.py
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "resonancefit" / "assets"
SIZE = 1024


def draw(size: int = SIZE) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    margin = size * 0.06
    box = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0072B2"))
    p.drawRoundedRect(box, size * 0.2, size * 0.2)
    # baseline response with one narrow resonance peak (log-frequency feel)
    path = QPainterPath()
    left, right = box.left() + size * 0.13, box.right() - size * 0.13
    base, top = box.top() + box.height() * 0.70, box.top() + box.height() * 0.2
    steps = 240
    for i in range(steps + 1):
        x = i / steps
        peak = math.exp(-0.5 * ((x - 0.66) / 0.055) ** 2)
        tilt = 0.06 * math.sin(x * math.pi * 1.2)
        y = base - (top - base) * -(peak * 0.95 + tilt)
        point = QPointF(left + x * (right - left), y)
        path.lineTo(point) if i else path.moveTo(point)
    pen = QPen(QColor("#FFFFFF"), size * 0.055)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    # the marker the customer drags
    marker = QPen(QColor("#E69F00"), size * 0.03)
    marker.setCapStyle(Qt.RoundCap)
    p.setPen(marker)
    mx = left + 0.66 * (right - left)
    p.drawLine(QPointF(mx, box.top() + box.height() * 0.12), QPointF(mx, box.bottom() - box.height() * 0.12))
    p.setPen(pen)
    p.drawPath(path)
    p.end()
    return img


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    ASSETS.mkdir(parents=True, exist_ok=True)
    big = ASSETS / "icon-1024.png"
    draw().save(str(big))
    image = Image.open(big).convert("RGBA")
    image.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon.png")
    image.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.save(ASSETS / "icon.icns")
    big.unlink()
    print("wrote", *(p.name for p in sorted(ASSETS.glob("icon.*"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
