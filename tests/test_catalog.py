"""The EQ app catalog is data: validated, filterable, and needs no code per app."""

import json

import numpy as np
import pytest

from eq_helper.dsp.resonance import Measurements, match_channels
from eq_helper.exports.catalog import (
    DEFAULT_CATALOG_PATH, CatalogError, export_for_app, load_catalog, parse_catalog,
)
from eq_helper.exports.formats import registered_formats
from eq_helper.exports.formats.base import ExportMeta

SEED = {"equalizer-apo", "peace", "easyeffects", "camilladsp", "foobar2000", "roon", "eqmac",
        "soundsource", "wavelet", "rootlessjamesdsp", "poweramp-eq", "neutron", "uapp",
        "qudelix-5k", "other"}
META = ExportMeta("Ada", "Truthear Nova", "2026-10-27", "L 7600 / R 8100")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def eq():
    f = np.geomspace(20, 20000, 384)
    return match_channels(Measurements.bundled(), {"L": 7600, "R": 8100}, f, -2 * np.log10(f / 1000), "D")


def test_bundled_catalog_validates_and_has_seed_entries(catalog):
    assert {a.id for a in catalog.apps} == SEED
    for app in catalog.apps:
        assert app.verified is False  # nothing is claimed as checked yet
        assert app.setup_steps and app.description
        for fmt in app.formats:
            assert fmt.format in registered_formats()
    assert DEFAULT_CATALOG_PATH.name == "catalog.json"


def test_validation_errors_name_the_entry():
    good = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["apps"][0]["formats"][0]["format"] = "laserdisc"
    with pytest.raises(CatalogError, match="equalizer-apo.*laserdisc"):
        parse_catalog(bad)
    bad = json.loads(json.dumps(good))
    bad["apps"][1]["lr"] = "half"
    with pytest.raises(CatalogError, match="peace"):
        parse_catalog(bad)
    bad = json.loads(json.dumps(good))
    bad["apps"].append(bad["apps"][0])
    with pytest.raises(CatalogError, match="Duplicate"):
        parse_catalog(bad)


def test_filter_by_listening_device_and_os(catalog):
    win = [a.id for a in catalog.for_listening("this_computer", "windows")]
    assert "equalizer-apo" in win and "foobar2000" in win and "easyeffects" not in win
    assert win[-1] == "other"
    linux = [a.id for a in catalog.for_listening("other_computer", "linux")]
    assert "easyeffects" in linux and "equalizer-apo" not in linux
    ios = [a.id for a in catalog.for_listening("phone", "ios")]
    assert "neutron" in ios and "wavelet" not in ios
    assert [a.id for a in catalog.for_listening("hardware")] == ["qudelix-5k", "other"]
    with pytest.raises(CatalogError):
        catalog.for_listening("phone", "windows")


def test_full_lr_before_shared_and_installed_first(catalog):
    android = catalog.for_listening("phone", "android")
    kinds = [a.lr for a in android if a.id != "other"]
    assert kinds == sorted(kinds, key=lambda lr: lr != "full")
    installed = catalog.for_listening("this_computer", "macos", installed={"soundsource"})
    assert installed[0].id == "soundsource"


@pytest.mark.parametrize("app_id", sorted(SEED))
def test_every_catalog_app_exports_files(catalog, eq, tmp_path, app_id):
    app = catalog.get(app_id)
    for index in range(len(app.formats)):
        out = tmp_path / str(index)
        result = export_for_app(app, eq, out, "Nova", META, format_index=index, device_rate=44100)
        assert result.files and all(p.exists() for p in result.files)
        if app.lr == "shared":
            assert result.notes
            assert any("averaged" in p.read_text(encoding="utf-8", errors="ignore")
                       for p in result.files if p.suffix in (".txt", ".csv"))


def test_device_rate_fills_placeholder(catalog, eq, tmp_path):
    result = export_for_app(catalog.get("equalizer-apo"), eq, tmp_path, "Nova", META,
                            format_index=1, device_rate=44100)
    assert [p.name for p in result.files if p.suffix == ".wav"] == ["Nova 44.1kHz.wav"]


def test_adding_an_app_needs_no_code(eq, tmp_path):
    data = {"version": 1, "apps": [{
        "id": "fake-player", "name": "Fake Player", "description": "Test app.",
        "platforms": ["linux"], "device_kind": "computer", "links": {"site": "https://example.com"},
        "price": "free", "scope": "player", "lr": "shared", "balance_control": True,
        "formats": [{"format": "parametric", "options": {"writer": "csv", "limits": {"max_filters": 4}}}],
        "setup_steps": ["Type the filters in."]}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    catalog = load_catalog(path)
    (app,) = catalog.for_listening("this_computer", "linux")
    result = export_for_app(app, eq, tmp_path / "out", "Nova", META)
    (csv_path,) = result.files
    rows = [l for l in csv_path.read_text().splitlines() if l and not l.startswith("#")]
    assert rows[0] == "side,type,fc_hz,gain_db,q" and {r.split(",")[0] for r in rows[1:]} == {"preamp", "M"}
    assert len(rows) - 2 <= 4
