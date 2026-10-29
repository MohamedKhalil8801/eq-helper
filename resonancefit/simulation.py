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
from scipy.special import ndtr

from eq_helper.dsp.resonance import CONTROL_RANGE, Measurements, ResonanceModel
from resonancefit.guided_test import (
    FINE_MIN_DELTA, FIRST_SMOOTH_DELTA, FIRST_SMOOTH_MAX_ROUNDS, SMOOTH_DELTA, SMOOTH_MAX_ROUNDS, SMOOTH_WIDE_DELTA,
    LouderSearch, Round, SmoothnessSearch,
)
from resonancefit.shape_match import ShapeMatch, ShapeModel, band_level

SHAPE_SMOOTH_DELTA = 0.03  # after a confident shape match the smoothness check starts this close
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
            return self.rng.choice([*range(len(round_.options)), None])
        rough = [self.roughness(predict, hz) for hz in round_.options]
        if max(rough) - min(rough) < self.listener.cant_tell:
            return None
        noisy = [r + self.rng.gauss(0, self.listener.smooth_noise) for r in rough]
        return int(np.argmin(noisy))


    def eliminate(self, options, predict, keep=(2, 3)) -> tuple[list[int], int]:
        """Many cards, free to switch between: rule out the clearly worse ones until ``keep`` are left,
        then pick the best of those. Returns (kept indices, best index). Each card's impression wobbles
        once per round (it can be heard again, so it settles); a slip rules out the best-sounding card."""
        rough = np.array([self.roughness(predict, hz) for hz in options])
        seen = rough + np.array([self.rng.gauss(0, self.listener.smooth_noise) for _ in options])
        order = [int(i) for i in np.argsort(seen)]
        if self._slipped():
            order = order[1:] + order[:1]
        close = [i for i in order if seen[i] <= seen[order[0]] + self.listener.cant_tell]
        kept = order[:min(max(len(close), keep[0]), keep[1])]
        best = self.rng.choice(kept) if self._slipped() else kept[0]
        return kept, best


# ── test designs ────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    hz: float
    questions: int
    stages: str
    cards: int = 0          # options heard in all (how much listening the rounds took)


def run_smoothness(ear: Ear, predict, centre: float, delta: float, rng, **options) -> tuple[float, int]:
    search = SmoothnessSearch(centre, delta=delta, rng=rng, **options)
    while not search.done:
        search.answer(ear.smoothest(search.options(), predict))
    return search.result(), len(search.answers)


def louder_then_smooth(ear: Ear, predict, default_hz: float, rng) -> Outcome:
    """The earlier test (still the one without a starting point): the louder test, then the smoothness check."""
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


def shape_then_smooth(ear: Ear, predict, shape: ShapeModel, start: float | None, rng,
                      from_other_ear=False) -> Outcome:
    """The new primary search, as the guided test runs it: the shape match from a starting point,
    then the fine smoothness check."""
    search = ShapeMatch(shape, start=start, from_other_ear=from_other_ear)
    while not search.done:
        search.answer(ear.match(search.question()))
    found = search.result()
    hz, n = run_smoothness(ear, predict, found.hz, SHAPE_SMOOTH_DELTA if found.confident else SMOOTH_DELTA, rng,
                           max_rounds=SMOOTH_MAX_ROUNDS + 2, min_delta=FINE_MIN_DELTA)
    return Outcome(hz, len(search.answers) + n, "shape+smooth")


def smooth_only(ear: Ear, predict, start: float, delta: float, rng) -> Outcome:
    """Today's test: the smoothness check alone, from a starting point, down to options ~50 Hz apart."""
    hz, n = run_smoothness(ear, predict, start, delta, rng, max_rounds=FIRST_SMOOTH_MAX_ROUNDS,
                           min_delta=FINE_MIN_DELTA)
    return Outcome(hz, n, "smooth")


def shape_only(ear: Ear, predict, shape: ShapeModel, start: float | None, rng, from_other_ear=False) -> Outcome:
    search = ShapeMatch(shape, start=start, from_other_ear=from_other_ear)
    while not search.done:
        search.answer(ear.match(search.question()))
    return Outcome(search.result().hz, len(search.answers), "shape")


# ── the scorecard: every answer scored against every possible resonance ──────────────────────
SCORE_OCTAVES = 1 / 192
SCORE_NOISE = 0.3          # dB: how much the scorecard assumes a roughness impression wobbles
SCORE_SAME = 0.08          # dB: closer than this, "Can't tell them apart" is likely
SCORE_LAPSE = 0.05
SCORE_WIDTH_HZ = 100.0     # done when the middle 80 % of the belief is this narrow
SCORE_MAX_ROUNDS = 14
FUNNEL_FIRST = 8           # cards in the first round, across the whole range
FUNNEL_SECOND = 5
FUNNEL_MAX_ROUNDS = 8


class RoughnessTable:
    """For every possible true resonance (rows) and every EQ (columns): how rough the treble should
    sound, from the calibration's model alone (the same measure as :meth:`Ear.roughness`)."""

    def __init__(self, predict, hz_range=CONTROL_RANGE):
        lo, hi = (math.log2(v) for v in hz_range)
        self.hz = 2.0 ** np.arange(lo, hi + 1e-9, SCORE_OCTAVES)
        mask = (GRID >= 4500) & (GRID <= 11000)
        g = np.log2(GRID[mask])
        trend = np.vstack([g, np.ones_like(g)]).T
        project = trend @ np.linalg.pinv(trend)
        curves = np.array([predict(h)[1][mask] for h in self.hz])
        self.table = np.empty((len(self.hz), len(self.hz)))
        for i in range(len(self.hz)):
            r = curves[i][None, :] - curves
            r = r - r @ project.T
            self.table[i] = np.sqrt(np.mean(r * r, axis=1))

    def index(self, hz: float) -> int:
        return int(np.argmin(np.abs(np.log(self.hz / hz))))


class Scorecard:
    """A belief over the resonance (the table's rows), updated by every pick, never thrown away."""

    def __init__(self, table: RoughnessTable, start: float | None = None):
        self.t = table
        x = np.log2(table.hz)
        prior = np.full(len(x), 0.3 / len(x))
        if start is not None:
            bump = np.exp(-0.5 * ((x - math.log2(start)) / 0.1) ** 2)
            prior = prior + 0.7 * bump / bump.sum()
        self.log_p = np.log(prior / prior.sum())

    def posterior(self) -> np.ndarray:
        p = np.exp(self.log_p - self.log_p.max())
        return p / p.sum()

    def quantile(self, q: float) -> float:
        return float(np.interp(q, np.cumsum(self.posterior()), self.t.hz))

    @property
    def width(self) -> float:
        return self.quantile(0.9) - self.quantile(0.1)

    def estimate(self) -> float:
        return self.quantile(0.5)

    @staticmethod
    def _better(d):
        """P(an option is heard smoother than another that is ``d`` dB rougher in truth)."""
        return (1 - SCORE_LAPSE) * ndtr(d / (SCORE_NOISE * math.sqrt(2))) + SCORE_LAPSE / 2

    def picked(self, shown: list[int], kept: list[int], best: int) -> None:
        """``best`` (a column) was picked over the other ``kept``; the rest of ``shown`` were ruled out
        as worse than it. Each comparison with the pick counts, but they all share the pick's own
        wobble, so together they count as about √n comparisons rather than n."""
        r = self.t.table
        others = [k for k in shown if k != best]
        weight = 1 / math.sqrt(len(others))
        for k in others:
            self.log_p += weight * np.log(self._better(r[:, k] - r[:, best]))

    def same(self, a: int, b: int) -> None:
        d = self.t.table[:, b] - self.t.table[:, a]
        s = SCORE_NOISE * math.sqrt(2)
        p = ndtr((SCORE_SAME - d) / s) - ndtr((-SCORE_SAME - d) / s)
        self.log_p += np.log((1 - SCORE_LAPSE) * p + SCORE_LAPSE / 3)

    def best_pair(self, step: int = 2) -> tuple[int, int]:
        """The A/B pair whose answer should narrow the belief most."""
        p = self.posterior()
        cols = np.arange(0, len(self.t.hz), step)
        i, j = np.triu_indices(len(cols), k=1)
        a, b = cols[i], cols[j]
        d = self.t.table[:, b] - self.t.table[:, a]                       # (states, pairs): >0 → A smoother
        s = SCORE_NOISE * math.sqrt(2)
        pa = ndtr((d - SCORE_SAME) / s)
        pb = ndtr((-d - SCORE_SAME) / s)
        probs = np.stack([pa, pb, np.clip(1 - pa - pb, 0, 1)]) * (1 - SCORE_LAPSE) + SCORE_LAPSE / 3
        joint = probs * p[None, :, None]
        outcome = joint.sum(axis=1)
        cond = joint / np.maximum(outcome[:, None, :], 1e-300)
        entropy = -(cond * np.log(np.maximum(cond, 1e-300))).sum(axis=1)
        k = int(np.argmin((outcome * entropy).sum(axis=0)))
        return int(a[k]), int(b[k])


def _cards(table: RoughnessTable, lo: float, hi: float, n: int) -> list[int]:
    """``n`` distinct columns spread evenly (log) from ``lo`` to ``hi``."""
    cols = sorted({table.index(h) for h in np.geomspace(lo, hi, n)})
    while len(cols) < n:  # too narrow for distinct columns: widen by one step each side
        cols = sorted(set(cols) | {max(cols[0] - 1, 0), min(cols[-1] + 1, len(table.hz) - 1)})
    return cols[:n]


def ab_scorecard(ear: Ear, predict, table: RoughnessTable, start: float, rng, overview: bool = False) -> Outcome:
    """Two EQs a round, the pair chosen and every answer scored by the scorecard. ``overview``: first a
    round of 8 cards across the range (rule out, keep 2–3, pick the best), scored the same way."""
    card = Scorecard(table, start)
    rounds = cards = 0
    if overview:
        shown = _first_round(table)
        kept, best = ear.eliminate([table.hz[c] for c in shown], predict)
        card.picked(shown, [shown[i] for i in kept], shown[best])
        rounds, cards = 1, len(shown)
    while rounds < SCORE_MAX_ROUNDS and (rounds < 3 or card.width > SCORE_WIDTH_HZ):
        a, b = card.best_pair()
        options = [a, b]
        rng.shuffle(options)
        answer = ear.smoothest(Round(tuple(table.hz[c] for c in options)), predict)
        if answer is None:
            card.same(a, b)
        else:
            card.picked(options, options, options[answer])
        rounds, cards = rounds + 1, cards + 2
    return Outcome(card.estimate(), rounds, "ab", cards)


def _first_round(table: RoughnessTable) -> list[int]:
    lo, hi = table.hz[0], table.hz[-1]
    step = (hi / lo) ** (1 / FUNNEL_FIRST)
    return _cards(table, lo * step ** 0.5, hi / step ** 0.5, FUNNEL_FIRST)


def funnel_today(ear: Ear, predict, table: RoughnessTable, rng) -> Outcome:
    """Elimination with today's rule: 8 cards across the range, keep 2–3; 5 cards across what was kept
    (plus a step each side), keep 2–3 and pick the best; then today's smoothness check from that pick."""
    shown = _first_round(table)
    kept, _best = ear.eliminate([table.hz[c] for c in shown], predict)
    hz = sorted(table.hz[shown[i]] for i in kept)
    step = (table.hz[shown[-1]] / table.hz[shown[0]]) ** (1 / (len(shown) - 1))
    second = _cards(table, max(hz[0] / step, table.hz[0]), min(hz[-1] * step, table.hz[-1]), FUNNEL_SECOND)
    _kept2, best2 = ear.eliminate([table.hz[c] for c in second], predict)
    spacing = math.log2(table.hz[second[-1]] / table.hz[second[0]]) / (len(second) - 1)
    hz, n = run_smoothness(ear, predict, table.hz[second[best2]], spacing, rng,
                           max_rounds=FUNNEL_MAX_ROUNDS - 2, min_delta=FINE_MIN_DELTA)
    return Outcome(hz, 2 + n, "funnel", len(shown) + len(second) + 3 * n)


def funnel_scorecard(ear: Ear, predict, table: RoughnessTable, start: float, rng) -> Outcome:
    """Elimination, every pick scored: 8 cards across the range, then 5, then 3 at a time, each round
    spread over the middle of the belief; done when the belief is narrow."""
    card = Scorecard(table, start)
    shown = _first_round(table)
    rounds, cards = 0, 0
    while True:
        keep = (2, 3) if len(shown) > 3 else (1, 1)
        kept, best = ear.eliminate([table.hz[c] for c in shown], predict, keep=keep)
        card.picked(shown, [shown[i] for i in kept], shown[best])
        rounds, cards = rounds + 1, cards + len(shown)
        if rounds >= FUNNEL_MAX_ROUNDS or (rounds >= 4 and card.width <= SCORE_WIDTH_HZ):
            break
        n = FUNNEL_SECOND if rounds == 1 else 3
        lo, hi = card.quantile(0.05 if n > 3 else 0.15), card.quantile(0.95 if n > 3 else 0.85)
        middle = card.estimate()
        lo, hi = min(lo, middle * 2 ** -0.004), max(hi, middle * 2 ** 0.004)  # options at least ~50 Hz apart
        shown = _cards(table, lo, hi, n)
    return Outcome(card.estimate(), rounds, "funnel+score", cards)


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
    cards = np.array([o.cards or 3 * o.questions for _t, o in outcomes])
    return (f"{name:<22} median {np.median(errors):4.0f} Hz   90% {np.percentile(errors, 90):5.0f} Hz   "
            f"within 50 Hz {np.mean(errors <= 50):4.0%}   within 100 Hz {np.mean(errors <= 100):4.0%}   "
            f"rounds {np.mean(questions):4.1f}   cards heard {np.mean(cards):4.1f}")


def designs(ear, predict, table, default, rng_seed):
    """The first ear's designs: name → Outcome. (The loudness-match designs: shape_then_smooth.)"""
    return {
        "today (3 cards)": smooth_only(ear(), predict, default, FIRST_SMOOTH_DELTA, random.Random(rng_seed)),
        "funnel, today's rule": funnel_today(ear(), predict, table, random.Random(rng_seed)),
        "A/B scorecard": ab_scorecard(ear(), predict, table, default, random.Random(rng_seed)),
        "funnel + scorecard": funnel_scorecard(ear(), predict, table, default, random.Random(rng_seed)),
        "8 cards, then A/B": ab_scorecard(ear(), predict, table, default, random.Random(rng_seed), overview=True),
    }


def main(argv=None) -> None:
    """``python -m resonancefit.simulation [repeats] [measurement folder]`` (default: the bundled set)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    repeats = int(argv[0]) if argv else 3
    rng = random.Random(7)
    measurements = Measurements.from_folder(argv[1]) if len(argv) > 1 else Measurements.bundled()
    print(measurements.name)
    measured = measured_cases(measurements)
    cases = measured + synthetic_cases(measurements, 6, rng)
    cache = {}
    for listener in (TRAINED, AVERAGE):
        results: dict[str, list] = {}
        for n_case, (_side, truth, true_hz, model) in enumerate(cases):
            if id(model) not in cache:
                predictions = Predictions(model)
                cache[id(model)] = (predictions, RoughnessTable(predictions))
            predict, table = cache[id(model)]
            default = float(np.exp(np.median(np.log(model.peaks))))
            for _ in range(repeats):
                seed = rng.random()
                person = Ear.person(listener, random.Random(seed))

                def ear():
                    return Ear(truth, true_hz, listener, random.Random(seed + 1), person)
                best = best_eq_hz(ear(), predict)
                for name, outcome in designs(ear, predict, table, default, seed).items():
                    results.setdefault(name, []).append((best, outcome, n_case < len(measured)))
        print(f"\n{listener.name} listener: {len(cases)} ears × {repeats}, error from the smoothest EQ")
        for name, outcomes in results.items():
            print(" ", summarise(name, [(t, o) for t, o, _m in outcomes]))
        print(f"  measured insertions only ({len(measured)} ears, left out of the model):")
        for name, outcomes in results.items():
            print(" ", summarise(name, [(t, o) for t, o, m in outcomes if m]))


if __name__ == "__main__":
    main()
