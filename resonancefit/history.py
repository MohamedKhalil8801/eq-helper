"""History: the saved tunings of one calibration (see GLOSSARY).

Entries are created only when the customer exports, applies to Equalizer APO
or presses "Save tuning". Each entry embeds its target curve, so it can be
reapplied even after the target file is gone. One JSON file per calibration
in the data folder's ``history/``; writes are atomic and a corrupt file is
kept as a backup rather than lost.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from eq_helper.calibration.tuning import Tuning

REASONS = ("export", "apply", "manual")
FILE_VERSION = 1


@dataclass(frozen=True, eq=False)
class HistoryEntry:
    id: str
    date: str
    reason: str
    tuning: Tuning
    target_name: str
    target_freqs: np.ndarray
    target_db: np.ndarray
    note: str = ""
    files: list[str] = field(default_factory=list)
    app: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "date": self.date, "reason": self.reason, "tuning": self.tuning.to_dict(),
                "target": {"name": self.target_name, "freqs": [float(x) for x in self.target_freqs],
                           "db": [float(x) for x in self.target_db]},
                "note": self.note, "files": list(self.files), "app": self.app}

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryEntry":
        target = data["target"]
        freqs = np.asarray(target["freqs"], dtype=float)
        db = np.asarray(target["db"], dtype=float)
        if freqs.shape != db.shape or freqs.ndim != 1 or len(freqs) < 2:
            raise ValueError("Bad target curve in history entry.")
        if data["reason"] not in REASONS:
            raise ValueError("Bad history reason.")
        return cls(str(data["id"]), str(data["date"]), data["reason"], Tuning.from_dict(data["tuning"]),
                   str(target.get("name", "")), freqs, db, str(data.get("note", "")),
                   [str(f) for f in data.get("files", [])], data.get("app"))


class HistoryStore:
    def __init__(self, folder, calibration_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", calibration_id or ""):
            raise ValueError(f"Invalid calibration id {calibration_id!r}.")
        self.folder = Path(folder)
        self.calibration_id = calibration_id
        self.path = self.folder / f"{calibration_id}.json"
        self._entries = self._load()

    def _load(self) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data["entries"]
            if not isinstance(raw, list):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                os.replace(self.path, self.path.with_name(f"{self.calibration_id}.corrupt-{stamp}.json"))
            except OSError:
                pass
            return []
        entries = []
        for item in raw:
            try:
                entries.append(HistoryEntry.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue  # one bad entry never hides the rest
        return entries

    def _write(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"version": FILE_VERSION, "calibration_id": self.calibration_id,
                "entries": [e.to_dict() for e in self._entries]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def entries(self) -> list[HistoryEntry]:
        """Newest first."""
        return list(reversed(self._entries))

    def get(self, entry_id: str) -> HistoryEntry:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)

    def save(self, tuning: Tuning, target, reason: str, note: str = "", files=(), app=None) -> HistoryEntry:
        if reason not in REASONS:
            raise ValueError(f"History reason must be one of {REASONS}, not {reason!r}.")
        if target is None:
            raise ValueError("A tuning needs its target curve to be saved.")
        freqs, db = (np.asarray(x, dtype=float) for x in target)
        entry = HistoryEntry(str(uuid.uuid4()), datetime.now().isoformat(timespec="seconds"), reason, tuning,
                             tuning.target_name, freqs, db, note, [str(f) for f in files], app)
        self._entries.append(entry)
        self._write()
        return entry

    def reapply(self, entry_id: str) -> tuple[Tuning, tuple[np.ndarray, np.ndarray]]:
        """The tuning and its stored target curve, for ``TuningSession.restore``."""
        entry = self.get(entry_id)
        return entry.tuning, (entry.target_freqs, entry.target_db)

    def set_note(self, entry_id: str, note: str) -> None:
        index = self._entries.index(self.get(entry_id))
        old = self._entries[index]
        self._entries[index] = HistoryEntry(**{**old.__dict__, "note": note})
        self._write()

    def delete(self, entry_id: str) -> None:
        self._entries.remove(self.get(entry_id))
        self._write()
