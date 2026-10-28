"""History: the saved tunings of one calibration (see GLOSSARY).

One entry per distinct tuning (resonances, upper treble, target): exporting,
applying to Equalizer APO or pressing "Save tuning" adds a tuning that isn't
there yet, and only brings an existing one up to date (when it was last used,
its files) otherwise — so switching between saved tunings never adds entries.
Each entry embeds its target curve, so it can be reapplied even after the
target file is gone. The entry last applied to the system EQ is "in use". One
JSON file per calibration in the data folder's ``history/``; writes are atomic
and a corrupt file is kept as a backup rather than lost. Older files, which
logged every export and apply, are merged into one entry per tuning on load.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from eq_helper.calibration.tuning import Tuning

REASONS = ("export", "apply", "manual")
FILE_VERSION = 2
SAME_HZ = 0.5          # resonances this close are the same tuning (the controls move in whole Hz)


@dataclass(frozen=True, eq=False)
class HistoryEntry:
    id: str
    date: str
    reason: str
    tuning: Tuning
    target_name: str
    target_freqs: np.ndarray
    target_db: np.ndarray
    note: str = ""         # the customer's name for it ("" = named from its settings)
    files: list[str] = field(default_factory=list)
    app: str | None = None
    last_used: str = ""    # when it was last exported, applied or saved ("" = its date)
    last_reason: str = ""

    @property
    def used(self) -> str:
        return self.last_used or self.date

    @property
    def used_reason(self) -> str:
        return self.last_reason or self.reason

    def to_dict(self) -> dict:
        return {"id": self.id, "date": self.date, "reason": self.reason, "tuning": self.tuning.to_dict(),
                "target": {"name": self.target_name, "freqs": [float(x) for x in self.target_freqs],
                           "db": [float(x) for x in self.target_db]},
                "note": self.note, "files": list(self.files), "app": self.app,
                "last_used": self.last_used, "last_reason": self.last_reason}

    def same_tuning(self, tuning: Tuning, freqs, db) -> bool:
        """The same resonances, upper treble and target curve."""
        mine = self.tuning
        if mine.target_name != tuning.target_name or not _close(mine.primary, tuning.primary):
            return False
        if mine.upper_enabled != tuning.upper_enabled:
            return False
        if mine.upper_enabled and not _close(mine.upper, tuning.upper):
            return False
        freqs, db = np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)
        return (freqs.shape == self.target_freqs.shape and np.allclose(freqs, self.target_freqs)
                and np.allclose(db, self.target_db, atol=1e-6))

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryEntry":
        target = data["target"]
        freqs = np.asarray(target["freqs"], dtype=float)
        db = np.asarray(target["db"], dtype=float)
        if freqs.shape != db.shape or freqs.ndim != 1 or len(freqs) < 2:
            raise ValueError("Bad target curve in history entry.")
        if data["reason"] not in REASONS:
            raise ValueError("Bad history reason.")
        last_reason = str(data.get("last_reason") or "")
        return cls(str(data["id"]), str(data["date"]), data["reason"], Tuning.from_dict(data["tuning"]),
                   str(target.get("name", "")), freqs, db, str(data.get("note", "")),
                   [str(f) for f in data.get("files", [])], data.get("app"), str(data.get("last_used") or ""),
                   last_reason if last_reason in REASONS else "")


def _close(a: dict, b: dict) -> bool:
    return set(a) == set(b) and all(abs(float(a[k]) - float(b[k])) <= SAME_HZ for k in a)


def _merged(old: HistoryEntry, new: HistoryEntry) -> HistoryEntry:
    """``old`` brought up to date by a later use ``new`` of the same tuning."""
    files = list(dict.fromkeys([*old.files, *new.files]))
    return replace(old, note=new.note or old.note, files=files, app=new.app or old.app,
                   last_used=new.used, last_reason=new.used_reason)


class HistoryStore:
    def __init__(self, folder, calibration_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", calibration_id or ""):
            raise ValueError(f"Invalid calibration id {calibration_id!r}.")
        self.folder = Path(folder)
        self.calibration_id = calibration_id
        self.path = self.folder / f"{calibration_id}.json"
        self.in_use: str | None = None   # the entry last applied to the system EQ
        self._entries = self._load()
        if self._merge_duplicates():
            self._write()

    def _load(self) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data["entries"]
            if not isinstance(raw, list):
                raise ValueError
            self.in_use = data.get("in_use") if isinstance(data.get("in_use"), str) else None
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

    def _merge_duplicates(self) -> bool:
        """One entry per tuning (older files logged every export and apply): later uses fold into the
        first entry of each tuning. True if anything changed."""
        kept: list[HistoryEntry] = []
        for entry in self._entries:
            index = next((i for i, k in enumerate(kept)
                          if k.same_tuning(entry.tuning, entry.target_freqs, entry.target_db)), None)
            if index is None:
                kept.append(entry)
                continue
            if self.in_use == entry.id:
                self.in_use = kept[index].id
            kept[index] = _merged(kept[index], entry)
        if len(kept) == len(self._entries):
            return False
        self._entries = sorted(kept, key=lambda e: e.used)
        return True

    def _write(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"version": FILE_VERSION, "calibration_id": self.calibration_id, "in_use": self.in_use,
                "entries": [e.to_dict() for e in self._entries]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def entries(self) -> list[HistoryEntry]:
        """Most recently used first."""
        return list(reversed(self._entries))

    def get(self, entry_id: str) -> HistoryEntry:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)

    def find(self, tuning: Tuning, target) -> HistoryEntry | None:
        """The entry for this tuning, if it's saved."""
        freqs, db = target
        return next((e for e in self._entries if e.same_tuning(tuning, freqs, db)), None)

    def save(self, tuning: Tuning, target, reason: str, note: str = "", files=(), app=None) -> HistoryEntry:
        """Add this tuning, or bring its entry up to date (a note given now renames it). An "apply" makes
        it the one in use."""
        if reason not in REASONS:
            raise ValueError(f"History reason must be one of {REASONS}, not {reason!r}.")
        if target is None:
            raise ValueError("A tuning needs its target curve to be saved.")
        freqs, db = (np.asarray(x, dtype=float) for x in target)
        now = datetime.now().isoformat(timespec="seconds")
        entry = HistoryEntry(str(uuid.uuid4()), now, reason, tuning, tuning.target_name, freqs, db, note,
                             [str(f) for f in files], app)
        old = self.find(tuning, (freqs, db))
        if old is not None:
            self._entries.remove(old)
            entry = _merged(old, entry)
        self._entries.append(entry)  # most recently used last
        if reason == "apply":
            self.in_use = entry.id
        self._write()
        return entry

    def reapply(self, entry_id: str) -> tuple[Tuning, tuple[np.ndarray, np.ndarray]]:
        """The tuning and its stored target curve, for ``TuningSession.restore``."""
        entry = self.get(entry_id)
        return entry.tuning, (entry.target_freqs, entry.target_db)

    def set_note(self, entry_id: str, note: str) -> None:
        index = self._entries.index(self.get(entry_id))
        self._entries[index] = replace(self._entries[index], note=note)
        self._write()

    def delete(self, entry_id: str) -> None:
        self._entries.remove(self.get(entry_id))
        if self.in_use == entry_id:
            self.in_use = None
        self._write()
