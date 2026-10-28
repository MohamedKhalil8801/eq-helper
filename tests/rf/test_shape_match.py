"""Shape match (loudness matches fitted to the calibration model) and the simulated listeners."""

import random

import numpy as np
import pytest

from eq_helper.dsp.resonance import Measurements
from resonancefit import simulation as sim
from resonancefit.shape_match import (
    MIN_MATCHES, MIN_MATCHES_FROM_OTHER_EAR, ShapeMatch, ShapeModel, band_level, stable_anchor,
)

from .conftest import make_calibration


@pytest.fixture(scope="module")
def model():
    return Measurements.from_fits(make_calibration().fits, "test").models["L"]


@pytest.fixture(scope="module")
def predict(model):
    return sim.Predictions(model)


@pytest.fixture(scope="module")
def shape(model, predict):
    return ShapeModel(predict, stable_anchor(model.freqs, model.curves))


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
def test_finds_the_resonance_despite_the_listeners_hearing_curve(shape, predict, hz):
    errors = []
    for seed in range(3):
        result = _run(ShapeMatch(shape, start=7500), _ear(predict, hz, seed=seed))
        errors.append(abs(result.hz / hz - 1))
        assert result.matches >= MIN_MATCHES
    assert np.median(errors) < 0.02


def test_a_wrong_start_costs_matches_not_the_answer(shape, predict):
    result = _run(ShapeMatch(shape, start=8800), _ear(predict, 6400, seed=4))
    assert abs(result.hz / 6400 - 1) < 0.03


def test_starting_from_the_other_ear_needs_fewer_matches(shape, predict):
    counts = {False: [], True: []}
    for seed in range(4):
        for other in (False, True):
            search = ShapeMatch(shape, start=7600 if other else 7000, from_other_ear=other)
            _run(search, _ear(predict, 7650, seed=seed))
            counts[other].append(len(search.answers))
    assert min(counts[True]) >= MIN_MATCHES_FROM_OTHER_EAR
    assert np.mean(counts[True]) < np.mean(counts[False])


def test_matches_stay_within_a_small_pitch_gap_and_start_well_placed(shape, predict):
    search = ShapeMatch(shape, start=7500)
    ear = _ear(predict, 7500)
    for _ in range(6):
        q = search.question()
        assert 0 < np.log2(q.adjust / q.reference) <= 0.75 + 1e-9
        search.answer(ear.match(q))


def test_a_match_far_off_the_others_is_asked_again_once_and_undo_restores(shape, predict):
    search = ShapeMatch(shape, start=7500)
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


def test_simulated_today_vs_shape_then_smooth(shape, predict):
    """The simulator runs both designs end to end (numbers: python -m resonancefit.simulation)."""
    ear = _ear(predict, 7300, seed=2)
    best = sim.best_eq_hz(ear, predict)
    assert abs(best - 7300) < 60
    new = sim.shape_then_smooth(_ear(predict, 7300, seed=2), predict, shape, 7500, random.Random(1))
    old = sim.today(_ear(predict, 7300, seed=2), predict, 7500, random.Random(1))
    assert abs(new.hz - best) < 150 and new.questions > MIN_MATCHES and old.questions > 0


def test_guided_test_uses_the_shape_match_then_a_fine_smoothness_check(shape, predict):
    from resonancefit.guided_test import FINE_MIN_DELTA, PRIMARY, SHAPE, SMOOTHNESS, GuidedTest

    test = GuidedTest(("L", "R"), {"L": 7000, "R": 7000}, random.Random(1), offer_upper=False,
                      shape_models={"L": shape, "R": shape})
    ears = {"L": _ear(predict, 7400, seed=1), "R": _ear(predict, 7700, seed=2)}
    assert test.kind == SHAPE and test.stage.search.min_matches == MIN_MATCHES
    kinds = []
    while test.status == "testing":
        if test.side == "R" and test.kind == SHAPE and not test.stage.search.answers:
            assert test.stage.search.min_matches == MIN_MATCHES_FROM_OTHER_EAR  # starts from the left ear
        kinds.append((test.side, test.kind))
        ear = ears[test.side]
        if test.kind == SHAPE:
            test.answer(ear.match(test.options()))
        else:
            test.answer(ear.smoothest(test.options(), predict))
    assert test.status == "finished"
    assert list(dict.fromkeys(kinds)) == [("L", SHAPE), ("L", SMOOTHNESS), ("R", SHAPE), ("R", SMOOTHNESS)]
    assert all(s.search.min_delta == FINE_MIN_DELTA for s in test.stages if s.kind == SMOOTHNESS)
    assert abs(test.results["L"] - 7400) < 200 and abs(test.results["R"] - 7700) < 200
    assert test.uncertainty()[("L", PRIMARY)] > 0
    while test.undo():
        pass
    assert test.kind == SHAPE and test.side == "L" and not test.stage.search.answers


def test_pair_generator_takes_turns_and_moves_band_b_with_the_slider():
    from eq_helper.audio.generators import AlternatingBandsGenerator

    pair = AlternatingBandsGenerator(48000, 4800, 7000, 1 / 6, period_s=0.1, fade_s=0.01, seed=1)
    first = pair.next_block(4800)          # A's turn
    assert pair.current == "b"
    second = pair.next_block(4800)         # B's turn
    rms = lambda x: float(np.sqrt(np.mean(x[960:-960] ** 2)))  # noqa: E731
    assert rms(first) == pytest.approx(rms(second), rel=0.25)
    pair.next_block(4800)
    pair.gain_db = 6.0
    louder = pair.next_block(4800)         # B again, 6 dB up (after the smoothing fade)
    assert 20 * np.log10(rms(louder) / rms(second)) == pytest.approx(6.0, abs=1.0)
    pair.mode = "a"
    pair.next_block(4800)
    assert pair.current == "a"
