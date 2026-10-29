"""The guided test's search logic (no Qt): louder test, smoothness check and the flow across ears."""

import math
import random

import numpy as np
import pytest

from eq_helper.dsp.resonance import CONTROL_RANGE, PERSONAL_TREBLE_RANGE
from resonancefit.guided_test import (
    AUDIBLE, AUDIBLE_BAND, BANDS, FIRST_SMOOTH_DELTA, LOUDER, LOUDER_MAX_ROUNDS, PRIMARY, SMOOTH_DELTA, SMOOTH_WIDE_DELTA, SMOOTHNESS,
    UPPER, UPPER_SMOOTH_DELTA, UPPER_WINDOW, GuidedTest, LouderSearch, SmoothnessSearch,
)


def ear(f0, width=0.1, height=8.0, noise=0.6, jnd=1.0, slope=0.0, slip=0.0, ripple=0.0, rng=None):
    """A simulated listener: a peak at ``f0`` (σ ``width`` octaves, ``height`` dB) on a flat or sloped
    background, plus ``ripple`` dB (RMS) of fine dips and bumps 0.03–0.1 octave wide; a band's loudness
    is the power average over it. Differences below ``jnd`` dB are heard as "About the same"."""
    rng = rng or random.Random(1)
    ripples = [(rng.uniform(0.03, 0.1), rng.uniform(0, 2 * math.pi)) for _ in range(3)]

    def level(x):  # x: log2 Hz
        peak = height * np.exp(-0.5 * ((x - math.log2(f0)) / width) ** 2) + slope * (x - math.log2(7500))
        bumps = sum(np.sin(2 * np.pi * x / period + phase) for period, phase in ripples)
        return peak + ripple * bumps / math.sqrt(1.5)  # three unit sines: RMS √1.5

    def heard(hz, band):
        x = math.log2(hz) + (np.linspace(-band / 2, band / 2, 41) if band > 0 else np.zeros(1))
        return float(10 * np.log10(np.mean(10 ** (level(x) / 10))))

    def answer(round_):
        if rng.random() < slip:
            return rng.choice([0, 1, None])
        band = getattr(round_, "band", 0.0)
        d = heard(round_.options[0], band) - heard(round_.options[1], band) + rng.gauss(0, noise)
        if abs(d) < jnd:
            return None
        return 0 if d > 0 else 1
    return answer


def run(search, listener):
    while not search.done:
        search.answer(listener(search.options()))
    return search.result()


@pytest.mark.parametrize("f0", [6000, 6300, 6800, 7300, 7700, 8200, 8600, 9000])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_louder_search_finds_the_peak_within_three_percent(f0, seed):
    result = run(LouderSearch(rng=random.Random(seed)), ear(f0, rng=random.Random(seed + 10)))
    assert result.rounds <= LOUDER_MAX_ROUNDS
    assert result.confident and not result.fell_back
    assert abs(result.hz / f0 - 1) < 0.03
    assert CONTROL_RANGE[0] <= result.hz <= CONTROL_RANGE[1]


@pytest.mark.parametrize("width", [0.05, 0.1, 0.18])
@pytest.mark.parametrize("slope", [-3.0, 0.0, 3.0])
def test_robust_to_peak_width_and_a_sloped_background(width, slope):
    misses = 0
    for seed in range(12):
        f0 = math.exp(random.Random(seed).uniform(math.log(6000), math.log(9000)))
        result = run(LouderSearch(rng=random.Random(seed)), ear(f0, width, slope=slope, rng=random.Random(seed)))
        misses += abs(result.hz / f0 - 1) >= 0.03
    assert misses <= 1


def test_ripple_in_the_ear_misleads_tones_but_not_bands():
    """Why the louder test plays noise bands: fine dips and bumps in a sealed ear canal (here 2.5 dB)
    send a tone search astray; bands average over them."""
    misses = {"tones": 0, "bands": 0}
    for seed in range(30):
        f0 = math.exp(random.Random(seed).uniform(math.log(6000), math.log(9000)))
        for name, bands in (("tones", (0.0,)), ("bands", BANDS)):
            search = LouderSearch(rng=random.Random(seed), bands=bands)
            result = run(search, ear(f0, width=0.15, ripple=2.5, rng=random.Random(seed + 50)))
            misses[name] += abs(result.hz / f0 - 1) >= 0.03
    assert misses["bands"] <= 1
    assert misses["tones"] >= misses["bands"] + 3


def test_wide_bands_first_narrow_bands_later():
    first, last = [], []
    for seed in range(10):
        search = LouderSearch(rng=random.Random(seed))
        listener = ear(7600, rng=random.Random(seed))
        bands = []
        while not search.done:
            bands.append(search.options().band)
            search.answer(listener(search.options()))
        assert set(bands) <= set(BANDS)
        first.append(bands[0])
        last.append(bands[-1])
    assert np.mean(first) > np.mean(last)


def test_same_for_two_sounds_beside_a_narrow_peak_keeps_the_far_region():
    """Two sounds on the flat part sound alike: "About the same" must not rule out a peak far from both."""
    search = LouderSearch(rng=random.Random(3))
    search.options()
    search._round = type(search.options())((7005.0, 7709.0), 1 / 12)  # the pair a midpoint search asks first
    search._index.setdefault(7005.0, int(np.argmin([abs(c - 7005) for c in search.candidates])))
    search._index.setdefault(7709.0, int(np.argmin([abs(c - 7709) for c in search.candidates])))
    search.answer(None)
    marginal = search._place(search.posterior())
    hz = 2.0 ** search.grid
    far = marginal[hz > 8300].sum() / (hz > 8300).mean()
    between = marginal[(hz > 7200) & (hz < 7500)].sum() / ((hz > 7200) & (hz < 7500)).mean()
    near_tones = marginal[(hz > 6950) & (hz < 7060)].sum() / ((hz > 6950) & (hz < 7060)).mean()
    assert far > 0.5 * between     # a peak far from both stays likely
    assert near_tones < far        # a peak right at one of the sounds would more likely have been heard


def test_narrow_peak_at_the_top_is_found_even_after_same_answers():
    for seed in range(6):
        result = run(LouderSearch(rng=random.Random(seed)),
                     ear(8800, width=0.05, jnd=1.5, rng=random.Random(seed)))
        assert abs(result.hz / 8800 - 1) < 0.03


def test_slips_are_flagged_or_survived():
    flagged = wrong_and_confident = 0
    for seed in range(40):
        f0 = math.exp(random.Random(seed).uniform(math.log(6000), math.log(9000)))
        result = run(LouderSearch(rng=random.Random(seed)), ear(f0, slip=0.15, rng=random.Random(seed)))
        flagged += not result.confident
        wrong_and_confident += result.confident and abs(result.hz / f0 - 1) > 0.06
    assert flagged >= 1
    assert wrong_and_confident <= 1


def test_a_listener_who_hears_no_difference_falls_back_quickly():
    search = LouderSearch(rng=random.Random(4))
    for _ in range(4):
        search.answer(None)
    assert search.done
    result = search.result()
    assert result.fell_back and not result.confident
    assert CONTROL_RANGE[0] <= result.hz <= CONTROL_RANGE[1]


def test_options_are_playable_bands_in_random_order():
    orders = set()
    for seed in range(20):
        round_ = LouderSearch(rng=random.Random(seed)).options()
        assert len(round_.options) == 2 and round_.options[0] != round_.options[1]
        assert all(CONTROL_RANGE[0] <= hz <= CONTROL_RANGE[1] for hz in round_.options)
        ratio = max(round_.options) / min(round_.options)
        assert round_.band in BANDS
        assert 2 ** max(0.039, round_.band / 2 - 0.001) <= ratio <= 2 ** 0.401
        orders.add(round_.options[0] < round_.options[1])
    assert orders == {True, False}


def test_undo_restores_the_belief_and_the_same_round():
    search = LouderSearch(rng=random.Random(4))
    first = search.options()
    before = search.posterior().copy()
    search.answer(0)
    second = search.options()
    search.answer(1)
    assert search.undo()
    assert search.options() == second
    assert search.undo()
    assert search.options() == first
    np.testing.assert_allclose(search.posterior(), before)
    assert not search.undo()


# ── Stage 2 ──────────────────────────────────────────────────────────────────

def pick(search, which):
    """Answer with the low/mid/high option (whatever letter it has)."""
    triple = sorted(search.options().options)
    search.answer(search.options().options.index(triple[{"low": 0, "mid": 1, "high": 2}[which]]))


def test_smoothness_options_are_centre_and_neighbours_low_to_high():
    for seed in range(4):
        search = SmoothnessSearch(7500, rng=random.Random(seed))
        options = search.options().options
        assert options == pytest.approx((7500 * 2 ** -SMOOTH_DELTA, 7500, 7500 * 2 ** SMOOTH_DELTA))


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

F0 = {PRIMARY: {"L": 7000.0, "R": 8200.0}, UPPER: {"L": 13500.0, "R": 15800.0}}


def run_ear(test, rng, listen=None):
    side, target = test.side, test.target
    f0 = F0[target][side]
    listener = listen or ear(f0, width=0.14 if target == UPPER else 0.1, rng=rng)
    while test.side == side and test.target == target and test.status == "testing":
        if test.kind == LOUDER:
            test.answer(listener(test.options()))
        elif test.kind == AUDIBLE:
            test.answer(0)  # heard clearly
        else:
            options = test.options().options
            test.answer(int(np.argmin([abs(math.log(hz / f0)) for hz in options])))


def test_full_flow_left_then_right_then_the_upper_offer():
    test = GuidedTest(("L", "R"), rng=random.Random(9))
    assert (test.side, test.kind, test.target) == ("L", LOUDER, PRIMARY)
    progress = [test.progress()]
    run_ear(test, random.Random(1))
    assert "L" in test.results and (test.side, test.kind) == ("R", SMOOTHNESS)  # from the left ear
    progress.append(test.progress())
    run_ear(test, random.Random(2))
    assert test.status == "upper_offer" and test.progress() == 1.0
    assert progress == sorted(progress)
    for side in "LR":
        assert abs(test.results[side] / F0[PRIMARY][side] - 1) < 0.03
    test.skip_upper()
    assert test.status == "finished" and test.upper_results == {}


PREDICTED = {"L": 14000.0, "R": 15000.0}


def primary_done(sides=("L", "R"), seed=10):
    test = GuidedTest(sides, rng=random.Random(seed))
    for i, _side in enumerate(sides):
        run_ear(test, random.Random(i + 1))
    assert test.status == "upper_offer"
    return test


def test_upper_treble_starts_from_the_prediction_with_no_louder_test():
    test = primary_done()
    test.start_upper(PREDICTED)
    assert (test.side, test.kind, test.target) == ("L", AUDIBLE, UPPER)
    question = test.options()
    assert question.options == (14000.0,) and question.band == AUDIBLE_BAND
    test.answer(0)
    assert (test.side, test.kind) == ("L", SMOOTHNESS)
    assert sorted(test.options().options) == pytest.approx(
        [14000 * 2 ** -UPPER_SMOOTH_DELTA, 14000, 14000 * 2 ** UPPER_SMOOTH_DELTA])
    run_ear(test, random.Random(3))
    assert (test.side, test.kind) == ("R", AUDIBLE)
    run_ear(test, random.Random(4))
    assert test.status == "finished"
    assert all(stage.kind != LOUDER for stage in test.stages if stage.target == UPPER)
    for side in "LR":
        assert abs(test.upper_results[side] / F0[UPPER][side] - 1) < 0.03
    assert set(test.results) == {"L", "R"}


def test_upper_search_stays_where_the_eq_can_move_it():
    test = primary_done(("L",))
    test.start_upper({"L": 12000.0})
    lo, hi = test.upper_window("L")
    assert lo == pytest.approx(max(PERSONAL_TREBLE_RANGE[0], 12000 / UPPER_WINDOW))
    assert hi == pytest.approx(12000 * UPPER_WINDOW)
    test.answer(0)
    while test.status == "testing":  # always the highest: the true peak is far above
        options = test.options().options
        test.answer(options.index(max(options)))
    assert lo <= test.upper_results["L"] <= hi


def test_not_heard_clearly_leaves_that_ears_upper_treble_alone():
    test = primary_done()
    test.start_upper(PREDICTED)
    test.answer(None)  # left: barely or not at all
    assert test.upper_results == {"L": None}
    assert (test.side, test.kind) == ("R", AUDIBLE)
    assert test.best_guess("L", UPPER) is None and test.best_guess("R", UPPER) is None
    test.answer(None)
    assert test.status == "finished" and test.upper_results == {"L": None, "R": None}
    assert test.undo() and test.status == "testing" and (test.side, test.kind) == ("R", AUDIBLE)
    assert test.undo() and (test.side, test.kind) == ("L", AUDIBLE)


def test_an_ear_without_a_predicted_upper_peak_is_skipped():
    test = primary_done()
    test.start_upper({"L": None, "R": 15000.0})
    assert test.upper_results == {"L": None} and (test.side, test.kind) == ("R", AUDIBLE)
    test = primary_done()
    test.start_upper({})
    assert test.status == "finished" and test.upper_results == {"L": None, "R": None}


def test_flow_undo_crosses_stages_and_ears():
    test = GuidedTest(("L", "R"), rng=random.Random(10))
    run_ear(test, random.Random(1))
    assert test.side == "R"
    assert test.undo()  # back into the left ear's smoothness check
    assert (test.side, test.kind) == ("L", SMOOTHNESS) and "L" not in test.results
    while test.undo():
        pass
    assert (test.side, test.kind) == ("L", LOUDER) and test.stage.search.answers == []


def unsure_test():
    for seed in range(200):
        test = GuidedTest(("L", "R"), rng=random.Random(seed))
        rng = random.Random(seed)
        n = 0
        while test.kind == LOUDER and test.status == "testing":
            # an ear that contradicts itself: a peak that jumps between two places
            test.answer(ear(6300 if n % 2 else 8700, rng=rng)(test.options()))
            n += 1
        if test.status == "unsure":
            return test
    raise AssertionError("never unsure")


def test_unsure_offers_redo_or_using_the_estimate():
    test = unsure_test()
    estimate = test.stage.search.estimate()
    assert test.best_guess("L") == pytest.approx(estimate) or test.best_guess("L") is None
    test.use_estimate()
    assert test.status == "testing" and test.kind == SMOOTHNESS
    assert test.stage.search.center == pytest.approx(estimate)
    assert test.stage.search.delta == SMOOTH_WIDE_DELTA
    test = unsure_test()
    test.redo_ear()
    assert test.status == "testing" and test.kind == LOUDER and test.stage.search.answers == []


def test_leaving_during_the_louder_test_keeps_its_estimate():
    test = GuidedTest(("L", "R"), rng=random.Random(12))
    listener = ear(7400, rng=random.Random(1))
    assert test.best_guess("L") is None
    for _ in range(4):
        test.answer(listener(test.options()))
    assert abs(test.best_guess("L") / 7400 - 1) < 0.05


def test_a_starting_point_skips_the_louder_test():
    test = GuidedTest(("R",), default_hz={"R": 7700}, rng=random.Random(12))
    assert test.kind == SMOOTHNESS
    assert test.stage.search.center == 7700 and test.stage.search.delta == FIRST_SMOOTH_DELTA


def test_louder_test_hearing_nothing_starts_smoothness_wide_from_its_estimate():
    test = GuidedTest(("R",), rng=random.Random(12))
    for _ in range(4):
        test.answer(None)
    assert test.kind == SMOOTHNESS and test.stage.search.delta == SMOOTH_WIDE_DELTA


def test_always_same_means_no_clear_peak_not_a_confident_middle():
    """"About the same" around a real peak is information, but a listener who never hears a
    difference must not be pulled to wherever the pairs happened to balance."""
    for seed in range(4):
        search = LouderSearch(rng=random.Random(seed))
        while not search.done:
            search.options()
            search.answer(None)
        result = search.result()
        assert result.fell_back and not result.confident and result.rounds <= 6


def test_same_answers_around_a_real_peak_still_count():
    for seed in range(4):
        result = run(LouderSearch(rng=random.Random(seed)), ear(7300, jnd=2.0, rng=random.Random(seed)))
        assert result.confident and abs(result.hz / 7300 - 1) < 0.03


def test_answers_that_contradict_each_other_are_unsure():
    for seed in range(6):
        search, rng, n = LouderSearch(rng=random.Random(seed)), random.Random(seed), 0
        while not search.done:
            search.answer(ear(6300 if n % 2 else 8700, rng=rng)(search.options()))
            n += 1
        assert not search.result().confident


def test_check_rounds_catch_most_random_clicking():
    unsure = 0
    for seed in range(12):
        search, rng = LouderSearch(rng=random.Random(seed)), random.Random(seed + 5)
        while not search.done:
            search.options()
            search.answer(rng.choice([0, 1]))
        unsure += not search.result().confident
    assert unsure >= 7
