"""The guided "Find my resonance" test, without Qt.

Two listening stages per ear find a resonance (see GLOSSARY):

- :class:`LouderSearch` ("Louder test"): A/B rounds of two equal-level
  noise bands. It keeps a belief over where the peak is (and how wide),
  updated after every answer with a simple listener model, and asks the
  pair — place *and* band width — that should teach it most: wide bands
  while it knows little, narrow ones as it closes in. Bands rather than pure
  tones because a sealed ear canal adds fine dips and bumps that a single
  tone can land on; a band averages over them. "About the same" is
  information too: the peak lies midway between the two bands, *or far from
  both* — two sounds on the flat part beside a narrow peak sound alike, so
  that region is never thrown away.
- :class:`SmoothnessSearch` ("Smoothness check"): A/B/C rounds of pink
  noise through three EQs whose resonance sits just below, at and just
  above the current guess; the smoothest one wins.

With a starting point the louder test is skipped: the smoothness check
alone finds the resonance, the first ear starting wide around the
calibration's measurements, the second at the usual spacing around the
first ear's result, both going on down to options about 50 Hz apart.
Judging smoothness is easier than judging loudness, and in simulation
(:mod:`resonancefit.simulation`) it's as accurate in fewer rounds. The
louder test is the fallback when there's no starting point.

:class:`GuidedTest` runs the stages for each ear, first for the primary
resonance and then, if the customer wants, for the upper resonance. It keeps
one undo history across stages and ears. Option order is shuffled every
round; options are plain frequencies, the UI only ever shows letters.

The upper resonance (about 15–18 kHz) gets no louder test: up there hearing
falls off steeply and differs a lot between people, so "quieter" says more
about the ear than about the IEM. It follows the primary resonance closely
(about twice its frequency), so the calibration's prediction is the start,
and only the smoothness check — a local comparison a smooth fall-off
doesn't disturb — fine-tunes it, within the range the EQ can move it.
First, an :class:`AudibilityCheck` asks whether a band there is heard
clearly at all; if not, that ear's upper treble stays as calibrated.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr

from eq_helper.dsp.resonance import CONTROL_RANGE, PERSONAL_TREBLE_RANGE

# ── the listener model behind the louder test ───────────────────────────────
PEAK_HEIGHTS = (4.0, 8.0)    # dB: how much louder a sound right on the peak may be than the flat part
NO_PEAK_PRIOR = 0.2          # belief, before any answer, that no peak will be heard at all
GUESS_PRIOR = 0.1            # … that the answers will be guesses (unrelated to what's played)
GUESS_ANSWERS = (0.4, 0.4, 0.2)  # how a guesser answers: first / second louder / about the same
GUESS_DOUBT = 0.3            # this likely that the answers are guesses: not done / unsure
CHECK_EVERY = 3              # every third round is a check: the pair whose answer is most predictable
PEAK_WIDTHS = (0.06, 0.1, 0.18)        # octaves (Gaussian σ) a primary resonance may have
JND_DB = 1.0                 # smaller differences are heard as "About the same"
NOISE_DB = 0.8               # how much the impression wobbles from round to round
LAPSE = 0.05                 # a slip: any answer, whatever was heard
RIPPLE_DB = 1.5              # fine dips and bumps in the ear (unknown): extra noise for narrow sounds
RIPPLE_OCTAVES = 0.04        # their typical width; a band this wide or wider averages them out
BANDS = (1 / 3, 1 / 6, 1 / 12)  # octaves; the search picks the width each round (0 would be a tone)
BAND_POINTS = 9              # how finely the model averages over a band
GRID_OCTAVES = 1 / 192       # resolution of the belief over the peak's place
CANDIDATE_OCTAVES = 1 / 48   # spacing of the band centres the test can play
PAIR_OCTAVES = (0.04, 0.4)   # how far apart the two centres of a round may be (at least half a band)

LOUDER_MAX_ROUNDS = 12
LOUDER_MIN_ROUNDS = 6
SURPRISE = 0.1               # an answer the final belief gave less than this chance is a surprise
LOUDER_STOP_RATIO = 1.04     # done when 80 % of the belief lies within this ratio
LOUDER_CONFIDENT_RATIO = 1.08
CREDIBLE = 0.8
NO_PEAK_SAME_SHARE = 0.6     # this share of "About the same": no clear peak heard
KEEP_ESTIMATE_RATIO = 1.15   # leaving mid-test keeps the louder test's estimate once it's this narrow
NO_PEAK_DONE = 0.85          # this sure that nothing is heard (after a few rounds): stop
NO_PEAK_DOUBT = 0.3          # this likely that nothing is heard: not done / not confident yet
LOUDER_MIN_NO_PEAK_ROUNDS = 4

SMOOTH_DELTA = 0.06          # octaves between the options
SMOOTH_WIDE_DELTA = 0.12     # when the louder test wasn't sure: start wider
UPPER_SMOOTH_DELTA = 0.1     # the upper smoothness check starts from the calibration's prediction
SMOOTH_MIN_DELTA = 0.015
FIRST_SMOOTH_DELTA = 0.3     # no louder test: the first ear starts this wide around the calibration's guess
FINE_MIN_DELTA = 0.007       # … and goes on to options about 50 Hz apart (≈ 0.7 % at 7 kHz)
FIRST_SMOOTH_MAX_ROUNDS = 10
UPPER_SMOOTH_MIN_DELTA = 0.025
SMOOTH_MAX_ROUNDS = 6
SMOOTH_MAX_MOVES = 2         # edge picks in a row at one spacing before it halves
UPPER_WINDOW = 1.25          # the upper search stays within ÷/× this of the prediction (the EQ moves it ≤ 1.4×)
AUDIBLE_BAND = 1 / 3         # octaves: the band played to ask whether the upper treble is heard clearly

LOUDER, SMOOTHNESS, AUDIBLE = "louder", "smoothness", "audible"
PRIMARY, UPPER = "primary", "upper"


@dataclass(frozen=True)
class Round:
    """One question: ``options`` in display order (A, B, C) as frequencies in Hz.

    ``band``: for the louder test, the width (octaves) of the noise band
    played around each option; 0 = a tone.
    """

    options: tuple[float, ...]
    band: float = 0.0

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


def band_noise_db(band: float) -> float:
    """How much a round's impression wobbles for sounds ``band`` octaves wide (0 = a tone): the
    listener's own wobble plus the ear's fine ripple on both sounds, which a wider band averages out."""
    ripple = 2 * RIPPLE_DB ** 2 * min(1.0, RIPPLE_OCTAVES / max(band, RIPPLE_OCTAVES))
    return math.sqrt(NOISE_DB ** 2 + ripple)


def outcome_probabilities(difference_db, noise_db: float = NOISE_DB):
    """P(first louder), P(second louder), P(about the same) for a level difference (first − second)."""
    d = np.asarray(difference_db, dtype=float)
    first = ndtr((d - JND_DB) / noise_db)
    second = ndtr((-d - JND_DB) / noise_db)
    same = np.clip(1.0 - first - second, 0.0, 1.0)
    return (1.0 - LAPSE) * np.stack([first, second, same]) + LAPSE / 3.0


class LouderSearch:
    """Which of two equal-level noise bands is louder: a Bayesian search for the peak in log frequency.

    The belief covers every peak place × width × height, plus two more
    possibilities: "no audible peak", so a listener who hears no difference
    at all ends up there instead of being pulled to wherever the pairs
    happened to balance; and "guessing", answers unrelated to the sounds,
    which is how answers that contradict each other show up.
    """

    def __init__(self, hz_range=CONTROL_RANGE, rng: random.Random | None = None,
                 max_rounds: int = LOUDER_MAX_ROUNDS, stop_ratio: float = LOUDER_STOP_RATIO,
                 widths=PEAK_WIDTHS, bands=BANDS):
        self.lo, self.hi = (float(v) for v in hz_range)
        self.rng = rng or random.Random()
        self.max_rounds = max_rounds
        self.stop_ratio = stop_ratio
        lo2, hi2 = math.log2(self.lo), math.log2(self.hi)
        self.grid = np.linspace(lo2, hi2, max(2, round((hi2 - lo2) / GRID_OCTAVES) + 1))
        cand = np.linspace(lo2, hi2, max(2, round((hi2 - lo2) / CANDIDATE_OCTAVES) + 1))
        self.candidates = tuple(float(2.0 ** c) for c in cand)
        self._index = {hz: i for i, hz in enumerate(self.candidates)}
        self.bands = tuple(float(b) for b in bands)
        self._band_index = {b: k for k, b in enumerate(self.bands)}
        n_f = len(self.grid)
        shapes = [(h, w) for h in PEAK_HEIGHTS for w in widths]
        # states: (height, width, place) …, then "no audible peak", then "guessing"
        self._n_peak = len(shapes) * n_f
        prior = np.full(self._n_peak + 2, (1.0 - NO_PEAK_PRIOR - GUESS_PRIOR) / self._n_peak)
        prior[-2], prior[-1] = NO_PEAK_PRIOR, GUESS_PRIOR
        self._log_prior = np.log(prior)
        # perceived level (dB above the flat part) of each candidate sound in every state: the power
        # average of the peak over the band (one point for a tone); 0 everywhere without a peak
        self._levels, self._noise, self._pairs = [], [], []
        i, j = np.triu_indices(len(cand), k=1)
        apart = cand[j] - cand[i]
        for band in self.bands:
            offsets = np.linspace(-band / 2, band / 2, BAND_POINTS) if band > 0 else np.zeros(1)
            points = cand[:, None] + offsets[None, :]                                    # (C, P)
            rows = []
            for height, width in shapes:
                peak = height * np.exp(-0.5 * ((points[None] - self.grid[:, None, None]) / width) ** 2)
                rows.append(10 * np.log10(np.mean(10 ** (peak / 10), axis=-1)))          # (F, C)
            rows.append(np.zeros((1, len(cand))))
            self._levels.append(np.concatenate(rows))                                    # (S, C)
            self._noise.append(band_noise_db(band))
            keep = (apart >= max(PAIR_OCTAVES[0], band / 2) - 1e-9) & (apart <= PAIR_OCTAVES[1] + 1e-9)
            self._pairs.append((i[keep], j[keep]))
        self.answers: list[tuple[Round, float | None]] = []  # (round, louder Hz or None = "About the same")
        self._round: Round | None = None
        self._posterior = None

    # ── belief ─────────────────────────────────────────────────────────────
    def _outcomes(self, k: int, a, b) -> np.ndarray:
        """(3, states, …) P(first louder / second louder / same) for band ``k`` and option indices."""
        heard = outcome_probabilities(self._levels[k][:, a] - self._levels[k][:, b], self._noise[k])
        guess = np.broadcast_to(np.asarray(GUESS_ANSWERS).reshape((3, 1) + (1,) * (heard.ndim - 2)),
                                (3, 1) + heard.shape[2:])
        return np.concatenate([heard, guess], axis=1)

    def _answer_probabilities(self, round_: Round, louder: float | None) -> np.ndarray:
        """P(this answer) in every state."""
        k = self._band_index[round_.band]
        a, b = (self._index[hz] for hz in round_.options)
        return self._outcomes(k, a, b)[2 if louder is None else (0 if louder == round_.options[0] else 1)]

    def posterior(self) -> np.ndarray:
        """P(state) after the answers so far (cached); the last two are "no audible peak" and "guessing"."""
        if self._posterior is None:
            log_p = self._log_prior.copy()
            for round_, louder in self.answers:
                log_p += np.log(self._answer_probabilities(round_, louder))
            p = np.exp(log_p - log_p.max())
            self._posterior = p / p.sum()
        return self._posterior

    @property
    def no_peak(self) -> float:
        """How likely it is that no peak is heard at all."""
        return float(self.posterior()[-2])

    @property
    def guessing(self) -> float:
        """How likely it is that the answers don't follow the sounds (they contradict each other)."""
        return float(self.posterior()[-1])

    def _place(self, p: np.ndarray) -> np.ndarray:
        """P(place), given that there is a peak."""
        marginal = p[:self._n_peak].reshape(-1, len(self.grid)).sum(axis=0)
        return marginal / max(marginal.sum(), 1e-300)

    def _quantiles(self, *qs: float) -> list[float]:
        cdf = np.cumsum(self._place(self.posterior()))
        return [float(np.interp(q, cdf, self.grid)) for q in qs]

    @property
    def spread(self) -> float:
        """Frequency ratio holding the middle 80 % of the belief (given a peak)."""
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
        return sum(float((self._answer_probabilities(r, louder) * p).sum()) < SURPRISE for r, louder in self.answers)

    @property
    def done(self) -> bool:
        n = len(self.answers)
        if n >= self.max_rounds:
            return True
        if n >= LOUDER_MIN_NO_PEAK_ROUNDS and self.no_peak >= NO_PEAK_DONE:
            return True  # nothing sounds louder than anything: no point going on
        # a surprising answer, or answers that look like guesses, keep the test going: more rounds
        # settle which answers to trust
        return (n >= LOUDER_MIN_ROUNDS and self.spread < self.stop_ratio and self.no_peak < NO_PEAK_DOUBT
                and self.guessing < GUESS_DOUBT and self.surprises() == 0)

    # ── rounds ─────────────────────────────────────────────────────────────
    @property
    def checking(self) -> bool:
        """This round is a check (see :meth:`_best_pair`)."""
        return len(self.answers) % CHECK_EVERY == CHECK_EVERY - 1

    def _best_pair(self) -> tuple[float, int, int]:
        """(band, i, j): the pair whose answer is expected to tell most about where the peak is
        (and whether there is one).

        Every third round is a check instead: the pair that best tells listening from guessing —
        one whose answer the belief so far can predict. The most informative pairs are the
        uncertain ones, where any answer looks plausible; only predictable pairs catch guesses.
        """
        p = self.posterior()
        n_f = len(self.grid)
        checking = self.checking
        choices = []
        for k, band in enumerate(self.bands):
            pi, pj = self._pairs[k]
            probs = self._outcomes(k, pi, pj)                                                 # (3, S, P)
            weighted = probs * p[None, :, None]
            if checking:  # two "places": listening (peak or no peak) or guessing
                joint = np.stack([weighted[:, :-1].sum(axis=1), weighted[:, -1]], axis=1)     # (3, 2, P)
            else:  # where the peak is, with "no peak" and "guessing" as two more places
                joint = np.concatenate([weighted[:, :self._n_peak].reshape(3, -1, n_f, len(pi)).sum(axis=1),
                                        weighted[:, self._n_peak:]], axis=1)                # (3, F+2, P)
            p_outcome = joint.sum(axis=1)                                                    # (3, P)
            cond = joint / np.maximum(p_outcome[:, None, :], 1e-300)
            entropy = -(cond * np.log(np.maximum(cond, 1e-300))).sum(axis=1)               # (3, P)
            expected = (p_outcome * entropy).sum(axis=0)
            choices += [(float(e), band, int(a), int(b)) for e, a, b in zip(expected, pi, pj)]
        best = min(c[0] for c in choices)
        near = [c for c in choices if c[0] <= best + 1e-3]
        _e, band, i, j = near[self.rng.randrange(len(near))]
        return band, i, j

    def options(self) -> Round:
        """The current round (stable until it's answered or undone)."""
        if self.done:
            raise RuntimeError("The louder test is finished.")
        if self._round is None:
            band, i, j = self._best_pair()
            pair = [self.candidates[i], self.candidates[j]]
            self.rng.shuffle(pair)
            self._round = Round(tuple(pair), band)
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
        heard_nothing = self.no_peak >= NO_PEAK_DOUBT and self.no_peak >= self.guessing
        confident = (not heard_nothing and spread <= LOUDER_CONFIDENT_RATIO and self.guessing < GUESS_DOUBT
                     and self.surprises() <= 1)
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


class AudibilityCheck:
    """One question before an ear's upper treble: is a band at the predicted peak heard clearly?

    Asked, never inferred from the answers: a band there sounding quiet is
    the ear's own fall-off as much as the IEM. ``answer(0)``: heard clearly;
    ``answer(None)``: barely or not at all.
    """

    def __init__(self, hz: float):
        self.hz = float(hz)
        self.answers: list[bool] = []

    @property
    def done(self) -> bool:
        return bool(self.answers)

    def options(self) -> Round:
        if self.done:
            raise RuntimeError("Already answered.")
        return Round((self.hz,), band=AUDIBLE_BAND)

    def answer(self, choice: int | None) -> None:
        self.options()
        self.answers.append(choice is not None)

    def undo(self) -> bool:
        if not self.answers:
            return False
        self.answers.pop()
        return True

    def result(self) -> bool:
        return self.answers[-1]

    def progress(self) -> float:
        return 1.0 if self.done else 0.0


@dataclass
class _Stage:
    side: str
    kind: str        # LOUDER / SMOOTHNESS / AUDIBLE
    target: str      # PRIMARY / UPPER
    search: LouderSearch | SmoothnessSearch | AudibilityCheck
    recheck: bool = False  # a re-check the balance check asked for: done → back to "finished"


@dataclass
class GuidedTest:
    """Both stages for each ear in turn — the primary resonance, then optionally the upper one.

    ``default_hz``: per side, where the primary search starts from (the
    calibration's measurements): with one, that ear gets the smoothness
    check alone, starting wide (the second ear starts from the first one's
    result instead); without, the louder test comes first. ``status``:

    - "testing": a round is waiting for an answer;
    - "unsure": a louder test ended with answers pointing to more than one
      place — redo the ear, use the estimate anyway, or adjust by hand;
    - "upper_offer": both primary resonances are found; the upper treble is
      offered (``start_upper(predicted)`` / ``skip_upper()``);
    - "finished" (and again after a ``recheck()`` the balance check asked for).

    For the upper treble each ear gets an AUDIBLE stage (one question), then
    a smoothness check around the prediction. ``upper_results[side]`` is
    ``None`` when that ear didn't hear it clearly or nothing was predicted
    (the upper treble then stays as calibrated).
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
    predicted_upper: dict[str, float | None] = field(default_factory=dict)
    status: str = "testing"

    def __post_init__(self):
        self.sides = tuple(self.sides)
        if not self.sides:
            raise ValueError("Nothing to test: no ears.")
        self.stages = [self._first(self.sides[0])]

    def _first(self, side: str) -> _Stage:
        """An ear's first stage: the smoothness check from the other ear's result (usual spacing) or
        the calibration's guess (wide), down to options about 50 Hz apart; the louder test without
        either."""
        other = next((self.results[s] for s in self.sides if s != side and s in self.results), None)
        start = other if other is not None else self.default_hz.get(side)
        if start is None:
            return _Stage(side, LOUDER, PRIMARY, LouderSearch(self.hz_range, self.rng))
        delta = SMOOTH_DELTA if other is not None else FIRST_SMOOTH_DELTA
        search = SmoothnessSearch(start, delta, self.hz_range, self.rng, max_rounds=FIRST_SMOOTH_MAX_ROUNDS,
                                  min_delta=FINE_MIN_DELTA)
        return _Stage(side, SMOOTHNESS, PRIMARY, search)

    def upper_window(self, side: str) -> tuple[float, float]:
        """Where ``side``'s upper resonance is looked for: around the prediction, inside the range."""
        lo, hi = self.upper_range
        predicted = self.predicted_upper.get(side)
        if predicted is None:
            return float(lo), float(hi)
        return max(float(lo), predicted / UPPER_WINDOW), min(float(hi), predicted * UPPER_WINDOW)

    def _smoothness(self, side: str, target: str, center: float, wide: bool) -> _Stage:
        if target == UPPER:
            search = SmoothnessSearch(center, UPPER_SMOOTH_DELTA, self.upper_window(side), self.rng,
                                      min_delta=UPPER_SMOOTH_MIN_DELTA)
            return _Stage(side, SMOOTHNESS, UPPER, search)
        delta = SMOOTH_WIDE_DELTA if wide else SMOOTH_DELTA
        return _Stage(side, SMOOTHNESS, PRIMARY, SmoothnessSearch(center, delta, self.hz_range, self.rng))

    def _upper_start(self, index: int) -> None:
        """The upper treble from ``sides[index]`` on: its audibility question, or (nothing
        predicted) straight on to the next ear."""
        for side in self.sides[index:]:
            predicted = self.predicted_upper.get(side)
            if predicted is not None:
                self.status = "testing"
                self.stages.append(_Stage(side, AUDIBLE, UPPER, AudibilityCheck(predicted)))
                return
            self.upper_results[side] = None
        self.status = "finished"

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
        """``choice``: the option's index; ``None`` = "About the same" / "Can't tell them apart"."""
        if self.status != "testing":
            raise RuntimeError(f"Can't answer while {self.status}.")
        self.stage.search.answer(choice)
        self._advance()

    def _advance(self) -> None:
        stage = self.stage
        if not stage.search.done:
            return
        if stage.kind == AUDIBLE:
            if stage.search.result():
                self.stages.append(self._smoothness(stage.side, UPPER, stage.search.hz, wide=False))
            else:  # not heard clearly: the upper treble stays as calibrated
                self.upper_results[stage.side] = None
                self._next_ear()
            return
        if stage.kind == LOUDER:
            found = stage.search.result()
            if found.confident:
                self.stages.append(self._smoothness(stage.side, stage.target, found.hz, wide=False))
            elif found.fell_back:
                center = self.default_hz.get(stage.side, found.hz)
                self.stages.append(self._smoothness(stage.side, PRIMARY, center, wide=True))
            else:
                self.status = "unsure"
            return
        self._results_for(stage.target)[stage.side] = stage.search.result()
        if stage.recheck:
            self.status = "finished"
            return
        self._next_ear()

    def _next_ear(self) -> None:
        stage = self.stage
        index = self.sides.index(stage.side)
        if stage.target == UPPER:
            self._upper_start(index + 1)
        elif index + 1 < len(self.sides):
            self.stages.append(self._first(self.sides[index + 1]))
        elif self.offer_upper:
            self.status = "upper_offer"
        else:
            self.status = "finished"

    def start_upper(self, predicted: dict[str, float | None]) -> None:
        """Go on to the upper resonance (after both primary resonances). ``predicted[side]``: where
        the calibration puts that ear's upper peak for its found primary resonance (``None``: none)."""
        if self.status != "upper_offer":
            raise RuntimeError("The upper treble comes after the primary resonances.")
        lo, hi = self.upper_range
        self.predicted_upper = {side: (None if predicted.get(side) is None
                                       else _clip(predicted[side], lo, hi)) for side in self.sides}
        self._upper_start(0)

    def skip_upper(self) -> None:
        if self.status == "upper_offer":
            self.status = "finished"

    def recheck(self, side: str, target: str, center_hz: float) -> None:
        """After the balance check: a short smoothness check of one ear's resonance, from ``center_hz``."""
        if self.status != "finished":
            raise RuntimeError("A re-check comes after the test.")
        stage = self._smoothness(side, target, center_hz, wide=False)
        stage.recheck = True
        self.stages.append(stage)
        self._results_for(target).pop(side, None)  # found again when the re-check ends
        self.status = "testing"

    def uncertainty(self) -> dict[tuple[str, str], float]:
        """(side, target) → how unsure the louder test still was (octaves, ≈ σ)."""
        out = {}
        for stage in self.stages:
            if stage.kind == LOUDER and stage.search.answers:
                out[(stage.side, stage.target)] = max(math.log2(stage.search.spread) / 2.56, 0.01)
        return out

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
        self.stages[-1] = self._first(stage.side)
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
            if stage.kind == AUDIBLE:
                return None
            if len(stage.search.answers) >= 3 and stage.search.spread <= KEEP_ESTIMATE_RATIO:
                return stage.search.estimate()
            return None
        return None

    def progress(self) -> float:
        """0…1 within the current part (primary resonances, or the upper treble)."""
        if self.status in ("finished", "upper_offer"):
            return 1.0
        total = 2 * len(self.sides)
        done = 2 * self.sides.index(self.side)
        if any(s.side == self.side and s.target == self.target for s in self.stages[:-1]):
            return (done + 1 + self.stage.search.progress()) / total  # the second of two stages
        # the louder test or audibility question, or an ear's only stage (the smoothness check alone)
        share = 2 if self.kind == SMOOTHNESS else 1
        return (done + share * self.stage.search.progress()) / total
