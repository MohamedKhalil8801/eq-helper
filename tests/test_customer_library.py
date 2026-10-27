"""Admin customer library: records of generated calibrations + operator accessories."""

import json
from datetime import date

import pytest

from eq_helper.admin.customer_library import CalibrationRecord, CustomerLibrary, MeasurementSource
from eq_helper.calibration.rescal import Accessory, Customer, Iem, Manifest, Operator, SongLink


def _manifest(name="Ada", **kw):
    return Manifest(customer=Customer(name, "ada@example.com"), iem=Iem("Truthear", "Nova", "SN1"),
                    accessories=[Accessory("tips", "Spinfit W1 M", "customer"),
                                 Accessory("dac", "Apple dongle", "operator")],
                    coupler="IEC-711 clone", notes="deep fit", operator=Operator("Mohamed"),
                    test_song_links=[SongLink("Treble", "https://x.example")], created="2026-10-01", **kw)


def test_save_and_load_record(tmp_path):
    lib = CustomerLibrary(tmp_path)
    rec = lib.save(_manifest(), MeasurementSource.from_folder(tmp_path / "meas"),
                   outputs=[str(tmp_path / "out/Ada.zip")], targets=["PEQdB Diamond.txt"], systems=["windows"])
    again = CustomerLibrary(tmp_path)
    loaded = again.get(rec.record_id)
    assert loaded.manifest == rec.manifest
    assert loaded.source == MeasurementSource("folder", str(tmp_path / "meas"))
    assert loaded.outputs == [str(tmp_path / "out/Ada.zip")]
    assert loaded.systems == ["windows"] and loaded.targets == ["PEQdB Diamond.txt"]
    assert [r.record_id for r in again.records()] == [rec.record_id]


def test_customers_listed_newest_first_and_last_record(tmp_path):
    lib = CustomerLibrary(tmp_path)
    lib.save(_manifest("Ada"), MeasurementSource.from_folder("a"))
    lib.save(_manifest("Bob"), MeasurementSource.from_sides("l", "r"))
    assert [c.name for c in lib.customers()] == ["Bob", "Ada"]
    assert lib.last_record().manifest.customer.name == "Bob"
    assert [r.manifest.customer.name for r in lib.records_for("Ada")] == ["Ada"]


def test_operator_accessories_remembered_deduplicated_last_used_first(tmp_path):
    lib = CustomerLibrary(tmp_path)
    lib.remember_accessories([Accessory("dac", "Apple dongle", "operator"),
                              Accessory("tips", "Spinfit W1 M", "customer")])
    lib.remember_accessories([Accessory("dac", "Moondrop Dawn Pro", "operator")])
    lib.remember_accessories([Accessory("dac", " apple dongle ", "operator")])
    again = CustomerLibrary(tmp_path)
    assert again.operator_accessories("dac") == ["apple dongle", "Moondrop Dawn Pro"]
    assert again.last_used("dac") == "apple dongle"
    assert again.operator_accessories("tips") == []  # customer's own tips aren't operator kit
    assert again.last_used("cable") is None


def test_regenerate_identical_except_id_and_date_when_asked(tmp_path):
    lib = CustomerLibrary(tmp_path)
    rec = lib.save(_manifest(), MeasurementSource.from_folder("m"))
    same = lib.regenerate_manifest(rec, new_calibration=False)
    assert same == rec.manifest
    fresh = lib.regenerate_manifest(rec, new_calibration=True)
    assert fresh.calibration_id != rec.manifest.calibration_id
    assert fresh.created == date.today().isoformat()
    fresh.calibration_id, fresh.created = rec.manifest.calibration_id, rec.manifest.created
    assert fresh == rec.manifest


def test_corrupt_files_do_not_break_the_library(tmp_path):
    lib = CustomerLibrary(tmp_path)
    rec = lib.save(_manifest(), MeasurementSource.from_folder("m"))
    (lib.records_dir / "broken.json").write_text("{nope", encoding="utf-8")
    (lib.root / "accessories.json").write_text("[]", encoding="utf-8")
    again = CustomerLibrary(tmp_path)
    assert [r.record_id for r in again.records()] == [rec.record_id]
    assert again.operator_accessories("dac") == []


def test_default_root_is_never_inside_the_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    lib = CustomerLibrary()
    assert lib.root == tmp_path / ".eq_helper" / "customer_library"
