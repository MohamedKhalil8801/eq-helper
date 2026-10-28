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
    for hz in (7000, 7100, 7200):
        s.set_primary("L", hz)
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


def test_the_same_tuning_is_kept_once_and_moves_to_the_top(tmp_path, bundled):
    store = HistoryStore(tmp_path, "cal-1")
    s = _session(bundled)
    s.set_primary("L", 7400)
    first = store.save(s.snapshot(), s.target, "manual", note="bright")
    s.set_primary("L", 7000)
    other = store.save(s.snapshot(), s.target, "export", files=["b.txt"])
    s.set_primary("L", 7400)
    again = store.save(s.snapshot(), s.target, "export", files=["a.txt"], app="wavelet")
    assert again.id == first.id and len(store.entries()) == 2
    assert [e.id for e in store.entries()] == [first.id, other.id]  # most recently used first
    assert (again.note, again.files, again.app, again.used_reason) == ("bright", ["a.txt"], "wavelet", "export")
    assert again.date == first.date and again.used >= first.date
    s.set_target(*_target(-1.0), name="Diamond", path="/gone/Diamond.txt")  # another curve: another tuning
    assert store.find(s.snapshot(), s.target) is None
    renamed = store.save(store.get(first.id).tuning, (first.target_freqs, first.target_db), "manual", note="warm")
    assert renamed.id == first.id and renamed.note == "warm"  # saving again with a name renames it


def test_applying_marks_the_tuning_in_use_and_survives_restart(tmp_path, bundled):
    store = HistoryStore(tmp_path, "cal-1")
    s = _session(bundled)
    a = store.save(s.snapshot(), s.target, "apply")
    s.set_primary("R", 8300)
    b = store.save(s.snapshot(), s.target, "apply")
    assert store.in_use == b.id
    s.set_primary("R", store.get(a.id).tuning.primary["R"])
    store.save(s.snapshot(), s.target, "apply")  # switching back: no new entry
    assert len(store.entries()) == 2 and HistoryStore(tmp_path, "cal-1").in_use == a.id
    store.save(s.snapshot(), s.target, "export")
    assert store.in_use == a.id  # exporting doesn't change what's in use
    store.delete(a.id)
    assert store.in_use is None


def test_old_logs_are_merged_into_one_entry_per_tuning(tmp_path, bundled):
    s = _session(bundled)
    s.set_primary("L", 7400)
    t1 = s.snapshot()
    s.set_primary("L", 7000)
    t2 = s.snapshot()
    f, db = s.target

    def raw(i, tuning, reason, note="", files=()):
        return {"id": f"e{i}", "date": f"2026-10-2{i}T10:00:00", "reason": reason, "tuning": tuning.to_dict(),
                "target": {"name": "Diamond", "freqs": list(f), "db": list(db)}, "note": note,
                "files": list(files), "app": None}
    old = {"version": 1, "calibration_id": "cal-1", "entries": [
        raw(1, t1, "manual", "bright"), raw(2, t2, "export", files=["x.txt"]), raw(3, t1, "apply", files=["apo.txt"]),
        raw(4, t1, "apply", files=["apo.txt"]), raw(5, t2, "export", files=["y.txt"])]}
    (tmp_path / "cal-1.json").write_text(json.dumps(old))
    entries = HistoryStore(tmp_path, "cal-1").entries()
    assert [e.id for e in entries] == ["e2", "e1"]  # used last on the 25th and the 24th
    two, one = entries
    assert two.files == ["x.txt", "y.txt"] and two.used.startswith("2026-10-25")
    assert (one.note, one.files, one.date, one.used_reason) == ("bright", ["apo.txt"], "2026-10-21T10:00:00", "apply")
    data = json.loads((tmp_path / "cal-1.json").read_text())
    assert data["version"] == 2 and len(data["entries"]) == 2
