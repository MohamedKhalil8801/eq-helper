"""The guided "Find my resonance" test, without Qt.

Two listening stages per ear find a resonance (see GLOSSARY):

- :class:`LouderSearch` ("Louder test"): A/B rounds of equal-level tones.
  It keeps a belief over where the peak is (and how wide), updated after
  every answer with a simple listener model, and asks the pair that should
  teach it most. "About the same" is information too: the peak lies midway
  between the two tones, *or far from both* — two tones on the flat part
  beside a narrow peak sound alike, so that region is never thrown away.
- :class:`SmoothnessSearch` ("Smoothness check"): A/B/C rounds of pink
  noise through three EQs whose resonance sits just below, at and just
  above the current guess; the smoothest one wins.

:class:`GuidedTest` runs both stages for each ear, first for the primary
resonance and then, if the customer wants, for the upper resonance. It keeps
one undo history across stages and ears. Option order is shuffled every
round; options are plain frequencies, the UI only ever shows letters.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr

from eq_helper.dsp.resonance import CONTROL_RANGE, PERSONAL_TREBLE_RANGE

# ── the listener model behind the louder test ───────────────────────────────
PEAK_DB = 6.0                # how much louder a tone right on the peak sounds than the flat part
PEAK_WIDTHS = (0.06, 0.1, 0.18)        # octaves (Gaussian σ) a primary resonance may have
UPPER_PEAK_WIDTHS = (0.08, 0.14, 0.24)
JND_DB = 1.0                 # smaller differences are heard as "About the same"
NOISE_DB = 0.8               # how much the impression wobbles from round to round
LAPSE = 0.05                 # a slip: any answer, whatever was heard
GRID_OCTAVES = 1 / 192       # resolution of the belief over the peak's place
CANDIDATE_OCTAVES = 1 / 48   # spacing of the tones the test can play
PAIR_OCTAVES = (0.04, 0.4)   # how far apart the two tones of a round may be

LOUDER_MAX_ROUNDS = 12
LOUDER_MIN_ROUNDS = 6
SURPRISE = 0.1               # an answer the final belief gave less than this chance is a surprise
LOUDER_STOP_RATIO = 1.04     # done when 80 % of the belief lies within this ratio
LOUDER_CONFIDENT_RATIO = 1.08
CREDIBLE = 0.8
NO_PEAK_SAME_SHARE = 0.6     # this share of "About the same": no clear peak heard
KEEP_ESTIMATE_RATIO = 1.15   # leaving mid-test keeps the louder test's estimate once it's this narrow
OPENING_SAMES = 4            # this many opening "About the same" answers end the louder test

SMOOTH_DELTA = 0.06          # octaves between the options
SMOOTH_WIDE_DELTA = 0.12     # when the louder test wasn't sure: start wider
UPPER_SMOOTH_DELTA = 0.08
UPPER_SMOOTH_WIDE_DELTA = 0.15
SMOOTH_MIN_DELTA = 0.015
SMOOTH_MAX_ROUNDS = 6
SMOOTH_MAX_MOVES = 2         # edge picks in a row at one spacing before it halves

LOUDER, SMOOTHNESS = "louder", "smoothness"
PRIMARY, UPPER = "primary", "upper"


@dataclass(frozen=True)
class Round:
    """One question: ``options`` in display order (A, B, C) as frequencies in Hz."""

    options: tuple[float, ...]

    def letter(self, index: int) -> str:
        return "ABC"[index]


@dataclass(frozen=True)
class LouderResult:
    hz: float                 # best estimate (the belief's median)
    confident: bool           # the belief is narrow
    fell_back: bool           # mostly "About the same": no clear peak was heard
    rounds: int
    spread: float             # ratio covering 80 % of the belief


def _clip(hz: float, lo: float, hi: float) -> float:
    return float(min(max(hz, lo), hi))


def outcome_probabilities(difference_db):
    """P(first louder), P(second louder), P(about the same) for a level difference (first − second)."""
    d = np.asarray(difference_db, dtype=float)
    first = ndtr((d - JND_DB) / NOISE_DB)
    second = ndtr((-d - JND_DB) / NOISE_DB)
    same = np.clip(1.0 - first - second, 0.0, 1.0)
    return (1.0 - LAPSE) * np.stack([first, second, same]) + LAPSE / 3.0


class LouderSearch:
    """Which of two equal-level tones is louder: a Bayesian search for the peak in log frequency."""

    def __init__(self, hz_range=CONTROL_RANGE, rng: random.Random | None = None,
                 max_rounds: int = LOUDER_MAX_ROUNDS, stop_ratio: float = LOUDER_STOP_RATIO,
                 widths=PEAK_WIDTHS):
        self.lo, self.hi = (float(v) for v in hz_range)
        self.rng = rng or random.Random()
        self.max_rounds = max_rounds
        self.stop_ratio = stop_ratio
        lo2, hi2 = math.log2(self.lo), math.log2(self.hi)
        self.grid = np.linspace(lo2, hi2, max(2, round((hi2 - lo2) / GRID_OCTAVES) + 1))
        cand = np.linspace(lo2, hi2, max(2, round((hi2 - lo2) / CANDIDATE_OCTAVES) + 1))
        self.candidates = tuple(float(2.0 ** c) for c in cand)
        self._index = {hz: i for i, hz in enumerate(self.candidates)}
        w = np.asarray(widths, dtype=float)[:, None, None]
        # perceived level (dB above the flat part) of each candidate tone, for every peak place and width
        self._levels = PEAK_DB * np.exp(-0.5 * ((cand[None, None, :] - self.grid[None, :, None]) / w) ** 2)
        i, j = np.triu_indices(len(cand), k=1)
        apart = cand[j] - cand[i]
        keep = (apart >= PAIR_OCTAVES[0] - 1e-9) & (apart <= PAIR_OCTAVES[1] + 1e-9)
        self._pairs = (i[keep], j[keep])
        self.answers: list[tuple[Round, float | None]] = []  # (round, louder Hz or None = "About the same")
        self._round: Round | None = None
        self._posterior = None

    # ── belief ─────────────────────────────────────────────────────────────
    def posterior(self) -> np.ndarray:
        """P(width, peak place) after the answers so far (cached)."""
        if self._posterior is None:
            log_p = np.zeros(self._levels.shape[:2])
            for round_, louder in self.answers:
                a, b = (self._index[hz] for hz in round_.options)
                probs = outcome_probabilities(self._levels[:, :, a] - self._levels[:, :, b])
                outcome = 2 if louder is None else (0 if louder == round_.options[0] else 1)
                log_p += np.log(probs[outcome])
            p = np.exp(log_p - log_p.max())
            self._posterior = p / p.sum()
        return self._posterior

    def _quantiles(self, *qs: float) -> list[float]:
        marginal = self.posterior().sum(axis=0)
        cdf = np.cumsum(marginal)
        return [float(np.interp(q, cdf, self.grid)) for q in qs]

    @property
    def spread(self) -> float:
        """Frequency ratio holding the middle 80 % of the belief."""
        low, high = self._quantiles((1 - CREDIBLE) / 2, (1 + CREDIBLE) / 2)
        return 2.0 ** (high - low)

    def estimate(self) -> float:
        """Best guess so far: the belief's median."""
        return _clip(2.0 ** self._quantiles(0.5)[0], self.lo, self.hi)

    @property
    def same_share(self) -> float:
        if not self.answers:
            return 0.0
        return sum(louder is None for _r, louder in self.answers) / len(self.answers)

    def surprises(self) -> int:
        """Answers the current belief finds very unlikely: slips, or answers that disagree."""
        p = self.posterior()
        count = 0
        for round_, louder in self.answers:
            a, b = (self._index[hz] for hz in round_.options)
            probs = outcome_probabilities(self._levels[:, :, a] - self._levels[:, :, b])
            outcome = 2 if louder is None else (0 if louder == round_.options[0] else 1)
            count += float((probs[outcome] * p).sum()) < SURPRISE
        return count

    @property
    def done(self) -> bool:
        n = len(self.answers)
        if n >= self.max_rounds:
            return True
        if n >= OPENING_SAMES and all(louder is None for _r, louder in self.answers):
            return True
        # a surprising answer keeps the test going: more rounds settle which answers to trust
        return n >= LOUDER_MIN_ROUNDS and self.spread < self.stop_ratio and self.surprises() == 0

    # ── rounds ─────────────────────────────────────────────────────────────
    def _best_pair(self) -> tuple[int, int]:
        """The pair whose answer is expected to narrow the peak's place most."""
        p = self.posterior()
        pi, pj = self._pairs
        probs = outcome_probabilities(self._levels[:, :, pi] - self._levels[:, :, pj])  # (3, W, F, P)
        joint = (probs * p[None, :, :, None]).sum(axis=1)                               # (3, F, P)
        p_outcome = joint.sum(axis=1)                                                    # (3, P)
        cond = joint / np.maximum(p_outcome[:, None, :], 1e-300)
        entropy = -(cond * np.log(np.maximum(cond, 1e-300))).sum(axis=1)               # (3, P)
        expected = (p_outcome * entropy).sum(axis=0)
        best = float(expected.min())
        near = np.flatnonzero(expected <= best + 1e-3)
        k = int(near[self.rng.randrange(len(near))])
        return int(pi[k]), int(pj[k])

    def options(self) -> Round:
        """The current round (stable until it's answered or undone)."""
        if self.done:
            raise RuntimeError("The louder test is finished.")
        if self._round is None:
            i, j = self._best_pair()
            pair = [self.candidates[i], self.candidates[j]]
            self.rng.shuffle(pair)
            self._round = Round(tuple(pair))
        return self._round

    def answer(self, louder: int | None) -> None:
        """``louder``: index of the louder option in the round, ``None`` = "About the same"."""
        current = self.options()
        self.answers.append((current, None if louder is None else current.options[louder]))
        self._round = None
        self._posterior = None

    def undo(self) -> bool:
        """Take back the last answer; False when there's none."""
        if not self.answers:
            return False
        self._round, _hz = self.answers.pop()
        self._posterior = None
        return True

    def result(self) -> LouderResult:
        if not self.done:
            raise RuntimeError("The louder test isn't finished yet.")
        spread = self.spread
        heard_nothing = self.same_share == 1.0  # a belief from "same" alone only says where the peak isn't
        confident = not heard_nothing and spread <= LOUDER_CONFIDENT_RATIO and self.surprises() <= 1
        fell_back = heard_nothing or (not confident and self.same_share >= NO_PEAK_SAME_SHARE)
        return LouderResult(self.estimate(), confident, fell_back, len(self.answers), spread)

    def progress(self) -> float:
        """0…1, from how far the belief has narrowed (or the rounds used)."""
        if self.done:
            return 1.0
        full = math.log(self.hi / self.lo) / math.log(self.stop_ratio)
        narrowed = math.log(max(math.log(self.hi / self.lo) / math.log(max(self.spread, 1.0001)), 1.0))
        by_spread = narrowed / math.log(full) if full > 1 else 1.0
        return min(0.9, max(by_spread, len(self.answers) / self.max_rounds))


class SmoothnessSearch:
    """Which of three EQs (resonance a little low / as found / a little high) is smoothest."""

    def __init__(self, center_hz: float, delta: float = SMOOTH_DELTA, hz_range=CONTROL_RANGE,
                 rng: random.Random | None = None, max_rounds: int = SMOOTH_MAX_ROUNDS,
                 min_delta: float = SMOOTH_MIN_DELTA):
        self.lo, self.hi = (float(v) for v in hz_range)
        self.rng = rng or random.Random()
        self.max_rounds = max_rounds
        self.min_delta = min_delta
        self.center = _clip(center_hz, self.lo, self.hi)
        self.delta = self.start_delta = float(delta)
        self.moves = 0               # edge picks in a row at this spacing
        self.cant_tell = False
        self.answers: list[tuple[float, float, int, Round, float | None]] = []
        self._round: Round | None = None

    def _triple(self) -> tuple[float, float, float]:
        """Low, middle, high: kept inside the range (shifted inwards at the edges)."""
        step = 2.0 ** self.delta
        mid = _clip(self.center, self.lo * step, self.hi / step)
        return mid / step, mid, mid * step

    @property
    def done(self) -> bool:
        return self.cant_tell or self.delta < self.min_delta or len(self.answers) >= self.max_rounds

    def options(self) -> Round:
        if self.done:
            raise RuntimeError("The smoothness check is finished.")
        if self._round is None:
            triple = list(self._triple())
            self.rng.shuffle(triple)
            self._round = Round(tuple(triple))
        return self._round

    def answer(self, smoothest: int | None) -> None:
        """``smoothest``: index of the smoothest option, ``None`` = "Can't tell them apart"."""
        current = self.options()
        hz = None if smoothest is None else current.options[smoothest]
        self.answers.append((self.center, self.delta, self.moves, current, hz))
        _low, mid, _high = self._triple()
        if hz is None:
            self.cant_tell = True
            self.center = mid
        elif hz == mid:
            self.center = mid
            self.delta /= 2
            self.moves = 0
        else:
            self.center = hz
            self.moves += 1
            if self.moves >= SMOOTH_MAX_MOVES:
                self.delta /= 2
                self.moves = 0
        self._round = None

    def undo(self) -> bool:
        if not self.answers:
            return False
        self.center, self.delta, self.moves, self._round, _hz = self.answers.pop()
        self.cant_tell = False
        return True

    def result(self) -> float:
        return _clip(self.center, self.lo, self.hi)

    def estimate(self) -> float:
        return self.result()

    def progress(self) -> float:
        if self.done:
            return 1.0
        halvings = math.log2(self.start_delta / self.min_delta) + 1
        by_delta = math.log2(max(self.start_delta / self.delta, 1.0)) / halvings
        return min(0.9, max(by_delta, len(self.answers) / self.max_rounds))


@dataclass
class _Stage:
    side: str
    kind: str        # LOUDER / SMOOTHNESS
    target: str      # PRIMARY / UPPER
    search: LouderSearch | SmoothnessSearch


@dataclass
class GuidedTest:
    """Both stages for each ear in turn — the primary resonance, then optionally the upper one.

    ``default_hz``: per side, where the primary smoothness check starts when
    the louder test heard no clear peak. ``status``:

    - "testing": a round is waiting for an answer;
    - "unsure": a louder test ended with answers pointing to more than one
      place — redo the ear, use the estimate anyway, or adjust by hand;
    - "upper_offer": both primary resonances are found; the upper treble is
      offered (``start_upper()`` / ``skip_upper()``);
    - "finished".

    ``upper_results[side]`` is ``None`` when no clear upper peak was heard
    (the upper treble then stays as it is).
    """

    sides: tuple[str, ...]
    default_hz: dict[str, float] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    hz_range: tuple[float, float] = CONTROL_RANGE
    upper_range: tuple[float, float] = PERSONAL_TREBLE_RANGE
    offer_upper: bool = True
    stages: list[_Stage] = field(default_factory=list)
    results: dict[str, float] = field(default_factory=dict)
    upper_results: dict[str, float | None] = field(default_factory=dict)
    status: str = "testing"

    def __post_init__(self):
        self.sides = tuple(self.sides)
        if not self.sides:
            raise ValueError("Nothing to test: no ears.")
        self.stages = [self._louder(self.sides[0], PRIMARY)]

    def _louder(self, side: str, target: str) -> _Stage:
        if target == UPPER:
            return _Stage(side, LOUDER, UPPER, LouderSearch(self.upper_range, self.rng, widths=UPPER_PEAK_WIDTHS))
        return _Stage(side, LOUDER, PRIMARY, LouderSearch(self.hz_range, self.rng))

    def _smoothness(self, side: str, target: str, center: float, wide: bool) -> _Stage:
        if target == UPPER:
            delta, hz_range = (UPPER_SMOOTH_WIDE_DELTA if wide else UPPER_SMOOTH_DELTA), self.upper_range
        else:
            delta, hz_range = (SMOOTH_WIDE_DELTA if wide else SMOOTH_DELTA), self.hz_range
        return _Stage(side, SMOOTHNESS, target, SmoothnessSearch(center, delta, hz_range, self.rng))

    @property
    def stage(self) -> _Stage:
        return self.stages[-1]

    @property
    def side(self) -> str:
        return self.stage.side

    @property
    def kind(self) -> str:
        return self.stage.kind

    @property
    def target(self) -> str:
        return self.stage.target

    @property
    def round_number(self) -> int:
        """1-based number of the current round within this stage."""
        return len(self.stage.search.answers) + 1

    def _results_for(self, target: str) -> dict:
        return self.upper_results if target == UPPER else self.results

    def options(self) -> Round:
        return self.stage.search.options()

    def answer(self, choice: int | None) -> None:
        if self.status != "testing":
            raise RuntimeError(f"Can't answer while {self.status}.")
        self.stage.search.answer(choice)
        self._advance()

    def _advance(self) -> None:
        stage = self.stage
        if not stage.search.done:
            return
        if stage.kind == LOUDER:
            found = stage.search.result()
            if found.confident:
                self.stages.append(self._smoothness(stage.side, stage.target, found.hz, wide=False))
            elif found.fell_back:
                if stage.target == UPPER:  # no clear upper peak: leave the upper treble as it is
                    self.upper_results[stage.side] = None
                    self._next_ear()
                    return
                center = self.default_hz.get(stage.side, found.hz)
                self.stages.append(self._smoothness(stage.side, PRIMARY, center, wide=True))
            else:
                self.status = "unsure"
            return
        self._results_for(stage.target)[stage.side] = stage.search.result()
        self._next_ear()

    def _next_ear(self) -> None:
        stage = self.stage
        index = self.sides.index(stage.side)
        if index + 1 < len(self.sides):
            self.stages.append(self._louder(self.sides[index + 1], stage.target))
        elif stage.target == PRIMARY and self.offer_upper:
            self.status = "upper_offer"
        else:
            self.status = "finished"

    def start_upper(self) -> None:
        """Go on to the upper resonance (after both primary resonances)."""
        if self.status != "upper_offer":
            raise RuntimeError("The upper treble comes after the primary resonances.")
        self.status = "testing"
        self.stages.append(self._louder(self.sides[0], UPPER))

    def skip_upper(self) -> None:
        if self.status == "upper_offer":
            self.status = "finished"

    def use_estimate(self) -> None:
        """After an unsure louder test: carry on from its best guess, with wider steps."""
        if self.status != "unsure":
            return
        stage = self.stage
        self.status = "testing"
        self.stages.append(self._smoothness(stage.side, stage.target, stage.search.estimate(), wide=True))

    def undo(self) -> bool:
        """Take back the last answer, across stages and ears; False at the very start."""
        while True:
            stage = self.stage
            self._results_for(stage.target).pop(stage.side, None)
            self.status = "testing"
            if stage.search.undo():
                return True
            if len(self.stages) == 1:
                return False
            self.stages.pop()

    def redo_ear(self) -> None:
        """Start the current ear's louder test again (after an unsure one)."""
        stage = self.stage
        self.stages[-1] = self._louder(stage.side, stage.target)
        self._results_for(stage.target).pop(stage.side, None)
        self.status = "testing"

    def best_guess(self, side: str, target: str = PRIMARY) -> float | None:
        """What's been found for ``side`` so far: its result, the smoothness check's current
        centre, or (with a few answers) the louder test's estimate."""
        found = self._results_for(target)
        if side in found:
            return found[side]
        for stage in reversed(self.stages):
            if stage.side != side or stage.target != target:
                continue
            if stage.kind == SMOOTHNESS:
                return stage.search.result()
            if len(stage.search.answers) >= 3 and stage.search.spread <= KEEP_ESTIMATE_RATIO:
                return stage.search.estimate()
            return None
        return None

    def progress(self) -> float:
        """0…1 within the current part (primary resonances, or the upper treble)."""
        if self.status in ("finished", "upper_offer"):
            return 1.0
        total = 2 * len(self.sides)
        done = 2 * self.sides.index(self.side) + (1 if self.kind == SMOOTHNESS else 0)
        return (done + self.stage.search.progress()) / total
