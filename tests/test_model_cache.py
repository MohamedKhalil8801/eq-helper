"""Trained resonance models are cached per calibration and invalidated safely."""

from dataclasses import replace

import numpy as np
import pytest

from eq_helper.calibration import model_cache
from eq_helper.calibration.model_cache import cache_path, load_measurements
from eq_helper.calibration.rescal import Calibration, Customer, Iem, Manifest
from eq_helper.dsp import resonance
from eq_helper.dsp.resonance import Fit, ResonanceModel


def _calibration():
    f = np.unique(np.r_[np.geomspace(20, 20000, 800), 1000])
    fits = [Fit(s, f"{s}{i}", f, 75 + o + 9 * np.exp(-0.5 * (np.log(f / p) / .075) ** 2))
            for s, o in (("L", 0), ("R", 2)) for i, p in enumerate([6500, 7200, 8000, 8800])]
    return Calibration(Manifest(customer=Customer("A"), iem=Iem("B", "C")), fits)


@pytest.fixture
def train_calls(monkeypatch):
    calls = []
    original = ResonanceModel._train

    def spy(self):
        calls.append(self)
        return original(self)
    monkeypatch.setattr(ResonanceModel, "_train", spy)
    return calls


def _same(a, b):
    assert set(a.models) == set(b.models)
    for side in a.models:
        ma, mb = a.models[side], b.models[side]
        np.testing.assert_array_equal(ma.predict(7700), mb.predict(7700))
        np.testing.assert_array_equal(ma.predict(9000), mb.predict(9000))
        assert (ma.alignment, ma.edge_trend, ma.validation, ma.sources, ma.sample_count) == \
               (mb.alignment, mb.edge_trend, mb.validation, mb.sources, mb.sample_count)
        assert ma.describe(7000) == mb.describe(7000)
    assert a.name == b.name and a.warnings == b.warnings


def test_second_load_skips_training(tmp_path, train_calls):
    cal = _calibration()
    first = load_measurements(cal, tmp_path)
    assert len(train_calls) == 2
    assert cache_path(cal, tmp_path).exists()
    second = load_measurements(cal, tmp_path)
    assert len(train_calls) == 2
    _same(first, second)
    _same(first, cal.measurements())


def test_changed_fits_invalidate_cache(tmp_path, train_calls):
    cal = _calibration()
    load_measurements(cal, tmp_path)
    changed = Calibration(cal.manifest, [replace(cal.fits[0], db=cal.fits[0].db + 0.5), *cal.fits[1:]])
    load_measurements(changed, tmp_path)
    assert len(train_calls) == 4
    load_measurements(changed, tmp_path)
    assert len(train_calls) == 4


def test_model_version_change_invalidates_cache(tmp_path, train_calls, monkeypatch):
    cal = _calibration()
    load_measurements(cal, tmp_path)
    monkeypatch.setattr(resonance, "MODEL_VERSION", resonance.MODEL_VERSION + 1)
    load_measurements(cal, tmp_path)
    assert len(train_calls) == 4


def test_corrupt_cache_retrains(tmp_path, train_calls):
    cal = _calibration()
    load_measurements(cal, tmp_path)
    cache_path(cal, tmp_path).write_bytes(b"garbage")
    result = load_measurements(cal, tmp_path)
    assert len(train_calls) == 4
    _same(result, cal.measurements())


def test_unwritable_cache_still_returns_models(tmp_path, train_calls, monkeypatch):
    def fail(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(model_cache, "_save", fail)
    result = load_measurements(_calibration(), tmp_path)
    assert set(result.models) == {"L", "R"}
