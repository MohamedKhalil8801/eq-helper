"""The ``.rescal`` calibration file: a versioned contract (ADR 0001).

A calibration file is a zip holding ``manifest.json`` plus every fit as plain
REW-style text under ``fits/L/``, ``fits/R/`` or ``fits/M/`` (one shared set).
The manifest indexes the fits, so original fit names survive exactly while
archive paths stay simple and safe.

Versioning: ``format_version`` is ``"<major>.<minor>"``. A newer *minor*
version loads (unknown keys are ignored); a newer *major* version is refused
with a clear "update the app" error. Files come from outside the app, so the
reader treats every archive as untrusted: size limits, no absolute or ``..``
paths, and only manifest-listed entries are read.
"""

from __future__ import annotations

import io
import json
import math
import re
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

import numpy as np

from eq_helper.dsp.resonance import Fit, Measurements

RESCAL_SUFFIX = ".rescal"  # fixed forever
FORMAT_VERSION = "1.0"
MANIFEST_NAME = "manifest.json"
SUPPLIED_BY = ("customer", "operator")

MAX_ENTRIES = 2000
MAX_ENTRY_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_POINTS = 200_000

_SIDE_DIRS = {"L": "L", "R": "R", None: "M"}
_DIR_SIDES = {v: k for k, v in _SIDE_DIRS.items()}


class RescalError(ValueError):
    """The calibration file is unusable; the message is shown to the customer."""


@dataclass(frozen=True)
class Customer:
    name: str
    contact: str | None = None


@dataclass(frozen=True)
class Iem:
    brand: str
    model: str
    serial: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.brand} {self.model}".strip()


@dataclass(frozen=True)
class Accessory:
    kind: str
    description: str
    supplied_by: str  # "customer" | "operator"

    def __post_init__(self):
        if self.supplied_by not in SUPPLIED_BY:
            raise RescalError(f"Accessory supplied_by must be one of {SUPPLIED_BY}, "
                              f"not {self.supplied_by!r}.")


@dataclass(frozen=True)
class SongLink:
    label: str
    url: str


@dataclass(frozen=True)
class Operator:
    name: str
    contact: str | None = None


def new_calibration_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Manifest:
    customer: Customer
    iem: Iem
    accessories: list[Accessory] = field(default_factory=list)
    coupler: str = ""
    notes: str = ""
    default_target: str = "PEQdB Diamond.txt"
    test_song_links: list[SongLink] = field(default_factory=list)
    operator: Operator | None = None
    created: str = field(default_factory=lambda: date.today().isoformat())
    calibration_id: str = field(default_factory=new_calibration_id)
    format_version: str = FORMAT_VERSION


@dataclass
class Calibration:
    manifest: Manifest
    fits: list[Fit]

    def measurements(self) -> Measurements:
        """Train resonance models for this calibration (see model_cache)."""
        return Measurements.from_fits(self.fits, name=self.manifest.iem.display_name)


# ── validation helpers ──────────────────────────────────────────────────────

def _check_sides(sides) -> None:
    present = set(sides)
    if not present:
        raise RescalError("A calibration needs measured fits; this one has none.")
    if present not in ({"L", "R"}, {None}):
        raise RescalError("A calibration needs both an L and an R measurement set, "
                          "or one shared (unlabelled) set — not "
                          + ", ".join(sorted(_SIDE_DIRS[s] for s in present)) + ".")


def _parse_version(text) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)", str(text))
    if not match:
        raise RescalError(f"Unrecognised calibration format version {text!r}.")
    return int(match.group(1)), int(match.group(2))


def _safe_member(path) -> str:
    text = str(path)
    pure = PurePosixPath(text)
    if (not text or "\\" in text or pure.is_absolute() or re.match(r"^[A-Za-z]:", text)
            or any(part in ("", ".", "..") for part in text.split("/"))
            or len(pure.parts) != 3 or pure.parts[0] != "fits" or pure.parts[1] not in _DIR_SIDES):
        raise RescalError(f"Unsafe or unexpected path in calibration file: {text!r}.")
    return text


def _require(data, key, kind=dict):
    if key not in data or not isinstance(data[key], kind):
        raise RescalError(f"Calibration manifest is missing a valid '{key}'.")
    return data[key]


def _text(data, key, required=False):
    value = data.get(key)
    if value is None:
        if required:
            raise RescalError(f"Calibration manifest is missing '{key}'.")
        return None
    if not isinstance(value, str):
        raise RescalError(f"Calibration manifest field '{key}' must be text.")
    return value


def _manifest_from_json(data) -> Manifest:
    if not isinstance(data, dict):
        raise RescalError("Calibration manifest must be a JSON object.")
    customer = _require(data, "customer")
    iem = _require(data, "iem")
    accessories = []
    for raw in data.get("accessories") or []:
        if not isinstance(raw, dict):
            raise RescalError("Calibration manifest accessory entries must be objects.")
        accessories.append(Accessory(_text(raw, "kind", True), _text(raw, "description", True) or "",
                                     _text(raw, "supplied_by", True)))
    links = []
    for raw in data.get("test_song_links") or []:
        if not isinstance(raw, dict):
            raise RescalError("Calibration manifest test-song links must be objects.")
        links.append(SongLink(_text(raw, "label", True), _text(raw, "url", True)))
    operator = data.get("operator")
    created = _text(data, "created", True)
    try:
        date.fromisoformat(created)
    except ValueError as exc:
        raise RescalError(f"Calibration date {created!r} is not an ISO date.") from exc
    calibration_id = _text(data, "calibration_id", True)
    try:
        uuid.UUID(calibration_id)
    except ValueError as exc:
        raise RescalError("Calibration id is not a valid UUID.") from exc
    return Manifest(
        customer=Customer(_text(customer, "name", True), _text(customer, "contact")),
        iem=Iem(_text(iem, "brand", True), _text(iem, "model", True), _text(iem, "serial")),
        accessories=accessories,
        coupler=_text(data, "coupler") or "",
        notes=_text(data, "notes") or "",
        default_target=_text(data, "default_target") or "PEQdB Diamond.txt",
        test_song_links=links,
        operator=(Operator(_text(operator, "name", True), _text(operator, "contact"))
                  if isinstance(operator, dict) else None),
        created=created,
        calibration_id=calibration_id,
        format_version=FORMAT_VERSION,
    )


# ── fit text ────────────────────────────────────────────────────────────────

def _fit_text(fit: Fit, manifest: Manifest) -> str:
    side = _SIDE_DIRS[fit.side]
    lines = [f"* {manifest.iem.display_name} — {side} — {fit.name}",
             f"* Calibration {manifest.calibration_id} ({manifest.created})",
             "* Freq(Hz) SPL(dB)"]
    lines += [f"{f!r} {d!r}" for f, d in zip(map(float, fit.freqs), map(float, fit.db))]
    return "\n".join(lines) + "\n"


def _parse_fit_text(text: str, name: str) -> tuple[np.ndarray, np.ndarray]:
    freqs, dbs = [], []
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "*#;":
            continue
        parts = s.split()
        if len(parts) < 2:
            raise RescalError(f"Fit {name!r} has a malformed line.")
        try:
            f, d = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise RescalError(f"Fit {name!r} has a non-numeric line.") from exc
        if not (math.isfinite(f) and math.isfinite(d)):
            raise RescalError(f"Fit {name!r} contains non-finite values.")
        freqs.append(f)
        dbs.append(d)
        if len(freqs) > MAX_POINTS:
            raise RescalError(f"Fit {name!r} has too many points.")
    if len(freqs) < 2:
        raise RescalError(f"Fit {name!r} has no data.")
    return np.asarray(freqs), np.asarray(dbs)


# ── public API ──────────────────────────────────────────────────────────────

def write_rescal(path, calibration: Calibration) -> Path:
    """Write ``calibration`` to ``path`` (suffix forced to ``.rescal``)."""
    path = Path(path)
    if path.suffix.lower() != RESCAL_SUFFIX:
        path = path.with_name(path.name + RESCAL_SUFFIX)
    _check_sides(f.side for f in calibration.fits)
    m = calibration.manifest
    for accessory in m.accessories:
        if accessory.supplied_by not in SUPPLIED_BY:
            raise RescalError(f"Accessory supplied_by must be one of {SUPPLIED_BY}.")
    data = asdict(m)
    data["format_version"] = FORMAT_VERSION
    counters: dict[str, int] = {}
    index, members = [], []
    for fit in calibration.fits:
        side_dir = _SIDE_DIRS[fit.side]
        counters[side_dir] = counters.get(side_dir, 0) + 1
        member = f"fits/{side_dir}/{counters[side_dir]:03d}.txt"
        index.append({"path": member, "side": side_dir, "name": fit.name})
        members.append((member, _fit_text(fit, m)))
    data["fits"] = index
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST_NAME, json.dumps(data, indent=2, ensure_ascii=False))
        for member, text in members:
            z.writestr(member, text)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(buffer.getvalue())
    tmp.replace(path)
    return path


def _read_member(z: zipfile.ZipFile, name: str, budget: list[int]) -> bytes:
    try:
        info = z.getinfo(name)
    except KeyError as exc:
        raise RescalError(f"Calibration file is missing {name!r}.") from exc
    if info.file_size > MAX_ENTRY_BYTES:
        raise RescalError(f"Calibration entry {name!r} is too large.")
    budget[0] -= info.file_size
    if budget[0] < 0:
        raise RescalError("Calibration file is too large.")
    with z.open(info) as stream:
        raw = stream.read(MAX_ENTRY_BYTES + 1)
    if len(raw) > MAX_ENTRY_BYTES:  # header lied about the size
        raise RescalError(f"Calibration entry {name!r} is too large.")
    return raw


def read_rescal(path) -> Calibration:
    """Read and validate a calibration file. Raises :class:`RescalError`."""
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RescalError(f"{path.name} is not a calibration file (or is damaged).") from exc
    with z:
        if len(z.infolist()) > MAX_ENTRIES:
            raise RescalError("Calibration file has too many entries.")
        budget = [MAX_TOTAL_BYTES]
        try:
            data = json.loads(_read_member(z, MANIFEST_NAME, budget).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RescalError("Calibration manifest is not valid JSON.") from exc
        if not isinstance(data, dict):
            raise RescalError("Calibration manifest must be a JSON object.")
        major, _minor = _parse_version(data.get("format_version"))
        supported_major, _ = _parse_version(FORMAT_VERSION)
        if major > supported_major:
            raise RescalError("This calibration was made by a newer version of the app. "
                              "Please update the app to open it.")
        if major < supported_major:
            raise RescalError(f"Calibration format version {data['format_version']} is no longer supported.")
        manifest = _manifest_from_json(data)
        index = data.get("fits")
        if not isinstance(index, list):
            raise RescalError("Calibration manifest is missing its 'fits' list.")
        fits, seen = [], set()
        for entry in index:
            if not isinstance(entry, dict):
                raise RescalError("Calibration fits entries must be objects.")
            member = _safe_member(entry.get("path", ""))
            side_dir = PurePosixPath(member).parts[1]
            if entry.get("side", side_dir) != side_dir:
                raise RescalError(f"Fit path {member!r} does not match its side.")
            if member in seen:
                raise RescalError(f"Duplicate fit path {member!r}.")
            seen.add(member)
            name = entry.get("name") or PurePosixPath(member).name
            if not isinstance(name, str):
                raise RescalError("Fit names must be text.")
            try:
                text = _read_member(z, member, budget).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RescalError(f"Fit {name!r} is not UTF-8 text.") from exc
            freqs, db = _parse_fit_text(text, name)
            fits.append(Fit(_DIR_SIDES[side_dir], name, freqs, db))
    _check_sides(f.side for f in fits)
    return Calibration(manifest, fits)


# ── public manifest (de)serialisation (admin customer library) ──────────────

def manifest_to_dict(manifest: Manifest) -> dict:
    """The manifest as plain JSON-ready data (same shape as in the .rescal file)."""
    return asdict(manifest)


def manifest_from_dict(data) -> Manifest:
    """Parse and validate manifest data. Raises :class:`RescalError`."""
    return _manifest_from_json(data)
