""".rescal calibration file: round trip, versioning and untrusted-archive safety."""

import json
import zipfile
from dataclasses import replace

import numpy as np
import pytest

from eq_helper.calibration.rescal import (
    FORMAT_VERSION, RESCAL_SUFFIX, Accessory, Calibration, Customer, Iem,
    Manifest, Operator, RescalError, SongLink, read_rescal, write_rescal,
)
from eq_helper.dsp.resonance import Fit


def _fit(side, i, peak, offset=0.0):
    f = np.unique(np.r_[np.geomspace(20, 20000, 600), 1000])
    y = 75 + offset + 9 * np.exp(-0.5 * (np.log(f / peak) / .075) ** 2) + 0.123456789 * i
    return Fit(side, f"{side} fit {i}.txt", f, y)


@pytest.fixture
def sample_calibration():
    manifest = Manifest(
        customer=Customer("Ada Lovelace", "ada@example.com"),
        iem=Iem("Truthear", "Nova", "SN-1"),
        accessories=[Accessory("ear tips", "Spinfit W1 M", "customer"),
                     Accessory("dac", "Apple dongle", "operator")],
        coupler="IEC-711 clone",
        notes="Deep fit preferred.",
        default_target="PEQdB Diamond.txt",
        test_song_links=[SongLink("Song", "https://example.com/song")],
        operator=Operator("Mohamed Khalil"),
        created="2026-10-27",
    )
    fits = [_fit(s, i, p, 2 if s == "R" else 0)
            for s in ("L", "R") for i, p in enumerate([6500, 7300, 8100, 8900])]
    return Calibration(manifest, fits)


def _rewrite_manifest(path, mutate):
    with zipfile.ZipFile(path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    data = json.loads(entries["manifest.json"])
    mutate(data)
    entries["manifest.json"] = json.dumps(data).encode()
    with zipfile.ZipFile(path, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)


def test_round_trip(tmp_path, sample_calibration):
    p = write_rescal(tmp_path / "x", sample_calibration)
    assert p.suffix == RESCAL_SUFFIX == ".rescal"
    back = read_rescal(p)
    assert back.manifest == sample_calibration.manifest
    assert [(f.side, f.name) for f in back.fits] == [(f.side, f.name) for f in sample_calibration.fits]
    for a, b in zip(back.fits, sample_calibration.fits):
        np.testing.assert_allclose(a.freqs, b.freqs, rtol=1e-12)
        np.testing.assert_allclose(a.db, b.db, rtol=1e-12)
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
    assert "manifest.json" in names
    assert any(n.startswith("fits/L/") for n in names) and any(n.startswith("fits/R/") for n in names)
    # Fits are plain text that REW-style loaders understand.
    with zipfile.ZipFile(p) as z:
        text = z.read(next(n for n in names if n.startswith("fits/L/"))).decode()
    assert text.splitlines()[0].startswith("*")


def test_measurements_trains_from_calibration(sample_calibration):
    m = sample_calibration.measurements()
    assert set(m.models) == {"L", "R"}
    assert "Truthear Nova" in m.name


def test_shared_single_set_round_trips(tmp_path, sample_calibration):
    fits = [replace(f, side=None) for f in sample_calibration.fits if f.side == "L"]
    p = write_rescal(tmp_path / "m.rescal", Calibration(sample_calibration.manifest, fits))
    with zipfile.ZipFile(p) as z:
        assert all(not n.startswith("fits/") or n.startswith("fits/M/") for n in z.namelist())
    assert {f.side for f in read_rescal(p).fits} == {None}


def test_newer_minor_version_loads_unknown_keys_ignored(tmp_path, sample_calibration):
    p = write_rescal(tmp_path / "x.rescal", sample_calibration)
    major = FORMAT_VERSION.split(".")[0]

    def mutate(d):
        d["format_version"] = f"{major}.99"
        d["future_field"] = {"anything": 1}
        d["customer"]["future"] = "x"
    _rewrite_manifest(p, mutate)
    back = read_rescal(p)
    assert back.manifest.customer == sample_calibration.manifest.customer
    assert back.manifest.calibration_id == sample_calibration.manifest.calibration_id


def test_newer_major_version_raises_clear_error(tmp_path, sample_calibration):
    p = write_rescal(tmp_path / "x.rescal", sample_calibration)
    _rewrite_manifest(p, lambda d: d.update(format_version="2.0"))
    with pytest.raises(RescalError, match="newer version"):
        read_rescal(p)
    _rewrite_manifest(p, lambda d: d.update(format_version="banana"))
    with pytest.raises(RescalError, match="version"):
        read_rescal(p)


def test_requires_both_sides_or_single_shared_set(tmp_path, sample_calibration):
    m = sample_calibration.manifest
    only_left = [f for f in sample_calibration.fits if f.side == "L"]
    with pytest.raises(RescalError, match="both"):
        write_rescal(tmp_path / "a.rescal", Calibration(m, only_left))
    mixed = only_left + [replace(only_left[0], side=None, name="shared")]
    with pytest.raises(RescalError, match="both"):
        write_rescal(tmp_path / "b.rescal", Calibration(m, mixed))
    with pytest.raises(RescalError, match="fits"):
        write_rescal(tmp_path / "c.rescal", Calibration(m, []))
    # The reader enforces the same rule on files written by anything else.
    p = write_rescal(tmp_path / "ok.rescal", sample_calibration)
    _rewrite_manifest(p, lambda d: d.update(fits=[e for e in d["fits"] if e["side"] == "L"]))
    with pytest.raises(RescalError, match="both"):
        read_rescal(p)


def test_rejects_zip_slip_paths(tmp_path, sample_calibration):
    p = write_rescal(tmp_path / "x.rescal", sample_calibration)
    for evil in ("../evil.txt", "/abs/evil.txt", "fits/L/../../evil.txt", "C:/evil.txt", "fits\\L\\x.txt"):
        def mutate(d, evil=evil):
            d["fits"][0]["path"] = evil
        q = tmp_path / "evil.rescal"
        q.write_bytes(p.read_bytes())
        _rewrite_manifest(q, mutate)
        with pytest.raises(RescalError, match="path"):
            read_rescal(q)


def test_rejects_non_zip_oversized_and_malformed(tmp_path, sample_calibration, monkeypatch):
    bad = tmp_path / "bad.rescal"
    bad.write_text("not a zip")
    with pytest.raises(RescalError, match="not a calibration file"):
        read_rescal(bad)
    p = write_rescal(tmp_path / "x.rescal", sample_calibration)
    import eq_helper.calibration.rescal as rescal
    monkeypatch.setattr(rescal, "MAX_ENTRY_BYTES", 100)
    with pytest.raises(RescalError, match="too large"):
        read_rescal(p)
    monkeypatch.undo()
    _rewrite_manifest(p, lambda d: d["accessories"][0].update(supplied_by="someone"))
    with pytest.raises(RescalError, match="supplied_by"):
        read_rescal(p)
    _rewrite_manifest(p, lambda d: d.pop("iem"))
    with pytest.raises(RescalError, match="iem"):
        read_rescal(p)


def test_id_is_stable_across_rewrites(tmp_path, sample_calibration):
    first = write_rescal(tmp_path / "a.rescal", sample_calibration)
    again = write_rescal(tmp_path / "b.rescal", read_rescal(first))
    assert read_rescal(again).manifest.calibration_id == sample_calibration.manifest.calibration_id
    other = Manifest(customer=Customer("B"), iem=Iem("X", "Y"))
    assert other.calibration_id != sample_calibration.manifest.calibration_id
    assert len(other.calibration_id) == 36
