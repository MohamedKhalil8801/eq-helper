"""The scorecard behind "Find it for me": every smoothness answer scored against every possible resonance.

The calibration's model knows the treble for any resonance, so for every
possible true resonance it can tell how rough the treble should sound
through an EQ made for any other (:class:`RoughnessTable`). An answer —
"this one is smoothest", "these are worse" — then counts for every
resonance under which it makes sense, by how clearly it should have been
heard, and against the rest. No answer is final: one slip only dents the
scores, and later answers outvote it.

:class:`PickSearch` is the search the guided test runs per ear: an
overview round of eight EQs across the whole range (the listener rules
out the clearly worse ones and picks the smoothest), then A/B rounds whose
pair is chosen for what its answer should tell, until the belief is about
100 Hz wide. In simulation (:mod:`resonancefit.simulation`) this beat the
three-card smoothness check clearly, above all in its worst results.
"""

from __future__ import annotations

import math
import random

import numpy as np
from scipy.special import ndtr

from eq_helper.dsp.resonance import CONTROL_RANGE

POINTS = np.geomspace(4500.0, 11000.0, 300)  # where roughness is judged (see RoughnessTable)
STEP_OCTAVES = 1 / 192     # spacing of the possible resonances (and of the EQs the test can play)
NOISE_DB = 0.3             # how much a roughness impression is assumed to wobble
SAME_DB = 0.08             # closer than this, "Can't tell them apart" is likely
LAPSE = 0.05               # any answer, whatever was heard
START_SHARE = 0.7          # the prior: this much belief near the starting point …
START_SD = 0.1             # … (octaves), the rest spread evenly
OVERVIEW_CARDS = 8
PAIR_STEP = 2              # A/B pairs are chosen from every second EQ (1/96 octave)
DONE_WIDTH_HZ = 100.0      # done when the middle 80 % of the belief is this narrow …
MIN_ROUNDS = 3
MAX_ROUNDS = 14            # … or after this many rounds


class RoughnessTable:
    """``table[i, j]``: how rough (RMS dB, after the overall tilt) the treble should sound through an EQ
    made for resonance ``hz[j]`` when the true resonance is ``hz[i]``, from the model alone.

    ``curve(hz)`` returns the model's response for a resonance at ``hz`` on :data:`POINTS`.
    """

    def __init__(self, curve, hz_range=CONTROL_RANGE):
        lo, hi = (math.log2(v) for v in hz_range)
        self.hz = 2.0 ** np.arange(lo, hi + 1e-9, STEP_OCTAVES)
        x = np.log2(POINTS)
        trend = np.vstack([x, np.ones_like(x)]).T
        project = trend @ np.linalg.pinv(trend)
        curves = np.array([curve(h) for h in self.hz])
        self.table = np.empty((len(self.hz), len(self.hz)))
        for i in range(len(self.hz)):
            r = curves[i][None, :] - curves
            r = r - r @ project.T
            self.table[i] = np.sqrt(np.mean(r * r, axis=1))

    @classmethod
    def for_model(cls, model, hz_range=CONTROL_RANGE) -> RoughnessTable:
        """From a :class:`~eq_helper.dsp.resonance.ResonanceModel` (about a second)."""
        log_f, log_points = np.log(model.freqs), np.log(POINTS)
        return cls(lambda hz: np.interp(log_points, log_f, model.predict(hz)), hz_range)

    def index(self, hz: float) -> int:
        return int(np.argmin(np.abs(np.log(self.hz / hz))))

    def spread(self, lo: float, hi: float, n: int) -> list[int]:
        """``n`` distinct EQs (indices) spread evenly in log frequency from ``lo`` to ``hi``."""
        cols = sorted({self.index(h) for h in np.geomspace(lo, hi, n)})
        while len(cols) < n:  # too narrow for distinct EQs: widen by one step each side
            cols = sorted(set(cols) | {max(cols[0] - 1, 0), min(cols[-1] + 1, len(self.hz) - 1)})
        return cols[:n]

    def overview(self, n: int = OVERVIEW_CARDS) -> list[int]:
        """``n`` EQs evenly across the whole range (each in the middle of its share)."""
        lo, hi = self.hz[0], self.hz[-1]
        step = (hi / lo) ** (1 / n)
        return self.spread(lo * step ** 0.5, hi / step ** 0.5, n)


def _heard_better(d):
    """P(one EQ is heard smoother than another that is ``d`` dB rougher in truth)."""
    return (1 - LAPSE) * ndtr(d / (NOISE_DB * math.sqrt(2))) + LAPSE / 2


class Scorecard:
    """A belief over the resonance (the table's rows), from a prior and every answer so far."""

    def __init__(self, table: RoughnessTable, start: float | None = None, start_sd: float = START_SD):
        self.t = table
        x = np.log2(table.hz)
        prior = np.full(len(x), (1 - START_SHARE) / len(x) if start is not None else 1.0 / len(x))
        if start is not None:
            bump = np.exp(-0.5 * ((x - math.log2(start)) / start_sd) ** 2)
            prior = prior + START_SHARE * bump / bump.sum()
        self.log_prior = np.log(prior / prior.sum())
        self.log_p = self.log_prior.copy()

    def posterior(self) -> np.ndarray:
        p = np.exp(self.log_p - self.log_p.max())
        return p / p.sum()

    def quantile(self, q: float) -> float:
        return float(np.interp(q, np.cumsum(self.posterior()), self.t.hz))

    @property
    def width(self) -> float:
        """Hz holding the middle 80 % of the belief."""
        return self.quantile(0.9) - self.quantile(0.1)

    def estimate(self) -> float:
        return self.quantile(0.5)

    def picked(self, shown, best: int) -> None:
        """EQ ``best`` (an index) was picked as smoothest of ``shown`` (the others ruled out or not
        chosen). The comparisons all share the pick's own wobble, so together they count as about √n
        comparisons rather than n."""
        r = self.t.table
        others = [k for k in shown if k != best]
        weight = 1 / math.sqrt(len(others)) if others else 0.0
        for k in others:
            self.log_p += weight * np.log(_heard_better(r[:, k] - r[:, best]))

    def same(self, a: int, b: int) -> None:
        """EQs ``a`` and ``b`` sounded alike ("Can't tell them apart")."""
        d = self.t.table[:, b] - self.t.table[:, a]
        s = NOISE_DB * math.sqrt(2)
        p = ndtr((SAME_DB - d) / s) - ndtr((-SAME_DB - d) / s)
        self.log_p += np.log((1 - LAPSE) * p + LAPSE / 3)

    def best_pair(self, step: int = PAIR_STEP) -> tuple[int, int]:
        """The A/B pair whose answer should narrow the belief most: expected entropy after it, lowest."""
        p = self.posterior()
        cols = np.arange(0, len(self.t.hz), step)
        i, j = np.triu_indices(len(cols), k=1)
        a, b = cols[i], cols[j]
        d = self.t.table[:, b] - self.t.table[:, a]                       # (states, pairs): > 0 → A smoother
        s = NOISE_DB * math.sqrt(2)
        pa = ndtr((d - SAME_DB) / s)
        pb = ndtr((-d - SAME_DB) / s)
        probs = np.stack([pa, pb, np.clip(1 - pa - pb, 0, 1)]) * (1 - LAPSE) + LAPSE / 3
        joint = probs * p[None, :, None]
        outcome = joint.sum(axis=1)
        cond = joint / np.maximum(outcome[:, None, :], 1e-300)
        entropy = -(cond * np.log(np.maximum(cond, 1e-300))).sum(axis=1)
        k = int(np.argmin((outcome * entropy).sum(axis=0)))
        return int(a[k]), int(b[k])


class PickSearch:
    """One ear: an overview round of eight EQs, then A/B rounds, all scored on one :class:`Scorecard`.

    ``options()`` is a round's EQs as resonance frequencies (Hz), low to high
    (shown in that order: the sound gets smoother card by card towards the
    right one, then harsher again, which makes the smoothest easy to find); ``answer(best, ruled_out)`` takes the index of the smoothest one
    (``None``: can't tell them apart) and, in the overview, the indices the
    listener ruled out (kept with the answer; every EQ but the pick counts
    as heard rougher than it). ``overview=False`` starts with A/B.
    """

    def __init__(self, table: RoughnessTable, start: float | None = None, rng: random.Random | None = None,
                 overview: bool = True, max_rounds: int = MAX_ROUNDS):
        self.table = table
        self.start = start
        self.rng = rng or random.Random()
        self.overview = overview
        self.max_rounds = max_rounds
        self.answers: list[tuple[tuple[int, ...], int | None, frozenset[int]]] = []  # (shown, best, ruled out)
        self._card = Scorecard(table, start)
        self._start_width = self._card.width
        self._round: tuple[int, ...] | None = None

    @property
    def card(self) -> Scorecard:
        return self._card

    @property
    def in_overview(self) -> bool:
        """The current round is the overview (many EQs, some can be ruled out)."""
        return self.overview and not self.answers

    @property
    def done(self) -> bool:
        n = len(self.answers)
        return n >= self.max_rounds or (n >= MIN_ROUNDS and self._card.width <= DONE_WIDTH_HZ)

    def _next(self) -> tuple[int, ...]:
        if self.in_overview:
            shown = self.table.overview()
        else:
            shown = list(self._card.best_pair())
        return tuple(sorted(shown))  # low to high: smoother card by card, then harsher again

    def options(self) -> tuple[float, ...]:
        """The current round's EQs (Hz), stable until it's answered or undone."""
        if self.done:
            raise RuntimeError("This ear is finished.")
        if self._round is None:
            self._round = self._next()
        return tuple(float(self.table.hz[k]) for k in self._round)

    def answer(self, best: int | None, ruled_out=()) -> None:
        self.options()
        shown = self._round
        out = frozenset(int(i) for i in ruled_out if i != best)
        self.answers.append((shown, best, out))
        self._apply(shown, best)
        self._round = None

    def _apply(self, shown, best) -> None:
        if best is not None:
            self._card.picked(shown, shown[best])
        elif len(shown) == 2:
            self._card.same(*shown)
        # can't tell in the overview: nothing learnt

    def undo(self) -> bool:
        if not self.answers:
            return False
        shown, _best, _out = self.answers.pop()
        self._card = Scorecard(self.table, self.start)
        for s, b, _o in self.answers:
            self._apply(s, b)
        self._round = shown
        return True

    def result(self) -> float:
        return self._card.estimate()

    def estimate(self) -> float:
        return self._card.estimate()

    def progress(self) -> float:
        """0…1 from how far the belief has narrowed (or the rounds used)."""
        if self.done:
            return 1.0
        start = max(self._start_width, DONE_WIDTH_HZ * 1.01)
        narrowed = math.log(start) - math.log(min(max(self._card.width, DONE_WIDTH_HZ), start))
        return min(0.9, max(narrowed / math.log(start / DONE_WIDTH_HZ), len(self.answers) / self.max_rounds))
