"""Tunings: the saved tunings of this calibration, and the one you're working on.

Clicking a saved tuning switches every control to it. Moving anything afterwards never changes a saved
tuning: what you have becomes *Current · unsaved* at the top, and it stays there (as *Last unsaved*) while
you try the saved ones, so you can always go back to it. **Save** keeps it, with a name if you like. Below
the list, Apply puts what you hear into the system-wide EQ and Export makes files for another EQ app.

Rows read at a glance: the name, then ``L 8.0k · R 7.4k · target``, and an *in use* tag on the tuning
applied to the system-wide EQ. Right-click (or F2 / Delete) renames or deletes a saved tuning.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem,
)

from .common import Card, hint, side_name, theme_colors, tr

CURRENT = "__current__"   # the row of the tuning you're working on (not saved yet)
REASON_LABELS = {"export": "Exported", "apply": "Applied", "manual": "Saved"}
TITLE, DETAIL, TAG, KIND = (Qt.UserRole + i for i in range(1, 5))


def friendly_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return iso


def _hz_list(values: dict) -> str:
    return " / ".join(f"{values[s]:.0f}" for s in sorted(values))


def _k(hz: float) -> str:
    return f"{hz / 1000:.1f}k"


def entry_name(entry) -> str:
    """Short and telling: the customer's name for it, else target and resonances (left / right)."""
    if entry.note:
        return entry.note
    t = entry.tuning
    name = f"{entry.target_name or tr('No target')} · {_hz_list(t.primary)} Hz"
    if t.upper_enabled and t.upper:
        name += " · " + tr("upper {values}").format(values=_hz_list(t.upper))
    return name


def tuning_detail(tuning) -> str:
    """``L 8.0k · R 7.4k · PEQdB Diamond`` (plus the upper treble when it's set by hand)."""
    sides = " · ".join(f"{s} {_k(hz)}" if s in ("L", "R") else _k(hz) for s, hz in sorted(tuning.primary.items()))
    parts = [sides, tuning.target_name or tr("No target")]
    if tuning.upper_enabled and tuning.upper:
        parts.append(tr("upper {values}").format(values=" / ".join(_k(tuning.upper[s]) for s in sorted(tuning.upper))))
    return " · ".join(parts)


def describe_entry(entry, app_names=None, in_use: bool = False) -> str:
    """Everything about a saved tuning, for its tooltip."""
    t = entry.tuning
    lines = [entry_name(entry) + (" · " + tr("in use") if in_use else ""),
             f"{tr(REASON_LABELS.get(entry.used_reason, entry.used_reason))} {friendly_date(entry.used)}",
             tr("Target: {name}").format(name=entry.target_name or "—"),
             ", ".join(f"{side_name(s)} {t.primary[s]:.0f} Hz" for s in sorted(t.primary))]
    if t.upper_enabled and t.upper:
        lines.append(tr("Upper resonance: {values}").format(
            values=", ".join(f"{side_name(s)} {hz:.0f} Hz" for s, hz in sorted(t.upper.items()))))
    if entry.app:
        lines.append(tr("For: {app}").format(app=(app_names or {}).get(entry.app, entry.app)))
    if entry.files:
        lines.append(tr("Files: {names}").format(names=", ".join(Path(f).name for f in entry.files[:3])
                                                 + (" …" if len(entry.files) > 3 else "")))
    return "\n".join(lines)


class TuningRow(QStyledItemDelegate):
    """Two lines per tuning, painted: the name (amber for the unsaved one), the settings in grey, and a tag."""

    PAD = 10

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt API
        fm = option.fontMetrics
        return QSize(option.rect.width(), 2 * fm.height() + 2 * self.PAD + 2)

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else None
        if style is not None:
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)  # background: the stylesheet's
        c = theme_colors(QApplication.palette())  # the app's scheme (a view's own palette can lag behind)
        rect = opt.rect.adjusted(self.PAD + 2, self.PAD - 2, -self.PAD, -self.PAD + 2)
        fm = opt.fontMetrics
        painter.save()
        tag = index.data(TAG) or ""
        tag_width = 0
        if tag:
            tag_font = QFont(opt.font)
            tag_font.setPointSizeF(opt.font.pointSizeF() * 0.85)
            painter.setFont(tag_font)
            tw = painter.fontMetrics().horizontalAdvance(tag) + 14
            box = QRect(rect.right() - tw, rect.top() + 1, tw, painter.fontMetrics().height() + 4)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(c["control"]))
            painter.drawRoundedRect(box, box.height() / 2, box.height() / 2)
            painter.setPen(QColor(c["text"]))
            painter.drawText(box, Qt.AlignCenter, tag)
            tag_width = tw + 8
        title_font = QFont(opt.font)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        current = index.data(KIND) == CURRENT
        painter.setPen(QColor(c["accent"] if current else c["text"]))
        title_rect = QRect(rect.left(), rect.top(), rect.width() - tag_width, fm.height() + 2)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(index.data(TITLE) or "", Qt.ElideRight, title_rect.width()))
        painter.setFont(opt.font)
        painter.setPen(QColor(c["hint"]))
        detail_rect = QRect(rect.left(), rect.top() + fm.height() + 4, rect.width(), fm.height() + 2)
        painter.drawText(detail_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(index.data(DETAIL) or "", Qt.ElideRight, detail_rect.width()))
        painter.restore()


class TuningsPanel(Card):
    saveRequested = Signal(str)      # name ("" = named from its settings)
    switchRequested = Signal(str)    # a saved tuning's id
    currentRequested = Signal()      # back to the unsaved tuning
    applyRequested = Signal()
    exportRequested = Signal()
    changed = Signal()               # a tuning was renamed or deleted

    def __init__(self, parent=None):
        super().__init__(tr("Tunings"), parent)
        self.store = None
        self.list = QListWidget()
        self.list.setObjectName("tunings")
        self.list.setAccessibleName(tr("Tunings"))
        self.list.setItemDelegate(TuningRow(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemClicked.connect(self._open)
        self.list.itemActivated.connect(self._open)
        self.list.setToolTip(tr("Click a tuning to switch to it. Right-click to rename or delete one."))
        self.body.addWidget(self.list, 1)
        for key, slot in ((QKeySequence(Qt.Key_F2), self.rename_selected),
                          (QKeySequence(Qt.Key_Delete), self.delete_selected)):
            shortcut = QShortcut(key, self.list)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(slot)

        row = QHBoxLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText(tr("Name it (optional): Bright tips"))
        self.note.setAccessibleName(tr("Name for this tuning"))
        self.note.returnPressed.connect(self._save)
        row.addWidget(self.note, 1)
        self.save_button = QPushButton(tr("Save"))
        self.save_button.setToolTip(tr("Keep the tuning you're hearing in this list."))
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        self.body.addLayout(row)
        self.apply_button = QPushButton(tr("Apply to Equalizer APO"))
        self.apply_button.setObjectName("primary")
        self.apply_button.setToolTip(tr("Use the tuning you're hearing for everything you play on this computer."))
        self.apply_button.clicked.connect(self.applyRequested)
        self.apply_button.hide()
        self.body.addWidget(self.apply_button)
        self.export_button = QPushButton(tr("Export for my EQ app…"))
        self.export_button.clicked.connect(self.exportRequested)
        self.body.addWidget(self.export_button)
        self.status = hint("")
        self.body.addWidget(self.status)

    # ── content ────────────────────────────────────────────────────────────
    def set_apply_target(self, app: str) -> None:
        self.apply_button.setText(tr("Apply to {app}").format(app=app))

    def set_apply_available(self, available: bool) -> None:
        self.apply_button.setVisible(available)

    def set_store(self, store) -> None:
        self.store = store

    def set_rows(self, current, saved, active) -> None:
        """``current``: (title, detail) of the unsaved tuning, or None; ``saved``: [(id, title, detail, tag,
        tooltip)]; ``active``: the row you're hearing (an id, or ``CURRENT``)."""
        self.list.blockSignals(True)
        self.list.clear()
        if current is not None:
            self._add(CURRENT, *current, "", tr("The tuning you're working on. Save it to keep it."), CURRENT)
        for entry_id, title, detail, tag, tooltip in saved:
            self._add(entry_id, title, detail, tag, tooltip, "saved")
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == active:
                self.list.setCurrentRow(i)
                break
        else:
            self.list.setCurrentRow(-1)
        self.list.blockSignals(False)

    def _add(self, entry_id, title, detail, tag, tooltip, kind) -> None:
        item = QListWidgetItem(f"{title}\n{detail}")
        item.setData(Qt.UserRole, entry_id)
        for role, value in ((TITLE, title), (DETAIL, detail), (TAG, tag), (KIND, kind)):
            item.setData(role, value)
        item.setToolTip(tooltip)
        self.list.addItem(item)

    def ids(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def show_status(self, text: str) -> None:
        self.status.setText(text)

    # ── actions ────────────────────────────────────────────────────────────
    def _open(self, item) -> None:
        entry_id = item.data(Qt.UserRole)
        if entry_id == CURRENT:
            self.currentRequested.emit()
        elif entry_id:
            self.switchRequested.emit(entry_id)

    def open_row(self, row: int) -> None:
        self._open(self.list.item(row))

    def _save(self) -> None:
        self.saveRequested.emit(self.note.text().strip())
        self.note.clear()

    def _selected_saved(self):
        item = self.list.currentItem()
        entry_id = item.data(Qt.UserRole) if item is not None else None
        return None if entry_id in (None, CURRENT) else entry_id

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or item.data(Qt.UserRole) == CURRENT:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(tr("Rename…"), self.rename_selected)
        menu.addAction(tr("Delete"), self.delete_selected)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def ask_name(self, current: str):
        return QInputDialog.getText(self, tr("Rename tuning"), tr("Name (leave empty to name it from its "
                                                                  "settings):"), text=current)

    def confirm_delete(self, name: str) -> bool:
        answer = QMessageBox.question(self, tr("Delete tuning"), tr("Delete “{name}”?").format(name=name))
        return answer == QMessageBox.Yes

    def rename_selected(self) -> None:
        entry_id = self._selected_saved()
        if entry_id is None or self.store is None:
            return
        name, ok = self.ask_name(self.store.get(entry_id).note)
        if ok:
            self.store.set_note(entry_id, name.strip())
            self.changed.emit()

    def delete_selected(self) -> None:
        entry_id = self._selected_saved()
        if entry_id is None or self.store is None:
            return
        if self.confirm_delete(entry_name(self.store.get(entry_id))):
            self.store.delete(entry_id)
            self.changed.emit()
