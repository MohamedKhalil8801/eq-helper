"""Shape match (loudness matches fitted to the calibration model) and the simulated listeners."""

import random

import numpy as np
import pytest

from eq_helper.dsp.resonance import Measurements
from resonancefit import simulation as sim
from resonancefit.shape_match import MIN_MATCHES, MIN_MATCHES_FROM_OTHER_EAR, ShapeMatch, band_level, stable_anchor

from .conftest import make_calibration


@pytest.fixture(scope="module")
def model():
    return Measurements.from_fits(make_calibration().fits, "test").models["L"]


@pytest.fixture(scope="module")
def predict(model):
    return sim.Predictions(model)


def _ear(predict, hz, listener=sim.TRAINED, seed=0):
    _f, response = predict(hz)
    rng = random.Random(seed)
    return sim.Ear(response, hz, listener, rng, sim.Ear.person(listener, rng))


def _run(search, ear):
    while not search.done:
        search.answer(ear.match(search.question()))
    return search.result()


def test_anchor_is_the_band_that_moves_least_with_insertion():
    f = np.geomspace(1000, 20000, 600)
    flat = np.exp(-0.5 * (np.log2(f / 4000) / 0.3) ** 2)  # changes only around 4 kHz
    curves = [flat * k for k in (0, 3, 6)]
    anchor = stable_anchor(f, curves, hz_range=(3500, 5500))
    assert anchor > 5000
    assert band_level(f, np.zeros_like(f), 7000) == pytest.approx(0.0)


@pytest.mark.parametrize("hz", [6300, 7100, 8000, 8700])
def test_finds_the_resonance_despite_the_listeners_hearing_curve(model, predict, hz):
    anchor = stable_anchor(model.freqs, model.curves)
    errors = []
    for seed in range(3):
        result = _run(ShapeMatch(predict, anchor, start=7500), _ear(predict, hz, seed=seed))
        errors.append(abs(result.hz / hz - 1))
        assert result.matches >= MIN_MATCHES
    assert np.median(errors) < 0.02


def test_a_wrong_start_costs_matches_not_the_answer(model, predict):
    anchor = stable_anchor(model.freqs, model.curves)
    result = _run(ShapeMatch(predict, anchor, start=8800), _ear(predict, 6400, seed=4))
    assert abs(result.hz / 6400 - 1) < 0.03


def test_starting_from_the_other_ear_needs_fewer_matches(model, predict):
    anchor = stable_anchor(model.freqs, model.curves)
    counts = {False: [], True: []}
    for seed in range(4):
        for other in (False, True):
            search = ShapeMatch(predict, anchor, start=7600 if other else 7000, from_other_ear=other)
            _run(search, _ear(predict, 7650, seed=seed))
            counts[other].append(len(search.answers))
    assert min(counts[True]) >= MIN_MATCHES_FROM_OTHER_EAR
    assert np.mean(counts[True]) < np.mean(counts[False])


def test_matches_stay_within_a_small_pitch_gap_and_start_well_placed(model, predict):
    search = ShapeMatch(predict, stable_anchor(model.freqs, model.curves), start=7500)
    ear = _ear(predict, 7500)
    for _ in range(6):
        q = search.question()
        assert 0 < np.log2(q.adjust / q.reference) <= 0.75 + 1e-9
        search.answer(ear.match(q))


def test_a_match_far_off_the_others_is_asked_again_once_and_undo_restores(model, predict):
    search = ShapeMatch(predict, stable_anchor(model.freqs, model.curves), start=7500)
    ear = _ear(predict, 7500)
    for _ in range(4):
        search.answer(ear.match(search.question()))
    q = search.question()
    search.answer(-12.0 if ear.match(q) > 0 else 12.0)  # way off
    again = search.question()
    assert again.repeat and (again.reference, again.adjust) == (q.reference, q.adjust)
    search.answer(ear.match(again))
    assert search._dropped == {4}  # the second answer disagreed: the first is left out of the fit
    assert search.done or not search.question().repeat
    assert search.undo() and search._dropped == set() and search.question().repeat
    while search.answers:
        assert search.undo()
    assert not search.undo()


def test_simulated_today_vs_shape_then_smooth(model, predict):
    """The simulator runs both designs end to end (numbers: python -m resonancefit.simulation)."""
    anchor = stable_anchor(model.freqs, model.curves)
    ear = _ear(predict, 7300, seed=2)
    best = sim.best_eq_hz(ear, predict)
    assert abs(best - 7300) < 60
    new = sim.shape_then_smooth(_ear(predict, 7300, seed=2), predict, anchor, 7500, random.Random(1))
    old = sim.today(_ear(predict, 7300, seed=2), predict, 7500, random.Random(1))
    assert abs(new.hz - best) < 150 and new.questions > MIN_MATCHES and old.questions > 0
