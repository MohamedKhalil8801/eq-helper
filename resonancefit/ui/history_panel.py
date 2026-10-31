"""History sidebar: the tunings saved for this calibration, most recently used first."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QMessageBox, QPushButton

from .common import Card, hint, side_name, tr

REASON_LABELS = {"export": "Exported", "apply": "Applied", "manual": "Saved"}


def friendly_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return iso


def _hz_list(values: dict) -> str:
    return " / ".join(f"{values[s]:.0f}" for s in sorted(values))


def entry_name(entry) -> str:
    """Short and telling: the customer's name for it, else target and resonances (left / right)."""
    if entry.note:
        return entry.note
    t = entry.tuning
    name = f"{entry.target_name or tr('No target')} · {_hz_list(t.primary)} Hz"
    if t.upper_enabled and t.upper:
        name += " · " + tr("upper {values}").format(values=_hz_list(t.upper))
    return name


def describe_entry(entry, app_names=None, in_use: bool = False) -> str:
    t = entry.tuning
    title = entry_name(entry) + (" · " + tr("in use") if in_use else "")
    lines = [title, f"{tr(REASON_LABELS.get(entry.used_reason, entry.used_reason))} {friendly_date(entry.used)}"]
    if entry.note:  # a named tuning: what's in it (an unnamed one says it in its name)
        lines.append(tr("Target: {name}").format(name=entry.target_name or "—"))
        lines.append(", ".join(f"{side_name(s)} {t.primary[s]:.0f} Hz" for s in sorted(t.primary)))
        if t.upper_enabled and t.upper:
            lines.append(tr("Upper resonance: {values}").format(
                values=", ".join(f"{side_name(s)} {hz:.0f} Hz" for s, hz in sorted(t.upper.items()))))
    if entry.app:
        lines.append(tr("For: {app}").format(app=(app_names or {}).get(entry.app, entry.app)))
    if entry.files:
        lines.append(tr("Files: {names}").format(names=", ".join(Path(f).name for f in entry.files[:3])
                                                 + (" …" if len(entry.files) > 3 else "")))
    return "\n".join(lines)


class HistoryPanel(Card):
    reapplyRequested = Signal(str)  # entry id
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("History"), parent)
        self.store = None
        self.app_names: dict[str, str] = {}
        self.empty = hint(tr("Your saved tunings appear here when you save, export or apply one. Each tuning "
                             "is kept once; using it again just moves it to the top."))
        self.body.addWidget(self.empty)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setObjectName("history")  # styled in common.build_style
        self.list.setAccessibleName(tr("Saved tunings"))
        self.list.itemDoubleClicked.connect(lambda _item: self._reapply())
        self.list.currentRowChanged.connect(self._sync_buttons)
        self.body.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.reapply_button = QPushButton(tr("Reapply"))
        self.reapply_button.setToolTip(tr("Put every control back the way it was for this tuning."))
        self.reapply_button.clicked.connect(self._reapply)
        row.addWidget(self.reapply_button)
        self.rename_button = QPushButton(tr("Rename"))
        self.rename_button.setToolTip(tr("Give this tuning a short name of your own."))
        self.rename_button.clicked.connect(self._rename)
        row.addWidget(self.rename_button)
        self.delete_button = QPushButton(tr("Delete"))
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        self.body.addLayout(row)
        self._sync_buttons()

    def set_store(self, store) -> None:
        self.store = store
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        entries = self.store.entries() if self.store is not None else []
        in_use = getattr(self.store, "in_use", None)
        for entry in entries:
            item = QListWidgetItem(describe_entry(entry, self.app_names, entry.id == in_use))
            item.setData(Qt.UserRole, entry.id)
            self.list.addItem(item)
        self.empty.setVisible(not entries)
        self.list.setVisible(bool(entries))
        self._sync_buttons()
        self.changed.emit()

    def _selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _sync_buttons(self, *_):
        has = self._selected_id() is not None
        self.reapply_button.setEnabled(has)
        self.rename_button.setEnabled(has)
        self.delete_button.setEnabled(has)

    def _reapply(self) -> None:
        entry_id = self._selected_id()
        if entry_id:
            self.reapplyRequested.emit(entry_id)

    def ask_name(self, current: str):
        return QInputDialog.getText(self, tr("Rename tuning"), tr("Name (leave empty to name it from its "
                                                                  "settings):"), text=current)

    def _rename(self) -> None:
        entry_id = self._selected_id()
        if not entry_id or self.store is None:
            return
        name, ok = self.ask_name(self.store.get(entry_id).note)
        if ok:
            self.store.set_note(entry_id, name.strip())
            self.refresh()

    def _delete(self) -> None:
        entry_id = self._selected_id()
        if not entry_id or self.store is None:
            return
        answer = QMessageBox.question(self, tr("Delete tuning"), tr("Delete this saved tuning?"))
        if answer == QMessageBox.Yes:
            self.store.delete(entry_id)
            self.refresh()
