"""The customer's calibrations: discovery, import, selection.

Every ``.rescal`` found in the calibrations folder at launch, double-clicked,
dropped on the window or opened is validated and copied into the data
folder's library under its calibration id, so it survives the download being
deleted. While the folder holds calibrations, the library mirrors it: one
whose file was deleted or replaced there is forgotten. One app can hold
several calibrations; the newest is selected unless the customer picked
another, and any can be removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from eq_helper.calibration.rescal import RESCAL_SUFFIX, Calibration, Manifest, RescalError, read_rescal

from .paths import AppPaths


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class LibraryEntry:
    calibration_id: str
    path: Path
    manifest: Manifest
    imported: str = ""


@dataclass
class ScanReport:
    imported: list[LibraryEntry] = field(default_factory=list)
    problems: list[tuple[Path, str]] = field(default_factory=list)


class CalibrationLibrary:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._index = self._load_index()
        self._entries: dict[str, LibraryEntry] = {}
        self._load_store()

    # ── persistence ────────────────────────────────────────────────────────
    def _load_index(self) -> dict:
        try:
            data = json.loads(self.paths.library_index.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (OSError, ValueError):
            data = {}
        data.setdefault("selected", None)
        data.setdefault("removed", [])
        data.setdefault("imported", {})  # id -> {"at": iso, "sha256": hex}
        data.setdefault("seen", {})      # source path -> [mtime_ns, size]
        data.setdefault("sources", {})   # source path -> calibration id it held when last read
        return data

    def _save_index(self) -> None:
        _write_json(self.paths.library_index, self._index)

    def _load_store(self) -> None:
        folder = self.paths.library_dir
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*" + RESCAL_SUFFIX)):
            try:
                manifest = read_rescal(path).manifest
            except RescalError:
                continue
            info = self._index["imported"].get(manifest.calibration_id, {})
            self._entries[manifest.calibration_id] = LibraryEntry(
                manifest.calibration_id, path, manifest, info.get("at", ""))

    # ── queries ────────────────────────────────────────────────────────────
    def list(self) -> list[LibraryEntry]:
        """Newest calibration first (calibration date, then import time)."""
        return sorted(self._entries.values(), key=lambda e: (e.manifest.created, e.imported), reverse=True)

    @property
    def selected(self) -> LibraryEntry | None:
        chosen = self._entries.get(self._index.get("selected"))
        if chosen is not None:
            return chosen
        entries = self.list()
        return entries[0] if entries else None

    def select(self, calibration_id: str) -> None:
        if calibration_id not in self._entries:
            raise KeyError(calibration_id)
        self._index["selected"] = calibration_id
        self._save_index()

    def load(self, calibration_id: str) -> Calibration:
        return read_rescal(self._entries[calibration_id].path)

    # ── changes ────────────────────────────────────────────────────────────
    def scan(self) -> ScanReport:
        """Import new or changed files from the calibrations folder(s), and forget the ones found
        there before whose file has since been deleted or replaced (see :meth:`_prune`)."""
        report = ScanReport()
        seen, sources = self._index["seen"], self._index["sources"]
        earlier = set(seen)  # files read by launches before this one
        for folder in self.paths.scan_dirs:
            if not folder.is_dir():
                continue
            present: set[str] = set()
            for path in sorted(p for p in folder.iterdir()
                               if p.is_file() and p.suffix.lower() == RESCAL_SUFFIX):
                try:
                    stat = path.stat()
                except OSError as exc:
                    report.problems.append((path, str(exc)))
                    continue
                key, fingerprint = str(path.resolve()), [stat.st_mtime_ns, stat.st_size]
                if seen.get(key) == fingerprint and key in sources:
                    present.add(sources[key])
                    continue
                try:
                    if seen.get(key) == fingerprint:  # unchanged, from before sources were kept
                        sources[key] = read_rescal(path).manifest.calibration_id
                        present.add(sources[key])
                        continue
                    entry, changed = self._import(path, explicit=False)
                    sources[key] = read_rescal(path).manifest.calibration_id if entry is None \
                        else entry.calibration_id
                except (RescalError, OSError) as exc:
                    report.problems.append((path, str(exc)))
                    continue
                present.add(sources[key])
                seen[key] = fingerprint
                if entry is not None:
                    self._index["imported"][entry.calibration_id]["source"] = key
                if changed and entry is not None:
                    report.imported.append(entry)
            self._prune(folder, present, earlier)
        self._save_index()
        return report

    def _prune(self, folder: Path, present: set[str], earlier: set[str]) -> None:
        """The library mirrors the calibrations folder: a calibration that came from it, whose file is
        no longer there (deleted, or replaced by a newer calibration), is forgotten. Explicit imports
        stay, and nothing is forgotten while the folder holds no calibration at all (the download may
        just have been moved or emptied)."""
        if not present:
            return
        folder = folder.resolve()
        from_here = bool(earlier) and all(Path(key).parent == folder for key in earlier)
        for cid in list(self._entries):
            info = self._index["imported"].get(cid, {})
            if cid in present or info.get("explicit"):
                continue
            source = info.get("source")
            # before sources were kept: only when every file ever seen came from this folder
            if (Path(source).parent == folder) if source else from_here:
                self._forget(cid)

    def _forget(self, calibration_id: str) -> None:
        entry = self._entries.pop(calibration_id)
        entry.path.unlink(missing_ok=True)
        self._index["imported"].pop(calibration_id, None)
        if self._index.get("selected") == calibration_id:
            self._index["selected"] = None

    def import_file(self, path) -> LibraryEntry:
        """Explicit import (double-click, drop, "Open…"). Raises RescalError."""
        entry, _ = self._import(Path(path), explicit=True)
        self._index["imported"][entry.calibration_id]["explicit"] = True
        self._save_index()
        return entry

    def _import(self, path: Path, explicit: bool):
        calibration = read_rescal(path)  # validates before anything is copied
        cid = calibration.manifest.calibration_id
        if cid in self._index["removed"]:
            if not explicit:
                return None, False
            self._index["removed"].remove(cid)
        digest = _sha256(path)
        existing = self._entries.get(cid)
        if existing is not None and self._index["imported"].get(cid, {}).get("sha256") == digest:
            return existing, False
        target = self.paths.library_dir / f"{cid}{RESCAL_SUFFIX}"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        shutil.copyfile(path, tmp)
        os.replace(tmp, target)
        at = datetime.now().isoformat(timespec="seconds")
        self._index["imported"][cid] = {"at": at, "sha256": digest}
        entry = LibraryEntry(cid, target, calibration.manifest, at)
        self._entries[cid] = entry
        return entry, True

    def remove(self, calibration_id: str) -> None:
        """Forget a calibration; launch scans won't bring it back, an explicit import will."""
        self._forget(calibration_id)
        if calibration_id not in self._index["removed"]:
            self._index["removed"].append(calibration_id)
        self._save_index()
