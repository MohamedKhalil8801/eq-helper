"""Tuning history: per calibration, persistent, reapplies even without the target file."""

import json

import numpy as np
import pytest

from eq_helper.calibration.tuning import TuningSession
from eq_helper.dsp.resonance import Measurements
from resonancefit.history import HistoryStore


@pytest.fixture(scope="module")
def bundled():
    return Measurements.bundled()


def _target(tilt):
    f = np.geomspace(20, 20000, 384)
    return f, tilt * np.log10(f / 1000)


def _session(bundled, tilt=-2.0, name="Diamond"):
    s = TuningSession(bundled)
    s.set_target(*_target(tilt), name=name, path=f"/gone/{name}.txt")
    return s


def test_entries_only_created_by_save_and_survive_restart(tmp_path, bundled):
    store = HistoryStore(tmp_path, "cal-1")
    assert store.entries() == []
    s = _session(bundled)
    s.set_primary("L", 7400)
    e1 = store.save(s.snapshot(), s.target, "export", note="first", files=["a.txt"], app="equalizer-apo")
    s.set_primary("R", 8300)
    e2 = store.save(s.snapshot(), s.target, "apply")
    again = HistoryStore(tmp_path, "cal-1").entries()
    assert [e.id for e in again] == [e2.id, e1.id]  # newest first
    first = again[1]
    assert (first.reason, first.note, first.files, first.app) == ("export", "first", ["a.txt"], "equalizer-apo")
    assert first.tuning.primary["L"] == 7400 and first.target_name == "Diamond"
    with pytest.raises(ValueError, match="reason"):
        store.save(s.snapshot(), s.target, "autosave")


def test_history_is_per_calibration(tmp_path, bundled):
    s = _session(bundled)
    HistoryStore(tmp_path, "cal-1").save(s.snapshot(), s.target, "manual")
    assert HistoryStore(tmp_path, "cal-2").entries() == []
    with pytest.raises(ValueError):
        HistoryStore(tmp_path, "../escape")


def test_reapply_restores_session_even_when_target_file_is_gone(tmp_path, bundled):
    s = _session(bundled, tilt=-3.0)
    s.set_primary("L", 7100)
    s.set_upper("R", 14000)
    s.upper_enabled = True
    expected = s.channel_eq()
    entry = HistoryStore(tmp_path, "cal-1").save(s.snapshot(), s.target, "manual", note="warm")

    fresh = TuningSession(bundled)
    fresh.set_target(*_target(1.0), name="Other")
    tuning, curve = HistoryStore(tmp_path, "cal-1").reapply(entry.id)
    fresh.restore(tuning, target=curve)
    assert fresh.snapshot() == s.snapshot()
    got = fresh.channel_eq()
    for side in ("L", "R"):
        np.testing.assert_allclose(got.channels[side].correction_db, expected.channels[side].correction_db)
    with pytest.raises(KeyError):
        HistoryStore(tmp_path, "cal-1").reapply("missing")


def test_atomic_write_leaves_no_temp_files(tmp_path, bundled):
    s = _session(bundled)
    store = HistoryStore(tmp_path, "cal-1")
    for _ in range(3):
        store.save(s.snapshot(), s.target, "manual")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cal-1.json"]
    data = json.loads((tmp_path / "cal-1.json").read_text())
    assert data["calibration_id"] == "cal-1" and len(data["entries"]) == 3


def test_corrupt_file_backed_up_and_history_starts_empty(tmp_path, bundled):
    (tmp_path / "cal-1.json").write_text("{not json")
    store = HistoryStore(tmp_path, "cal-1")
    assert store.entries() == []
    backups = list(tmp_path.glob("cal-1.corrupt-*.json"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"
    s = _session(bundled)
    store.save(s.snapshot(), s.target, "manual")
    assert len(HistoryStore(tmp_path, "cal-1").entries()) == 1


def test_bad_entries_are_skipped_not_fatal(tmp_path, bundled):
    s = _session(bundled)
    store = HistoryStore(tmp_path, "cal-1")
    store.save(s.snapshot(), s.target, "manual")
    data = json.loads((tmp_path / "cal-1.json").read_text())
    data["entries"].append({"id": "broken"})
    (tmp_path / "cal-1.json").write_text(json.dumps(data))
    assert len(HistoryStore(tmp_path, "cal-1").entries()) == 1


def test_delete_and_note(tmp_path, bundled):
    s = _session(bundled)
    store = HistoryStore(tmp_path, "cal-1")
    e = store.save(s.snapshot(), s.target, "manual")
    store.set_note(e.id, "bright")
    assert HistoryStore(tmp_path, "cal-1").entries()[0].note == "bright"
    store.delete(e.id)
    assert HistoryStore(tmp_path, "cal-1").entries() == []
