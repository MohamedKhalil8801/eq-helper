"""Predicted coupler response export: 1/48-oct REW text for squig.link / PEQdB."""

import numpy as np
import pytest

from eq_helper.dsp.overlay import load_frequency_response
from eq_helper.dsp.predicted_export import (
    LEVEL_AT_1K_DB, grid_1_48_octave, predicted_curves, resample_log, write_predicted,
)
from eq_helper.dsp.resonance import Measurements, interp, match_channels

META = {"Customer": "Ada", "IEM": "Truthear Nova", "Date": "2026-10-27"}


@pytest.fixture(scope="module")
def eq():
    f = np.geomspace(20, 20000, 384)
    return match_channels(Measurements.bundled(), {"L": 7600, "R": 8100}, f, -2 * np.log10(f / 1000), "D")


def test_grid_is_exact_one_48th_octave():
    g = grid_1_48_octave()
    assert g[0] == 20 and g[-1] == pytest.approx(20000, rel=1e-12)
    np.testing.assert_allclose(np.diff(np.log2(g)), np.log2(1000) / (len(g) - 1))
    assert abs(np.diff(np.log2(g))[0] - 1 / 48) < 1e-3
    assert len(g) == 479


def test_resample_is_log_linear_without_smoothing():
    f = np.array([100.0, 1000.0, 10000.0])
    y = np.array([0.0, 10.0, 0.0])
    out = resample_log(f, y, np.array([100.0, np.sqrt(1000 * 100), 1000.0, 20000.0]))
    np.testing.assert_allclose(out, [0, 5, 10, 0])


def test_writes_l_and_r_with_header_and_round_trip(eq, tmp_path):
    paths = write_predicted(eq, tmp_path, "Truthear Nova", META)
    assert [p.name for p in paths] == ["Truthear Nova L.txt", "Truthear Nova R.txt"]
    text = paths[0].read_text(encoding="utf-8")
    header = [line for line in text.splitlines() if line.startswith("*")]
    assert any("Customer: Ada" in line for line in header)
    assert any("Primary resonance: 7600 Hz" in line for line in header)
    curves = predicted_curves(eq)
    for path, side in zip(paths, ("L", "R")):
        f, db = load_frequency_response(path)
        assert len(f) == 479
        np.testing.assert_allclose(f, grid_1_48_octave(), rtol=1e-6)
        assert np.max(np.abs(db - curves[side][1])) < 0.05
        # The file is the model's prediction, not a smoothed version of it.
        ch = eq.channels[side]
        expected = interp(ch.freqs, ch.predicted_db, f) + LEVEL_AT_1K_DB
        assert np.max(np.abs(db - expected)) < 0.05


def test_left_right_level_difference_preserved(eq, tmp_path):
    left, right = (load_frequency_response(p) for p in write_predicted(eq, tmp_path, "X", META))
    ch = eq.channels
    want = interp(ch["R"].freqs, ch["R"].predicted_db, [1000.0])[0] - interp(ch["L"].freqs, ch["L"].predicted_db, [1000.0])[0]
    got = np.interp(np.log(1000), np.log(right[0]), right[1]) - np.interp(np.log(1000), np.log(left[0]), left[1])
    assert got == pytest.approx(want, abs=0.01)


def test_with_eq_option_names_and_values(eq, tmp_path):
    paths = write_predicted(eq, tmp_path, "Nova: 2/3", META, with_eq=True)
    assert [p.name for p in paths] == ["Nova- 2-3 L with EQ.txt", "Nova- 2-3 R with EQ.txt"]
    f, db = load_frequency_response(paths[0])
    ch = eq.channels["L"]
    expected = interp(ch.freqs, ch.predicted_db + ch.correction_db, f) + LEVEL_AT_1K_DB
    assert np.max(np.abs(db - expected)) < 0.05


def test_shared_single_set_writes_one_file(tmp_path):
    f = np.geomspace(20, 20000, 200)
    data = Measurements.bundled()
    single = Measurements({"M": data.models["L"]}, "one")
    eq = match_channels(single, {"M": 7700}, f, np.zeros_like(f), "flat")
    assert [p.name for p in write_predicted(eq, tmp_path, "Nova", META)] == ["Nova.txt"]
