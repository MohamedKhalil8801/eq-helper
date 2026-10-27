"""The guided test's search logic (no Qt): louder test, smoothness check and the flow across ears."""

import math
import random

import numpy as np
import pytest

from eq_helper.dsp.resonance import CONTROL_RANGE
from resonancefit.guided_test import (
    LOUDER, SMOOTH_DELTA, SMOOTH_WIDE_DELTA, SMOOTHNESS, GuidedTest, LouderSearch, SmoothnessSearch,
)


def loudness(hz, f0, width=0.15):
    """A simulated ear: loudness (dB) peaked at ``f0``, ``width`` octaves wide."""
    return 10 * math.exp(-0.5 * (math.log2(hz / f0) / width) ** 2)


def louder_listener(f0, noise=0.05, same_below=0.0, rng=None):
    rng = rng or random.Random(1)

    def answer(round_):
        levels = [loudness(hz, f0) + rng.gauss(0, noise) for hz in round_.options]
        if abs(levels[0] - levels[1]) < same_below:
            return None
        return int(np.argmax(levels))
    return answer


@pytest.mark.parametrize("f0", [6000, 6300, 6800, 7300, 7700, 8200, 8600, 9000])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_louder_search_finds_the_peak_within_three_percent(f0, seed):
    search = LouderSearch(rng=random.Random(seed))
    listener = louder_listener(f0, rng=random.Random(seed + 10))
    while not search.done:
        search.answer(listener(search.options()))
    result = search.result()
    assert result.rounds <= 10
    assert result.confident and not result.fell_back
    assert abs(result.hz / f0 - 1) < 0.03
    assert CONTROL_RANGE[0] <= result.hz <= CONTROL_RANGE[1]


def test_options_are_the_golden_section_points_in_random_order():
    lo, hi = CONTROL_RANGE
    x1 = math.exp(math.log(hi) - 0.6180339887 * math.log(hi / lo))
    x2 = math.exp(math.log(lo) + 0.6180339887 * math.log(hi / lo))
    orders = set()
    for seed in range(20):
        round_ = LouderSearch(rng=random.Random(seed)).options()
        assert sorted(round_.options) == pytest.approx([x1, x2])
        orders.add(round_.options[0] < round_.options[1])
    assert orders == {True, False}


def test_each_answer_narrows_the_interval():
    search = LouderSearch(rng=random.Random(3))
    x1, x2 = sorted(search.options().options)
    search.answer(search.options().options.index(x1))  # x1 louder: b = x2
    assert math.exp(search.b) == pytest.approx(x2)
    assert math.exp(search.a) == pytest.approx(CONTROL_RANGE[0])
    y1, y2 = sorted(search.options().options)
    search.answer(search.options().options.index(y2))  # x2 louder: a = x1
    assert math.exp(search.a) == pytest.approx(y1)
    z1, z2 = sorted(search.options().options)
    search.answer(None)  # about the same: a = x1, b = x2
    assert (math.exp(search.a), math.exp(search.b)) == pytest.approx((z1, z2))


def test_undo_restores_the_interval_and_the_same_round():
    search = LouderSearch(rng=random.Random(4))
    first = search.options()
    search.answer(0)
    second = search.options()
    search.answer(1)
    assert search.undo()
    assert search.options() == second
    assert search.undo()
    assert search.options() == first
    assert (math.exp(search.a), math.exp(search.b)) == pytest.approx(CONTROL_RANGE)
    assert not search.undo()


def test_check_round_repeats_an_earlier_pair_swapped():
    search = LouderSearch(rng=random.Random(5))
    listener = louder_listener(7400)
    while search.searching:
        search.answer(listener(search.options()))
    check = search.options()
    assert check.check
    earlier = [r for _a, _b, r, louder in search.answers if louder is not None
               and set(r.options) == set(check.options)]
    assert earlier, "the check repeats a pair that was asked before"
    louder_before = next(louder for _a, _b, r, louder in search.answers if r == earlier[-1])
    assert check.options[1] == louder_before  # the earlier louder one now comes second


def test_inconsistent_listener_is_flagged():
    search = LouderSearch(rng=random.Random(6))
    listener = louder_listener(7400)
    while search.searching:
        search.answer(listener(search.options()))
    check = search.options()
    search.answer(0)  # the earlier quieter one, since the pair is swapped: a contradiction
    result = search.result()
    assert not result.confident
    assert result.hz is not None


def test_three_opening_same_answers_fall_back():
    search = LouderSearch(rng=random.Random(7))
    search.answer(None)
    search.answer(None)
    assert not search.done
    search.answer(None)
    assert search.done
    result = search.result()
    assert result.fell_back and result.hz is None
    assert search.undo() and not search.done


def test_same_answers_narrow_both_ends():
    search = LouderSearch(rng=random.Random(8))
    lo, hi = CONTROL_RANGE
    search.answer(None)
    assert lo < math.exp(search.a) < math.exp(search.b) < hi


# ── Stage 2 ──────────────────────────────────────────────────────────────────

def pick(search, which):
    """Answer with the low/mid/high option (whatever letter it has)."""
    triple = sorted(search.options().options)
    search.answer(search.options().options.index(triple[{"low": 0, "mid": 1, "high": 2}[which]]))


def test_smoothness_options_are_centre_and_neighbours_shuffled():
    seen = set()
    for seed in range(12):
        search = SmoothnessSearch(7500, rng=random.Random(seed))
        options = search.options().options
        assert sorted(options) == pytest.approx([7500 * 2 ** -SMOOTH_DELTA, 7500, 7500 * 2 ** SMOOTH_DELTA])
        seen.add(options.index(7500))
    assert seen == {0, 1, 2}


def test_centre_pick_halves_the_step_and_cant_tell_ends():
    search = SmoothnessSearch(7500, rng=random.Random(1))
    pick(search, "mid")
    assert search.delta == pytest.approx(SMOOTH_DELTA / 2)
    assert search.center == pytest.approx(7500)
    search.answer(None)
    assert search.done and search.result() == pytest.approx(7500)


def test_edge_picks_move_at_most_twice_in_a_row_then_halve():
    search = SmoothnessSearch(7500, rng=random.Random(2))
    pick(search, "high")
    assert search.center == pytest.approx(7500 * 2 ** SMOOTH_DELTA)
    assert search.delta == pytest.approx(SMOOTH_DELTA)
    pick(search, "high")
    assert search.center == pytest.approx(7500 * 2 ** (2 * SMOOTH_DELTA))
    assert search.delta == pytest.approx(SMOOTH_DELTA / 2)  # two moves: the step halves
    pick(search, "low")
    assert search.delta == pytest.approx(SMOOTH_DELTA / 2)


def test_small_step_or_six_rounds_end_the_check():
    search = SmoothnessSearch(7500, rng=random.Random(3))
    for _ in range(3):
        pick(search, "mid")
    assert search.delta < 0.015 and search.done
    search = SmoothnessSearch(7500, rng=random.Random(3))
    rounds = 0
    while not search.done:
        pick(search, "low" if rounds % 2 else "high")
        rounds += 1
    assert rounds <= 6


def test_smoothness_results_are_clamped_to_the_range():
    lo, hi = CONTROL_RANGE
    search = SmoothnessSearch(50000, rng=random.Random(4))
    assert max(search.options().options) <= hi
    for _ in range(6):
        if search.done:
            break
        pick(search, "high")
    assert search.result() <= hi
    search = SmoothnessSearch(10, rng=random.Random(4))
    assert min(search.options().options) >= lo
    assert search.result() >= lo


def test_smoothness_undo():
    search = SmoothnessSearch(7500, rng=random.Random(5))
    first = search.options()
    pick(search, "high")
    search.answer(None)
    assert search.done
    assert search.undo() and not search.done
    assert search.undo()
    assert search.options() == first and search.center == 7500
    assert not search.undo()


# ── the flow across ears ─────────────────────────────────────────────────────

def run_ear(test, f0, rng):
    listener = louder_listener(f0, rng=rng)
    side = test.side
    while test.side == side and test.status == "testing":
        if test.kind == LOUDER:
            test.answer(listener(test.options()))
        else:
            options = test.options().options
            test.answer(int(np.argmin([abs(math.log(hz / f0)) for hz in options])))


def test_full_flow_left_then_right():
    test = GuidedTest(("L", "R"), rng=random.Random(9))
    assert (test.side, test.kind) == ("L", LOUDER)
    progress = [test.progress()]
    run_ear(test, 7000, random.Random(1))
    assert "L" in test.results and (test.side, test.kind) == ("R", LOUDER)
    progress.append(test.progress())
    run_ear(test, 8200, random.Random(2))
    assert test.status == "finished" and test.progress() == 1.0
    assert abs(test.results["L"] / 7000 - 1) < 0.03
    assert abs(test.results["R"] / 8200 - 1) < 0.03
    assert progress == sorted(progress)


def test_flow_undo_crosses_stages_and_ears():
    test = GuidedTest(("L", "R"), rng=random.Random(10))
    run_ear(test, 7000, random.Random(1))
    assert test.side == "R"
    assert test.undo()  # back into the left ear's smoothness check
    assert (test.side, test.kind) == ("L", SMOOTHNESS) and "L" not in test.results
    while test.undo():
        pass
    assert (test.side, test.kind) == ("L", LOUDER) and test.stage.search.answers == []


def test_flow_unsure_offers_redo():
    test = GuidedTest(("L",), rng=random.Random(11))
    listener = louder_listener(7400)
    while test.stage.search.searching:
        test.answer(listener(test.options()))
    test.answer(0)  # contradicts the earlier answer
    assert test.status == "unsure"
    test.redo_ear()
    assert test.status == "testing" and test.stage.search.answers == []


def test_flow_fallback_starts_smoothness_wide_from_the_default():
    test = GuidedTest(("R",), default_hz={"R": 7700}, rng=random.Random(12))
    for _ in range(3):
        test.answer(None)
    assert test.kind == SMOOTHNESS
    assert test.stage.search.center == 7700 and test.stage.search.delta == SMOOTH_WIDE_DELTA
