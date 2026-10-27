"""The EQ app catalog: which apps exist where, and what each one accepts (ADR 0004).

Apps are data in ``catalog.json``; export formats are code. Adding an app
that uses an existing format is a catalog edit only. Every claim about an app
(L/R support, limits, import steps) carries ``verified: false`` until checked.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from eq_helper.dsp.resonance import ChannelEQ

from .formats import get_format, registered_formats
from .formats.base import ExportMeta, shared_only

_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
DEFAULT_CATALOG_PATH = _ROOT / "eq_helper" / "exports" / "catalog.json"

PLATFORMS = ("windows", "macos", "linux", "android", "ios")
DEVICE_KINDS = ("computer", "phone", "hardware", "other")
PRICES = ("free", "paid", "freemium")
SCOPES = ("system", "player")
LR_SUPPORT = ("full", "shared")
LISTENING = ("this_computer", "other_computer", "phone", "hardware")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class AppFormat:
    format: str
    options: dict
    label: str = ""


@dataclass(frozen=True)
class CatalogApp:
    id: str
    name: str
    description: str
    platforms: tuple[str, ...]
    device_kind: str
    links: dict
    price: str
    scope: str
    lr: str
    balance_control: bool
    formats: tuple[AppFormat, ...]
    setup_steps: tuple[str, ...]
    detection: dict = field(default_factory=dict)
    verified: bool = False
    advanced: bool = False

    @property
    def lr_label(self) -> str:
        return "Left/right correction: full" if self.lr == "full" else "Left/right correction: shared only"


def _need(entry, key, kind, where):
    if key not in entry or not isinstance(entry[key], kind):
        raise CatalogError(f"{where}: '{key}' is missing or not {getattr(kind, '__name__', kind)}.")
    return entry[key]


def _choice(value, choices, what, where):
    if value not in choices:
        raise CatalogError(f"{where}: {what} {value!r} is not one of {choices}.")
    return value


def _parse_app(entry, known_formats) -> CatalogApp:
    where = f"catalog app {entry.get('id', '?')!r}"
    app_id = _need(entry, "id", str, where)
    platforms = tuple(_need(entry, "platforms", list, where))
    for p in platforms:
        _choice(p, PLATFORMS, "platform", where)
    formats = []
    for raw in _need(entry, "formats", list, where):
        fid = _need(raw, "format", str, where)
        if fid not in known_formats:
            raise CatalogError(f"{where}: export format {fid!r} has no exporter (known: {known_formats}).")
        formats.append(AppFormat(fid, dict(raw.get("options") or {}), str(raw.get("label", ""))))
    if not formats:
        raise CatalogError(f"{where}: needs at least one export format.")
    links = _need(entry, "links", dict, where)
    return CatalogApp(
        id=app_id, name=_need(entry, "name", str, where), description=_need(entry, "description", str, where),
        platforms=platforms, device_kind=_choice(entry.get("device_kind"), DEVICE_KINDS, "device_kind", where),
        links=dict(links), price=_choice(entry.get("price"), PRICES, "price", where),
        scope=_choice(entry.get("scope"), SCOPES, "scope", where),
        lr=_choice(entry.get("lr"), LR_SUPPORT, "lr", where),
        balance_control=bool(_need(entry, "balance_control", bool, where)),
        formats=tuple(formats), setup_steps=tuple(_need(entry, "setup_steps", list, where)),
        detection=dict(entry.get("detection") or {}), verified=bool(entry.get("verified", False)),
        advanced=bool(entry.get("advanced", False)),
    )


@dataclass(frozen=True)
class Catalog:
    apps: tuple[CatalogApp, ...]

    def get(self, app_id: str) -> CatalogApp:
        for app in self.apps:
            if app.id == app_id:
                return app
        raise KeyError(app_id)

    def for_listening(self, where: str, os_name: str | None = None, installed=frozenset()) -> list[CatalogApp]:
        """Apps for "Where will you listen?", best first.

        ``where``: this_computer / other_computer (needs ``os_name``), phone
        (``os_name`` android or ios), hardware. Installed apps first, then
        full L/R before shared-only, then by name; "Other / Advanced" last.
        """
        _choice(where, LISTENING, "listening device", "for_listening")
        if where in ("this_computer", "other_computer"):
            kinds = ("computer",)
            _choice(os_name, ("windows", "macos", "linux"), "computer OS", "for_listening")
        elif where == "phone":
            kinds = ("phone",)
            _choice(os_name, ("android", "ios"), "phone OS", "for_listening")
        else:
            kinds = ("hardware",)
        picked = [a for a in self.apps
                  if a.device_kind == "other"
                  or (a.device_kind in kinds and (where == "hardware" or os_name in a.platforms))]
        return sorted(picked, key=lambda a: (a.device_kind == "other", a.id not in installed,
                                             a.lr != "full", a.name.casefold()))


def parse_catalog(data) -> Catalog:
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        raise CatalogError("Catalog must be an object with an 'apps' list.")
    known = registered_formats()
    apps = tuple(_parse_app(entry, known) for entry in data["apps"])
    ids = [a.id for a in apps]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CatalogError(f"Duplicate catalog app ids: {dupes}.")
    return Catalog(apps)


def load_catalog(path=None) -> Catalog:
    path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"Could not read the app catalog {path}: {exc}") from exc
    return parse_catalog(data)


@dataclass(frozen=True)
class AppExport:
    files: list[Path]
    notes: tuple[str, ...]
    balance_db: float | None


def resolve_options(options: dict, device_rate: int | None) -> dict:
    """Fill ``"device"`` placeholders with the output device's sample rate."""
    out = dict(options)
    if out.get("sample_rate") == "device":
        out["sample_rate"] = int(device_rate or 48000)
    return out


def export_for_app(app: CatalogApp, channel_eq: ChannelEQ, folder, base_name: str, meta: ExportMeta,
                   format_index: int = 0, overrides: dict | None = None,
                   device_rate: int | None = None) -> AppExport:
    """Export ``channel_eq`` the way ``app`` takes it (shared-only reduced if needed)."""
    choice = app.formats[format_index]
    eq, notes, balance = channel_eq, (), None
    if app.lr == "shared":
        reduced = shared_only(channel_eq, app.balance_control)
        eq, notes, balance = reduced.channel_eq, reduced.notes, reduced.balance_db
    options = resolve_options({**choice.options, **(overrides or {})}, device_rate)
    meta = ExportMeta(meta.customer, meta.iem, meta.date, meta.tuning, tuple(meta.notes) + tuple(notes))
    files = get_format(choice.format).export(eq, options, Path(folder), base_name, meta)
    return AppExport(files, notes, balance)
