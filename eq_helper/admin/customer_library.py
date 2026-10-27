"""The operator's customer library: every calibration generated, for one-click
regeneration, plus the operator's own accessories remembered per kind.

Lives in ``~/.eq_helper/customer_library/`` — customer data never enters git
(ADR 0003). One JSON file per calibration record; writes are atomic and an
unreadable file is skipped rather than breaking the library.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from eq_helper.calibration.rescal import (
    Accessory, Manifest, RescalError, manifest_from_dict, manifest_to_dict, new_calibration_id,
)

MAX_REMEMBERED = 12


def default_library_root() -> Path:
    return Path(os.path.expanduser("~")) / ".eq_helper" / "customer_library"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class MeasurementSource:
    """Where a calibration's measurement sets came from on the operator's disk."""

    mode: str                 # "folder" (one folder, L/R detected) | "sides" (separate L and R folders)
    folder: str | None = None
    left: str | None = None
    right: str | None = None

    @classmethod
    def from_folder(cls, folder) -> "MeasurementSource":
        return cls("folder", str(folder))

    @classmethod
    def from_sides(cls, left, right) -> "MeasurementSource":
        return cls("sides", None, str(left), str(right))

    def to_dict(self) -> dict:
        return {"mode": self.mode, "folder": self.folder, "left": self.left, "right": self.right}

    @classmethod
    def from_dict(cls, data: dict) -> "MeasurementSource":
        if data.get("mode") not in ("folder", "sides"):
            raise ValueError("Unknown measurement source.")
        return cls(data["mode"], data.get("folder"), data.get("left"), data.get("right"))


@dataclass(frozen=True)
class Customer:
    name: str
    contact: str | None
    last_calibrated: str


@dataclass
class CalibrationRecord:
    record_id: str
    manifest: Manifest
    source: MeasurementSource
    generated_at: str
    outputs: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)   # "windows" | "macos" | "linux"; [] = file only

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, "manifest": manifest_to_dict(self.manifest),
                "source": self.source.to_dict(), "generated_at": self.generated_at,
                "outputs": list(self.outputs), "targets": list(self.targets), "systems": list(self.systems)}

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationRecord":
        return cls(str(data["record_id"]), manifest_from_dict(data["manifest"]),
                   MeasurementSource.from_dict(data["source"]), str(data["generated_at"]),
                   [str(p) for p in data.get("outputs", [])], [str(t) for t in data.get("targets", [])],
                   [str(s) for s in data.get("systems", [])])


class CustomerLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else default_library_root()
        self.records_dir = self.root / "records"
        self._accessories_path = self.root / "accessories.json"
        self._records: dict[str, CalibrationRecord] = {}
        self._accessories: dict[str, list[str]] = {}
        self._load()

    # ── persistence ────────────────────────────────────────────────────────
    def _load(self) -> None:
        if self.records_dir.is_dir():
            for path in sorted(self.records_dir.glob("*.json")):
                try:
                    record = CalibrationRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, KeyError, TypeError, RescalError):
                    continue
                self._records[record.record_id] = record
        try:
            data = json.loads(self._accessories_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._accessories = {str(k): [str(v) for v in vs] for k, vs in data.items() if isinstance(vs, list)}
        except (OSError, ValueError):
            self._accessories = {}

    # ── records ────────────────────────────────────────────────────────────
    def save(self, manifest: Manifest, source: MeasurementSource, outputs=(), targets=(), systems=(),
             record_id: str | None = None) -> CalibrationRecord:
        record = CalibrationRecord(record_id or str(uuid.uuid4()), manifest, source,
                                   datetime.now().isoformat(timespec="microseconds"),
                                   [str(p) for p in outputs], list(targets), list(systems))
        _write_json(self.records_dir / f"{record.record_id}.json", record.to_dict())
        self._records[record.record_id] = record
        self.remember_accessories(manifest.accessories)
        return record

    def get(self, record_id: str) -> CalibrationRecord:
        return self._records[record_id]

    def records(self) -> list[CalibrationRecord]:
        """Newest generated first."""
        return sorted(self._records.values(), key=lambda r: r.generated_at, reverse=True)

    def records_for(self, customer_name: str) -> list[CalibrationRecord]:
        key = customer_name.strip().casefold()
        return [r for r in self.records() if r.manifest.customer.name.strip().casefold() == key]

    def last_record(self) -> CalibrationRecord | None:
        records = self.records()
        return records[0] if records else None

    def customers(self) -> list[Customer]:
        """Each customer once, most recently calibrated first."""
        seen: dict[str, Customer] = {}
        for record in self.records():
            c = record.manifest.customer
            key = c.name.strip().casefold()
            if key not in seen:
                seen[key] = Customer(c.name, c.contact, record.manifest.created)
        return list(seen.values())

    def regenerate_manifest(self, record: CalibrationRecord, new_calibration: bool = True) -> Manifest:
        """The record's manifest again; a new calibration gets a fresh id and today's date."""
        manifest = manifest_from_dict(manifest_to_dict(record.manifest))  # deep copy
        if new_calibration:
            manifest = replace(manifest, calibration_id=new_calibration_id(), created=date.today().isoformat())
        return manifest

    # ── operator accessories ───────────────────────────────────────────────
    def remember_accessories(self, accessories) -> None:
        """Remember the operator's own accessories (customer-supplied ones are not kept)."""
        changed = False
        for acc in accessories:
            if not isinstance(acc, Accessory) or acc.supplied_by != "operator":
                continue
            text = acc.description.strip()
            if not text:
                continue
            known = [d for d in self._accessories.get(acc.kind, []) if d.casefold() != text.casefold()]
            self._accessories[acc.kind] = ([text] + known)[:MAX_REMEMBERED]
            changed = True
        if changed:
            _write_json(self._accessories_path, self._accessories)

    def operator_accessories(self, kind: str) -> list[str]:
        """Remembered descriptions for ``kind``, last used first."""
        return list(self._accessories.get(kind, []))

    def last_used(self, kind: str) -> str | None:
        items = self._accessories.get(kind)
        return items[0] if items else None
