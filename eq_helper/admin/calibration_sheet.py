"""The calibration sheet: one A4 page summarising a calibration for the customer.

Branding, customer, IEM, accessories (whose they are), coupler, date, every
fit plotted per side, and the measured primary-resonance range per side.
Drawn directly with QPainter onto a QPdfWriter, so text stays selectable and
plots stay vector. Needs a QGuiApplication; safe to call from a worker thread.
"""

from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path

import numpy as np
from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPainterPath, QImage, QPdfWriter, QPen

SIDE_NAMES = {"L": "Left", "R": "Right", None: "Both ears", "M": "Both ears"}
SIDE_COLORS = {"L": "#0072B2", "R": "#D55E00", None: "#009E73"}
ACCESSORY_KINDS = {"tips": "Ear tips", "dac": "DAC / amp", "amp": "Amp", "cable": "Cable",
                   "nozzle": "Nozzle / filter", "filter": "Nozzle / filter", "other": "Other"}
DPI = 300
_TICKS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)


def _safe(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", text).strip(" .")


def sheet_file_name(manifest, branding) -> str:
    return _safe(f"{manifest.customer.name} – {manifest.iem.display_name} – "
                 f"{branding.product_name} calibration sheet") + ".pdf"


def _whose(acc, manifest) -> str:
    if acc.supplied_by == "customer":
        return "yours"
    name = manifest.operator.name if manifest.operator and manifest.operator.name else "your tuner"
    return f"provided by {name}"


def _sides(calibration):
    order = {"L": 0, "R": 1, None: 2}
    return sorted({f.side for f in calibration.fits}, key=lambda s: order[s])


def _ranges(measurements) -> dict:
    if measurements is None:
        return {}
    return {side: (float(np.min(m.peaks)), float(np.max(m.peaks))) for side, m in measurements.models.items()}


def sheet_lines(calibration, branding, measurements=None) -> list[str]:
    """The sheet's text, in reading order (also used for tests and accessibility)."""
    m = calibration.manifest
    lines = [branding.title, "Calibration sheet",
             f"Customer: {m.customer.name}",
             f"IEM: {m.iem.display_name}" + (f" (serial {m.iem.serial})" if m.iem.serial else ""),
             f"Calibration date: {m.created}"]
    if m.coupler:
        lines.append(f"Measured on: {m.coupler}")
    if m.operator and m.operator.name:
        lines.append(f"Calibrated by: {m.operator.name}")
    for acc in m.accessories:
        kind = ACCESSORY_KINDS.get(acc.kind.lower(), acc.kind.capitalize())
        lines.append(f"{kind}: {acc.description} ({_whose(acc, m)})")
    ranges = _ranges(measurements)
    for side in _sides(calibration):
        count = sum(1 for f in calibration.fits if f.side == side)
        line = f"{SIDE_NAMES[side]}: {count} fits"
        key = side or "M"
        if key in ranges:
            lo, hi = ranges[key]
            line += f", primary resonance measured {lo:,.0f}–{hi:,.0f} Hz"
        lines.append(line)
    if m.notes:
        lines.append(f"Notes: {m.notes}")
    lines.append(f"Calibration id: {m.calibration_id}")
    return lines


# ── drawing ─────────────────────────────────────────────────────────────────

def _mm(value: float) -> float:
    return value / 25.4 * DPI


def _font(size_pt: float, bold: bool = False) -> QFont:
    font = QFont("Helvetica")
    font.setPointSizeF(size_pt)
    font.setBold(bold)
    return font


def _plot(p: QPainter, rect: QRectF, fits, side, rng) -> None:
    color = QColor(SIDE_COLORS.get(side, "#009E73"))
    curves = [(np.asarray(f.freqs, float), np.asarray(f.db, float)) for f in fits]
    ys = np.concatenate([d[(fr >= 20) & (fr <= 20000)] for fr, d in curves])
    lo, hi = math.floor(ys.min() / 5) * 5 - 5, math.ceil(ys.max() / 5) * 5 + 5
    lx, hx = math.log10(20), math.log10(20000)

    def pt(f, y):
        return QPointF(rect.left() + (math.log10(f) - lx) / (hx - lx) * rect.width(),
                       rect.bottom() - (y - lo) / (hi - lo) * rect.height())

    grid = QPen(QColor("#D0D0D0"))
    grid.setWidthF(_mm(0.15))
    p.setPen(grid)
    p.setFont(_font(6.5))
    for t in _TICKS:
        x = pt(t, lo).x()
        p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        label = f"{t / 1000:g}k" if t >= 1000 else str(t)
        p.setPen(QColor("#555555"))
        p.drawText(QRectF(x - _mm(6), rect.bottom() + _mm(0.8), _mm(12), _mm(4)), Qt.AlignHCenter, label)
        p.setPen(grid)
    for y in range(int(lo), int(hi) + 1, 5 if hi - lo <= 40 else 10):
        yy = pt(20, y).y()
        p.drawLine(QPointF(rect.left(), yy), QPointF(rect.right(), yy))
        p.setPen(QColor("#555555"))
        p.drawText(QRectF(rect.left() - _mm(10), yy - _mm(2), _mm(9), _mm(4)), Qt.AlignRight | Qt.AlignVCenter,
                   f"{y}")
        p.setPen(grid)
    if rng is not None:  # shade the measured primary-resonance range
        band = QRectF(pt(rng[0], hi), pt(rng[1], lo))
        if band.width() < _mm(0.6):
            band.setWidth(_mm(0.6))
        shade = QColor(color)
        shade.setAlphaF(0.12)
        p.fillRect(band, shade)
    pen = QPen(color)
    pen.setWidthF(_mm(0.25))
    p.setPen(pen)
    for freqs, db in curves:
        mask = (freqs >= 20) & (freqs <= 20000)
        path = QPainterPath()
        for i, (f, y) in enumerate(zip(freqs[mask], db[mask])):
            path.lineTo(pt(f, y)) if i else path.moveTo(pt(f, y))
        p.drawPath(path)
    frame = QPen(QColor("#808080"))
    frame.setWidthF(_mm(0.2))
    p.setPen(frame)
    p.setBrush(Qt.NoBrush)
    p.drawRect(rect)


def write_calibration_sheet(path, calibration, branding, measurements=None) -> Path:
    """Write the one-page A4 sheet to ``path`` and return it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if measurements is None:
        try:
            measurements = calibration.measurements()
        except ValueError:
            measurements = None
    m = calibration.manifest
    writer = QPdfWriter(str(path))
    writer.setResolution(DPI)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(15, 15, 15, 15),
                                     QPageLayout.Millimeter))
    writer.setTitle(f"{branding.product_name} calibration sheet — {m.customer.name}, {m.iem.display_name}")
    writer.setCreator(branding.title)
    p = QPainter(writer)
    try:
        page = QRectF(0, 0, writer.width(), writer.height())
        y = 0.0
        logo = branding.logo_path
        if logo is not None and logo.is_file():
            image = QImage(str(logo))  # QImage, not QPixmap: sheets are also drawn on worker threads
            if not image.isNull():
                h = _mm(14)
                w = h * image.width() / max(image.height(), 1)
                p.drawImage(QRectF(page.right() - w, 0, w, h), image, QRectF(image.rect()))
        p.setPen(QColor("#111111"))
        p.setFont(_font(20, True))
        p.drawText(QRectF(0, y, page.width(), _mm(10)), Qt.AlignLeft | Qt.AlignVCenter, branding.product_name)
        y += _mm(10)
        p.setFont(_font(10))
        p.setPen(QColor("#444444"))
        p.drawText(QRectF(0, y, page.width(), _mm(6)), Qt.AlignLeft | Qt.AlignVCenter, branding.byline)
        y += _mm(10)
        p.setPen(QColor("#111111"))
        p.setFont(_font(14, True))
        p.drawText(QRectF(0, y, page.width(), _mm(8)), Qt.AlignLeft | Qt.AlignVCenter, "Calibration sheet")
        y += _mm(10)

        lines = sheet_lines(calibration, branding, measurements)[2:]
        info = [line for line in lines if not line.startswith(("Left:", "Right:", "Both ears:", "Calibration id"))]
        p.setFont(_font(10))
        for line in info:
            label, _, value = line.partition(": ")
            p.setFont(_font(10, True))
            p.drawText(QRectF(0, y, _mm(42), _mm(6)), Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setFont(_font(10))
            p.drawText(QRectF(_mm(44), y, page.width() - _mm(44), _mm(6)),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, value)
            y += _mm(6.5)
        y += _mm(4)

        sides = _sides(calibration)
        ranges = _ranges(measurements)
        footer = _mm(16)
        space = page.bottom() - footer - y
        slot = space / max(len(sides), 1)
        for side in sides:
            fits = [f for f in calibration.fits if f.side == side]
            rng = ranges.get(side or "M")
            title = f"{SIDE_NAMES[side]} ear: {len(fits)} fits, from fully inserted until the IEM pops out"
            if rng is not None:
                title += f"; primary resonance {rng[0]:,.0f}–{rng[1]:,.0f} Hz (shaded)"
            p.setPen(QColor("#111111"))
            p.setFont(_font(10, True))
            p.drawText(QRectF(0, y, page.width(), _mm(6)), Qt.AlignLeft | Qt.AlignVCenter, title)
            plot = QRectF(_mm(12), y + _mm(8), page.width() - _mm(14), slot - _mm(18))
            _plot(p, plot, fits, side, rng)
            p.setFont(_font(7))
            p.setPen(QColor("#555555"))
            p.drawText(QRectF(plot.left(), plot.bottom() + _mm(4.5), plot.width(), _mm(4)), Qt.AlignHCenter,
                       "Frequency (Hz), level (dB, coupler)")
            y += slot
        p.setFont(_font(8))
        p.setPen(QColor("#555555"))
        p.drawText(QRectF(0, page.bottom() - _mm(10), page.width(), _mm(10)), Qt.AlignLeft | Qt.TextWordWrap,
                   f"Keep this sheet with your calibration file. Calibration id {m.calibration_id}, "
                   f"sheet made {date.today().isoformat()}.")
    finally:
        p.end()
    return path
