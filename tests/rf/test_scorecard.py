"""The scorecard and the search "Find it for me" runs per ear."""

import random

import numpy as np
import pytest

from eq_helper.dsp.resonance import CONTROL_RANGE, Measurements
from resonancefit.scorecard import DONE_WIDTH_HZ, MAX_ROUNDS, OVERVIEW_CARDS, PickSearch, RoughnessTable, Scorecard

from .conftest import make_calibration


@pytest.fixture(scope="module")
def model():
    return Measurements.from_fits(make_calibration().fits, "test").models["L"]


@pytest.fixture(scope="module")
def table(model):
    return RoughnessTable.for_model(model)


def _listener(table, true_hz, noise=0.1, seed=0):
    """Picks the EQ the table says is least rough at ``true_hz`` (with some wobble); rules out the
    clearly worse half in the overview."""
    rng = random.Random(seed)
    row = table.table[table.index(true_hz)]

    def answer(search):
        options = search.options()
        seen = [row[table.index(hz)] + rng.gauss(0, noise) for hz in options]
        best = int(np.argmin(seen))
        out = [i for i in np.argsort(seen)[len(seen) // 2:]] if search.in_overview else []
        search.answer(best, out)
    return answer


def test_table_is_zero_on_the_diagonal_and_grows_away_from_it(table):
    assert np.allclose(np.diag(table.table), 0.0, atol=1e-9)
    i = table.index(7500)
    row = table.table[i]
    assert row[i + 10] > row[i + 2] > 0 and row[i - 10] > row[i - 2] > 0
    assert table.hz[0] == pytest.approx(CONTROL_RANGE[0]) and table.hz[-1] <= CONTROL_RANGE[1]


@pytest.mark.parametrize("true_hz", [6400, 7300, 8100, 8800])
def test_finds_the_resonance_from_a_wrong_start(table, true_hz):
    search = PickSearch(table, start=7400, rng=random.Random(1))
    answer = _listener(table, true_hz)
    assert search.in_overview and len(search.options()) == OVERVIEW_CARDS
    while not search.done:
        answer(search)
        if not search.done:
            assert len(search.options()) == 2  # A/B after the overview
    assert abs(search.result() - true_hz) < 60
    assert len(search.answers) == MAX_ROUNDS or search.card.width <= DONE_WIDTH_HZ


def test_one_wrong_answer_is_outvoted(table):
    search = PickSearch(table, start=7400, rng=random.Random(2))
    answer = _listener(table, 8000)
    answer(search)
    options = search.options()  # an A/B round: answer it the wrong way
    row = table.table[table.index(8000)]
    worse = int(np.argmax([row[table.index(hz)] for hz in options]))
    search.answer(worse)
    while not search.done:
        answer(search)
    assert abs(search.result() - 8000) < 80


def test_undo_restores_the_round_and_the_belief(table):
    search = PickSearch(table, start=7400, rng=random.Random(3))
    answer = _listener(table, 7800)
    answer(search)
    before = search.card.posterior().copy()
    options = search.options()
    search.answer(0)
    assert search.undo()
    assert search.options() == options and np.allclose(search.card.posterior(), before)
    while search.answers:
        assert search.undo()
    assert not search.undo() and search.in_overview


def test_cant_tell_in_ab_is_information_but_not_in_the_overview(table):
    search = PickSearch(table, start=7400, rng=random.Random(4))
    flat = search.card.posterior().copy()
    search.answer(None)  # overview: can't tell
    assert np.allclose(search.card.posterior(), flat)
    search.answer(None)  # A/B: they sound alike
    assert not np.allclose(search.card.posterior(), flat)


def test_without_overview_it_starts_with_a_pair(table):
    search = PickSearch(table, start=7600, rng=random.Random(5), overview=False)
    assert not search.in_overview and len(search.options()) == 2


def test_picking_counts_for_every_resonance_where_it_makes_sense(table):
    card = Scorecard(table)
    shown = table.overview()
    best = shown[3]
    card.picked(shown, best)
    p = card.posterior()
    assert table.hz[int(np.argmax(p))] == pytest.approx(table.hz[best], rel=0.03)


def test_guided_test_runs_each_ear_as_a_pick_stage(table):
    from resonancefit.guided_test import PICK, PRIMARY, GuidedTest

    test = GuidedTest(("L", "R"), {"L": 7400, "R": 7400}, random.Random(6), offer_upper=False,
                      tables={"L": table, "R": table})
    truth = {"L": 7900, "R": 8300}
    assert test.kind == PICK and test.overview and len(test.options().options) == OVERVIEW_CARDS
    assert test.progress() == 0
    rows = {side: table.table[table.index(hz)] for side, hz in truth.items()}
    while test.status == "testing":
        options = test.options().options
        seen = [rows[test.side][table.index(hz)] for hz in options]
        best = int(np.argmin(seen))
        out = [int(i) for i in np.argsort(seen)[4:]] if test.overview else []
        if test.side == "R" and test.overview:
            assert test.stage.search.start == pytest.approx(test.results["L"])  # starts from the left ear
            assert test.progress() == pytest.approx(0.5)
        test.answer(best, out)
        if test.status == "testing" and test.stage.search.answers:
            assert test.best_guess(test.side, PRIMARY) is not None
    assert abs(test.results["L"] - 7900) < 60 and abs(test.results["R"] - 8300) < 60
    while test.undo():
        pass
    assert test.kind == PICK and test.side == "L" and test.overview
