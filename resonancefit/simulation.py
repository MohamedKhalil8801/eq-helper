"""Simulated listeners for the guided test: how accurate and how long each design is, before anyone listens.

A simulated ear is a real measured insertion from a calibration, left out
of the model the test uses (so the test never sees it), or a model
prediction with random errors added. On top come the listener's own traits,
none of which the test is told:

- a hearing curve: sensitivity changes across the treble (a tilt and a bend
  in dB, like equal-loudness contours, different for everyone) plus fine
  ripple about a tenth of an octave wide;
- pitch bias: a higher band can sound more prominent just for its pitch;
- fine dips and bumps of the sealed ear canal;
- answers that wobble from round to round, and the odd slip.

Run ``python -m resonancefit.simulation`` for a table.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass

import numpy as np

from eq_helper.dsp.resonance import CONTROL_RANGE, Measurements, ResonanceModel
from resonancefit.guided_test import (
    SMOOTH_DELTA, SMOOTH_WIDE_DELTA, LouderSearch, SmoothnessSearch,
)
from resonancefit.shape_match import ShapeMatch, band_level, stable_anchor

GRID = np.geomspace(1000, 20000, 900)   # where responses are kept (log spaced)
CACHE_OCTAVES = 1 / 384


@dataclass(frozen=True)
class Listener:
    """How one kind of listener answers (dB unless noted)."""

    name: str
    louder_noise: float = 0.8     # louder test: wobble of the impression
    jnd: float = 1.0              # … smaller differences: "About the same"
    match_noise: float = 1.0      # loudness match of neighbouring bands
    gap_noise: float = 1.0        # … extra per octave between them
    smooth_noise: float = 0.1     # smoothness check: wobble of the roughness impression (RMS dB)
    cant_tell: float = 0.05       # … all three closer than this: "Can't tell them apart"
    slip: float = 0.02            # any answer, whatever was heard
    tilt_sd: float = 6.0          # dB/octave: hearing tilt across the treble, around TILT_MEAN
    bend_sd: float = 4.0          # dB/octave²
    hearing_ripple: float = 1.0   # RMS dB, about 1/10 octave wide
    canal_ripple: float = 1.0     # RMS dB, 0.03–0.1 octave wide
    pitch_bias_sd: float = 1.5    # dB/octave: a higher band sounds this much more prominent (mean 1)


# dB/octave: at the eardrum, sensitivity changes little across 6–10 kHz on average (the free-field
# equal-loudness rise there is mostly the outer ear's own gain falling), but it differs between people
TILT_MEAN = 2.0
TRAINED = Listener("trained")
AVERAGE = Listener("average", louder_noise=1.5, jnd=1.5, match_noise=2.0, gap_noise=2.0,
                   smooth_noise=0.25, cant_tell=0.12, slip=0.06)


class Predictions:
    """A model's predictions on a fine grid of resonances, looked up instead of recomputed."""

    def __init__(self, model: ResonanceModel, lo=5600.0, hi=9800.0):
        x = np.arange(math.log2(lo), math.log2(hi) + 1e-9, CACHE_OCTAVES)
        self.x = x
        self.rows = np.array([np.interp(np.log(GRID), np.log(model.freqs), model.predict(2.0 ** v)) for v in x])

    def __call__(self, hz: float):
        k = int(np.clip(round((math.log2(hz) - self.x[0]) / CACHE_OCTAVES), 0, len(self.x) - 1))
        return GRID, self.rows[k]


def _ripple(rng, rms, periods):
    waves = [(rng.uniform(*periods), rng.uniform(0, 2 * math.pi)) for _ in range(3)]
    x = np.log2(GRID)
    return rms * sum(np.sin(2 * np.pi * x / p + ph) for p, ph in waves) / math.sqrt(1.5)


class Ear:
    """One simulated ear: what it really hears, and how its listener answers."""

    def __init__(self, response, true_hz: float, listener: Listener, rng: random.Random, person=None):
        self.true_hz = float(true_hz)
        self.listener = listener
        self.rng = rng
        x = np.log2(GRID / 7000.0)
        person = person or self.person(listener, rng)
        self.person_traits = person
        tilt, bend, ripple, bias = person
        self.response = np.asarray(response) + _ripple(rng, listener.canal_ripple, (0.03, 0.1))
        self.pitch_bias = bias
        # what's heard: the response minus the hearing loss curve (and plus pitch prominence for judgments)
        self.heard_db = self.response - (tilt * x + bend * x * x + ripple)

    @staticmethod
    def person(listener: Listener, rng: random.Random):
        """The traits shared by both ears of one person."""
        return (rng.gauss(TILT_MEAN, listener.tilt_sd), rng.gauss(0, listener.bend_sd),
                _ripple(rng, listener.hearing_ripple, (0.08, 0.15)), rng.gauss(1.0, listener.pitch_bias_sd))

    def heard(self, hz: float, band: float) -> float:
        level = band_level(GRID, self.heard_db, hz, band) if band > 0 else float(
            np.interp(math.log(hz), np.log(GRID), self.heard_db))
        return level + self.pitch_bias * math.log2(hz / 7000.0)

    def _slipped(self) -> bool:
        return self.rng.random() < self.listener.slip

    # ── answers ─────────────────────────────────────────────────────────
    def louder(self, round_) -> int | None:
        if self._slipped():
            return self.rng.choice([0, 1, None])
        a, b = round_.options
        d = self.heard(a, round_.band) - self.heard(b, round_.band) + self.rng.gauss(0, self.listener.louder_noise)
        if abs(d) < self.listener.jnd:
            return None
        return 0 if d > 0 else 1

    def match(self, question) -> float:
        gap = abs(math.log2(question.adjust / question.reference))
        if self._slipped():
            return self.rng.uniform(-6, 6)
        d = self.heard(question.adjust, 1 / 6) - self.heard(question.reference, 1 / 6)
        return d + self.rng.gauss(0, self.listener.match_noise + self.listener.gap_noise * gap)

    def roughness(self, predict, hz: float) -> float:
        """How uneven the treble sounds through an EQ made for a resonance at ``hz``: the RMS of what the
        EQ leaves uncorrected over 4.5–11 kHz, after its overall tilt (which doesn't sound uneven)."""
        _f, model = predict(hz)
        mask = (GRID >= 4500) & (GRID <= 11000)
        r = (self.response - model)[mask]
        x = np.log2(GRID[mask])
        r = r - np.polyval(np.polyfit(x, r, 1), x)
        return float(np.sqrt(np.mean(r * r)))

    def smoothest(self, round_, predict) -> int | None:
        if self._slipped():
            return self.rng.choice([0, 1, 2, None])
        rough = [self.roughness(predict, hz) for hz in round_.options]
        if max(rough) - min(rough) < self.listener.cant_tell:
            return None
        noisy = [r + self.rng.gauss(0, self.listener.smooth_noise) for r in rough]
        return int(np.argmin(noisy))


# ── test designs ────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    hz: float
    questions: int
    stages: str


def run_smoothness(ear: Ear, predict, centre: float, delta: float, rng) -> tuple[float, int]:
    search = SmoothnessSearch(centre, delta=delta, rng=rng)
    while not search.done:
        search.answer(ear.smoothest(search.options(), predict))
    return search.result(), len(search.answers)


def today(ear: Ear, predict, default_hz: float, rng) -> Outcome:
    """Today's test: the louder test, then the smoothness check."""
    louder = LouderSearch(rng=rng)
    while not louder.done:
        louder.answer(ear.louder(louder.options()))
    found = louder.result()
    if found.confident:
        hz, n = run_smoothness(ear, predict, found.hz, SMOOTH_DELTA, rng)
    else:  # fell back or unsure: a wide smoothness check (the customer may redo instead, not simulated)
        centre = default_hz if found.fell_back else found.hz
        hz, n = run_smoothness(ear, predict, centre, SMOOTH_WIDE_DELTA, rng)
    return Outcome(hz, found.rounds + n, "louder+smooth")


def shape_then_smooth(ear: Ear, predict, anchor: float, start: float | None, rng,
                      from_other_ear=False, smooth_delta=0.03) -> Outcome:
    """The new primary search: shape match from a starting point, then the smoothness check."""
    search = ShapeMatch(predict, anchor, start=start, from_other_ear=from_other_ear)
    while not search.done:
        search.answer(ear.match(search.question()))
    found = search.result()
    hz, n = run_smoothness(ear, predict, found.hz, smooth_delta if found.confident else SMOOTH_DELTA, rng)
    return Outcome(hz, len(search.answers) + n, "shape+smooth")


def shape_only(ear: Ear, predict, anchor: float, start: float | None, rng, from_other_ear=False) -> Outcome:
    search = ShapeMatch(predict, anchor, start=start, from_other_ear=from_other_ear)
    while not search.done:
        search.answer(ear.match(search.question()))
    return Outcome(search.result().hz, len(search.answers), "shape")


# ── ground truth ────────────────────────────────────────────────────────────
def measured_cases(measurements: Measurements):
    """(side, response on GRID, true resonance, model without it): each measured insertion left out in turn."""
    cases = []
    for side, model in measurements.models.items():
        for k, peak in enumerate(model.peaks):
            if not CONTROL_RANGE[0] <= peak <= CONTROL_RANGE[1]:
                continue
            keep = np.arange(len(model.peaks)) != k
            held_out = ResonanceModel(model.freqs, model.curves[keep], model.peaks[keep])
            truth = np.interp(np.log(GRID), np.log(model.freqs), model.curves[k])
            cases.append((side, truth, float(peak), held_out))
    return cases


def synthetic_cases(measurements: Measurements, n: int, rng: random.Random):
    """Model predictions at random resonances, with a smooth error and a different peak height."""
    cases = []
    for side, model in measurements.models.items():
        predictions = Predictions(model)
        for _ in range(n):
            hz = math.exp(rng.uniform(math.log(CONTROL_RANGE[0] * 1.02), math.log(CONTROL_RANGE[1] / 1.02)))
            _f, base = predictions(hz)
            x = np.log2(GRID / 7000.0)
            error = rng.gauss(0, 0.7) * x + rng.gauss(0, 0.7) * x * x
            cases.append((side, base + error, hz, model))
    return cases


def best_eq_hz(ear: Ear, predict) -> float:
    """The resonance whose EQ leaves this ear's treble smoothest: what the test should find. Usually
    close to the measured peak, but not where the model can't reproduce this ear's curve exactly."""
    candidates = np.geomspace(CONTROL_RANGE[0], CONTROL_RANGE[1], 400)
    return float(candidates[int(np.argmin([ear.roughness(predict, hz) for hz in candidates]))])


def summarise(name: str, outcomes: list[tuple[float, Outcome]]) -> str:
    errors = np.array([abs(o.hz - truth) for truth, o in outcomes])
    questions = np.array([o.questions for _t, o in outcomes])
    return (f"{name:<26} median {np.median(errors):4.0f} Hz   90% {np.percentile(errors, 90):5.0f} Hz   "
            f"within 50 Hz {np.mean(errors <= 50):4.0%}   within 100 Hz {np.mean(errors <= 100):4.0%}   "
            f"questions {np.mean(questions):4.1f}")


def designs(ear, predict, anchor, default, other, rng_seed):
    """Every design for one ear: name → Outcome."""
    return {
        "today": today(ear(), predict, default, random.Random(rng_seed)),
        "shape": shape_only(ear(), predict, anchor, default, random.Random(rng_seed)),
        "shape+smooth": shape_then_smooth(ear(), predict, anchor, default, random.Random(rng_seed)),
        "shape, other ear": shape_only(ear(), predict, anchor, other, random.Random(rng_seed), from_other_ear=True),
        "shape+smooth, other ear": shape_then_smooth(ear(), predict, anchor, other, random.Random(rng_seed),
                                                     from_other_ear=True),
    }


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    repeats = int(argv[0]) if argv else 3
    rng = random.Random(7)
    measurements = Measurements.bundled()
    cases = measured_cases(measurements) + synthetic_cases(measurements, 6, rng)
    cache = {}
    for listener in (TRAINED, AVERAGE):
        results: dict[str, list] = {}
        for _side, truth, true_hz, model in cases:
            if id(model) not in cache:
                cache[id(model)] = (Predictions(model), stable_anchor(model.freqs, model.curves))
            predict, anchor = cache[id(model)]
            default = float(np.exp(np.median(np.log(model.peaks))))
            for _ in range(repeats):
                seed = rng.random()
                person = Ear.person(listener, random.Random(seed))

                def ear():
                    return Ear(truth, true_hz, listener, random.Random(seed + 1), person)
                best = best_eq_hz(ear(), predict)
                other = best * 2 ** random.Random(seed + 2).gauss(0, 0.05)  # the other ear's result
                for name, outcome in designs(ear, predict, anchor, default, other, seed).items():
                    results.setdefault(name, []).append((best, outcome))
        print(f"\n{listener.name} listener: {len(cases)} ears × {repeats}, error from the smoothest EQ")
        for name, outcomes in results.items():
            print(" ", summarise(name, outcomes))


if __name__ == "__main__":
    main()
