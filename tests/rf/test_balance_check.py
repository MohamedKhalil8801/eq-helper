"""The balance check's analysis (no Qt): which leans mean a misplaced resonance, and which don't."""

import math
import random
from pathlib import Path

import numpy as np
import pytest

from resonancefit.balance_check import (
    BASS_HZ, REFERENCE_HZ, BalanceModel, analyse, plan_bands,
)

from .conftest import make_calibration

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def setup():
    data = np.loadtxt(ROOT / "assets" / "targets" / "PEQdB Diamond.txt", usecols=(0, 1))
    models = make_calibration().measurements().models
    return models, (data[:, 0], data[:, 1])


def leans_for(bands, model, wrong=None, tilt=0.0, bass=0.0, reference=0.0, noise=0.0, seed=0):
    """What a customer would centre: ``wrong`` = (side, target, true Hz) misplaced in the tuning."""
    rng = random.Random(seed)
    leans = []
    treble = [b for b in bands if b.kind in ("treble", "upper")]
    error = dict(zip(treble, model.lean_change(*wrong, treble))) if wrong else {}
    for b in bands:
        v = reference + rng.gauss(0, noise)
        if b.kind == "bass":
            v += bass
        if b.kind in ("treble", "upper"):
            v += error.get(b, 0.0) + tilt * (math.log2(b.hz) - 13)
        leans.append(v)
    return leans


def test_plan_has_reference_bass_treble_and_repeats():
    bands = plan_bands({"L": 7000, "R": 8000}, None, random.Random(1))
    kinds = [b.kind for b in bands]
    assert bands[0].hz == REFERENCE_HZ and [b.hz for b in bands if b.kind == "bass"][:2] == list(BASS_HZ)
    treble = [b.hz for b in bands if b.kind == "treble" and b.repeat_of is None]
    assert treble == sorted(treble) and treble[0] < 7000 / 1.3 and treble[-1] > 8000 * 1.3
    assert sum(b.repeat_of is not None for b in bands) == 2
    assert "upper" not in kinds
    upper = plan_bands({"L": 7000, "R": 8000}, {"L": 14000, "R": 15000}, random.Random(1))
    assert any(b.kind == "upper" for b in upper)


def test_centred_everywhere_is_balanced(setup):
    models, target = setup
    primary = {"L": 7200.0, "R": 7900.0}
    model = BalanceModel(models, target, "Diamond", primary)
    bands = plan_bands(primary, None, random.Random(2))
    result = analyse(bands, leans_for(bands, model, noise=0.5, reference=1.5), model)
    assert result.status == "balanced" and result.suggestion is None and result.reseat is None


@pytest.mark.parametrize("side,shift", [("L", 1.06), ("R", 0.94), ("R", 1.07)])
def test_a_misplaced_resonance_names_the_ear_and_the_way(setup, side, shift):
    models, target = setup
    primary = {"L": 7200.0, "R": 7900.0}
    model = BalanceModel(models, target, "Diamond", primary)
    bands = plan_bands(primary, None, random.Random(3))
    true_hz = primary[side] * shift
    leans = leans_for(bands, model, wrong=(side, "primary", true_hz), noise=0.4, seed=4)
    result = analyse(bands, leans, model, {("L", "primary"): 0.03, ("R", "primary"): 0.03})
    assert result.status == "recheck"
    s = result.suggestion
    assert s.side == side and s.target == "primary"
    assert (s.hz > primary[side]) == (shift > 1)
    assert abs(math.log(s.hz / true_hz)) < abs(math.log(primary[side] / true_hz))


def test_a_broad_hearing_lean_is_reported_not_rechecked(setup):
    models, target = setup
    primary = {"L": 7200.0, "R": 7900.0}
    model = BalanceModel(models, target, "Diamond", primary)
    bands = plan_bands(primary, None, random.Random(5))
    result = analyse(bands, leans_for(bands, model, tilt=3.0, reference=-1.0, noise=0.3), model)
    assert result.suggestion is None
    assert abs(result.broad_lean_db) > 1.5


def test_bass_lean_means_reseat_the_other_ear(setup):
    models, target = setup
    primary = {"L": 7200.0, "R": 7900.0}
    model = BalanceModel(models, target, "Diamond", primary)
    bands = plan_bands(primary, None, random.Random(6))
    result = analyse(bands, leans_for(bands, model, bass=4.0), model)  # bass louder on the left
    assert result.reseat == "R"


def test_repeats_that_disagree_are_flagged(setup):
    models, target = setup
    primary = {"L": 7200.0, "R": 7900.0}
    model = BalanceModel(models, target, "Diamond", primary)
    bands = plan_bands(primary, None, random.Random(7))
    leans = leans_for(bands, model)
    for i, b in enumerate(bands):
        if b.repeat_of is not None:
            leans[i] = 3.5
    assert analyse(bands, leans, model).unsteady


def test_a_well_found_ear_is_less_likely_to_be_blamed(setup):
    """Only the difference between the ears shows: the less certain ear takes the blame."""
    models, target = setup
    primary = {"L": 7200.0, "R": 7900.0}
    model = BalanceModel(models, target, "Diamond", primary)
    bands = plan_bands(primary, None, random.Random(8))
    leans = leans_for(bands, model, wrong=("L", "primary", 7200 * 1.06), seed=9)
    sure_left = analyse(bands, leans, model, {("L", "primary"): 0.005, ("R", "primary"): 0.06})
    assert sure_left.status != "recheck" or sure_left.suggestion.side == "R"
