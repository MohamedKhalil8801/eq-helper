"""TuningSession: Qt-free customer choices -> per-channel EQ, with snapshots."""

import numpy as np
import pytest

from eq_helper.calibration.tuning import Tuning, TuningSession, target_hash
from eq_helper.dsp.resonance import CONTROL_RANGE, Measurements, match_channels


@pytest.fixture(scope="module")
def bundled():
    return Measurements.bundled()


@pytest.fixture
def diamond():
    f = np.geomspace(20, 20000, 384)
    return f, 4 * np.log10(f / 1000) - 3 * np.exp(-0.5 * (np.log(f / 3000) / .3) ** 2)


def test_defaults(bundled):
    s = TuningSession(bundled)
    assert s.primary == {side: 7700.0 for side in bundled.models}
    assert s.upper == {} and s.upper_enabled is False
    assert s.target is None
    with pytest.raises(ValueError, match="target"):
        s.channel_eq()


def test_session_generates_same_eq_as_match_channels(bundled, diamond):
    s = TuningSession(bundled)
    s.set_primary("L", 7600)
    s.set_primary("R", 8100)
    s.set_target(*diamond, name="PEQdB Diamond")
    eq = s.channel_eq()
    ref = match_channels(bundled, {"L": 7600, "R": 8100}, *diamond, "PEQdB Diamond")
    assert eq.target_name == "PEQdB Diamond" and eq.preamp_db == ref.preamp_db
    for side in ("L", "R"):
        np.testing.assert_array_equal(eq.channels[side].correction_db, ref.channels[side].correction_db)


def test_upper_resonance_off_by_default_and_per_side(bundled, diamond):
    s = TuningSession(bundled)
    s.set_target(*diamond, name="D")
    s.set_upper("L", 13500)
    assert s.upper == {"L": 13500.0}
    assert s.channel_eq().personal_treble_enabled is False  # remembered, not applied
    s.upper_enabled = True
    eq = s.channel_eq()
    ref = match_channels(bundled, s.primary, *diamond, "D", personal_treble={"L": 13500.0})
    assert eq.personal_treble_enabled
    np.testing.assert_array_equal(eq.channels["L"].correction_db, ref.channels["L"].correction_db)
    np.testing.assert_array_equal(eq.channels["R"].correction_db, ref.channels["R"].correction_db)
    with pytest.raises(ValueError, match="10,500"):
        s.set_upper("R", 9000)
    s.clear_upper("L")
    assert s.upper == {}
    with pytest.raises(ValueError, match="side"):
        s.set_primary("X", 7000)


def test_snapshot_and_restore_round_trip(bundled, diamond):
    s = TuningSession(bundled)
    s.set_target(*diamond, name="D", path="/t/D.txt")
    s.set_primary("L", 7100)
    s.set_upper("R", 14000)
    s.upper_enabled = True
    snap = s.snapshot()
    assert isinstance(snap, Tuning)
    assert snap.target_hash == target_hash(*diamond)
    assert Tuning.from_dict(snap.to_dict()) == snap
    expected = s.channel_eq()

    s.set_primary("L", 8800)
    s.upper_enabled = False
    s.set_target(np.array([20., 1000, 20000]), np.zeros(3), name="flat")
    with pytest.raises(ValueError, match="curve"):
        s.restore(snap)  # target changed and no curve supplied
    s.restore(snap, target=diamond)
    assert s.snapshot() == snap
    np.testing.assert_array_equal(s.channel_eq().channels["L"].correction_db,
                                  expected.channels["L"].correction_db)
    # Restoring while the same target is loaded needs no curve.
    s.set_primary("R", 6500)
    s.restore(snap)
    assert s.primary == snap.primary
    with pytest.raises(ValueError, match="does not match"):
        s.restore(snap, target=(diamond[0], diamond[1] + 1))
    other = Tuning({"M": 7000.0}, {}, False, "D", None, snap.target_hash)
    with pytest.raises(ValueError, match="sides"):
        s.restore(other)


def test_primary_clamped_to_control_range(bundled):
    s = TuningSession(bundled)
    s.set_primary("L", 100)
    s.set_primary("R", 99999)
    assert s.primary == {"L": float(CONTROL_RANGE[0]), "R": float(CONTROL_RANGE[1])}
    with pytest.raises(ValueError):
        s.set_primary("L", float("nan"))


def test_invalid_target_rejected_and_previous_kept(bundled, diamond):
    s = TuningSession(bundled)
    s.set_target(*diamond, name="D")
    with pytest.raises(ValueError, match="1 kHz"):
        s.set_target(np.array([2000., 20000.]), np.zeros(2), name="bad")
    assert s.target_name == "D"


def test_new_measurements_keep_primary_for_shared_sides(bundled):
    s = TuningSession(bundled)
    s.set_primary("L", 7000)
    s.set_measurements(bundled, primary={"L": 7200})
    assert s.primary == {"L": 7200.0, "R": 7700.0}
