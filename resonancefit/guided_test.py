"""The guided "Find my resonance" test, without Qt.

Two listening stages per ear find the primary resonance (see GLOSSARY):

- :class:`LouderSearch` (Stage 1, "Louder test"): A/B rounds of equal-level
  tones. A golden-section search in log frequency over ``CONTROL_RANGE``
  narrows the interval holding the loudest frequency, then one earlier pair
  is repeated (swapped) to check the answers are consistent.
- :class:`SmoothnessSearch` (Stage 2, "Smoothness check"): A/B/C rounds of
  pink noise through three EQs whose primary resonance sits just below, at
  and just above the current guess; the smoothest one wins.

:class:`GuidedTest` runs the stages for each ear in order and keeps one undo
history across them. Option order is shuffled every round; options are
plain frequencies, the UI only ever shows letters.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from eq_helper.dsp.resonance import CONTROL_RANGE

GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0  # 0.618…

LOUDER_STOP_RATIO = 1.03     # done when b / a is below this
LOUDER_MAX_ROUNDS = 10
LOUDER_SAME_FALLBACK = 3     # this many opening "About the same" answers: give up on Stage 1
CHECK_MIN_CLARITY = 0.08     # octaves: how much nearer the result the louder tone of the repeated pair must be

SMOOTH_DELTA = 0.06          # octaves between the options
SMOOTH_WIDE_DELTA = 0.12     # after a Stage 1 fallback: start wider
SMOOTH_MIN_DELTA = 0.015
SMOOTH_MAX_ROUNDS = 6
SMOOTH_MAX_MOVES = 2         # edge picks in a row at one spacing before it halves

LOUDER, SMOOTHNESS = "louder", "smoothness"


@dataclass(frozen=True)
class Round:
    """One question: ``options`` in display order (A, B, C) as frequencies in Hz."""

    options: tuple[float, ...]
    check: bool = False  # Stage 1's repeated pair

    def letter(self, index: int) -> str:
        return "ABC"[index]


@dataclass(frozen=True)
class LouderResult:
    hz: float | None          # None: fell back (three opening "About the same")
    confident: bool
    fell_back: bool
    rounds: int


def _clip(hz: float, lo: float, hi: float) -> float:
    return float(min(max(hz, lo), hi))


class LouderSearch:
    """Stage 1: which of two equal-level tones is louder, narrowed by golden section."""

    def __init__(self, hz_range=CONTROL_RANGE, rng: random.Random | None = None,
                 max_rounds: int = LOUDER_MAX_ROUNDS, stop_ratio: float = LOUDER_STOP_RATIO):
        self.lo, self.hi = (float(v) for v in hz_range)
        self.rng = rng or random.Random()
        self.max_rounds = max_rounds
        self.stop_ratio = stop_ratio
        self.a, self.b = math.log(self.lo), math.log(self.hi)
        # each answered round: (a, b, round, answer); answer = louder Hz or None for "About the same"
        self.answers: list[tuple[float, float, Round, float | None]] = []
        self.check_answer: tuple[Round, float | None] | None = None
        self._round: Round | None = None

    # ── state ──────────────────────────────────────────────────────────────
    def _points(self) -> tuple[float, float]:
        span = self.b - self.a
        return math.exp(self.b - GOLDEN * span), math.exp(self.a + GOLDEN * span)

    @property
    def fell_back(self) -> bool:
        first = self.answers[:LOUDER_SAME_FALLBACK]
        return len(first) == LOUDER_SAME_FALLBACK and all(a[3] is None for a in first)

    @property
    def searching(self) -> bool:
        if self.fell_back or len(self.answers) >= self.max_rounds:
            return False
        if all(a[3] is None for a in self.answers):
            return True  # only "About the same" so far: keep asking until it's three of them
        return math.exp(self.b - self.a) >= self.stop_ratio

    def _check_pair(self) -> tuple[float, float, float] | None:
        """(x1, x2, louder) of the pair to repeat: the latest answer that should have been clear.

        A pair is clear when one tone lies much nearer the result than the
        other; repeating a pair that straddles the peak would only test chance.
        """
        clear = [(r.options, louder) for _a, _b, r, louder in self.answers if louder is not None]
        if not clear:
            return None
        center = (self.a + self.b) / 2

        def clarity(options):
            x1, x2 = (abs(math.log(x) - center) for x in options)
            return abs(x1 - x2) / math.log(2)
        clearest = [c for c in clear if clarity(c[0]) >= CHECK_MIN_CLARITY]
        options, louder = clearest[-1] if clearest else max(clear, key=lambda c: clarity(c[0]))
        return min(options), max(options), louder

    @property
    def done(self) -> bool:
        if self.searching:
            return False
        return self.fell_back or self.check_answer is not None or self._check_pair() is None

    def options(self) -> Round:
        """The current round (stable until it's answered or undone)."""
        if self.done:
            raise RuntimeError("The louder test is finished.")
        if self._round is None:
            if self.searching:
                pair = list(self._points())
                check = False
            else:
                x1, x2, earlier = self._check_pair()
                # swapped: the louder one of the earlier round now comes second
                pair = [x2, x1] if earlier == x1 else [x1, x2]
                check = True
            if not check:
                self.rng.shuffle(pair)
            self._round = Round(tuple(pair), check)
        return self._round

    def answer(self, louder: int | None) -> None:
        """``louder``: index of the louder option in the round, ``None`` = "About the same"."""
        current = self.options()
        hz = None if louder is None else current.options[louder]
        if current.check:
            self.check_answer = (current, hz)
        else:
            self.answers.append((self.a, self.b, current, hz))
            x1, x2 = self._points()
            if hz is None:
                self.a, self.b = math.log(x1), math.log(x2)
            elif hz == x1:
                self.b = math.log(x2)
            else:
                self.a = math.log(x1)
        self._round = None

    def undo(self) -> bool:
        """Take back the last answer; False when there's none."""
        if self.check_answer is not None:
            self._round = self.check_answer[0]
            self.check_answer = None
            return True
        if not self.answers:
            return False
        self.a, self.b, self._round, _hz = self.answers.pop()
        return True

    @property
    def consistent(self) -> bool:
        """False when the repeated pair got the opposite answer."""
        if self.check_answer is None or self.check_answer[1] is None:
            return True
        x1, x2, earlier = self._check_pair()
        return self.check_answer[1] == earlier

    def result(self) -> LouderResult:
        if not self.done:
            raise RuntimeError("The louder test isn't finished yet.")
        hz = None if self.fell_back else _clip(math.exp((self.a + self.b) / 2), self.lo, self.hi)
        return LouderResult(hz, self.consistent and not self.fell_back, self.fell_back, len(self.answers))

    def progress(self) -> float:
        """0…1, from how far the interval has narrowed (or the rounds used)."""
        if self.done:
            return 1.0
        total = math.log(math.log(self.hi / self.lo) / math.log(self.stop_ratio))
        narrowed = math.log(max(math.log(self.hi / self.lo) / max(self.b - self.a, 1e-9), 1.0))
        by_width = narrowed / total if total > 0 else 1.0
        return min(0.9, max(by_width, len(self.answers) / self.max_rounds))


class SmoothnessSearch:
    """Stage 2: which of three EQs (resonance a little low / as found / a little high) is smoothest."""

    def __init__(self, center_hz: float, delta: float = SMOOTH_DELTA, hz_range=CONTROL_RANGE,
                 rng: random.Random | None = None, max_rounds: int = SMOOTH_MAX_ROUNDS,
                 min_delta: float = SMOOTH_MIN_DELTA):
        self.lo, self.hi = (float(v) for v in hz_range)
        self.rng = rng or random.Random()
        self.max_rounds = max_rounds
        self.min_delta = min_delta
        self.center = _clip(center_hz, self.lo, self.hi)
        self.delta = float(delta)
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
        low, mid, high = self._triple()
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

    def progress(self) -> float:
        if self.done:
            return 1.0
        halvings = math.log2(SMOOTH_DELTA / self.min_delta) + 1
        by_delta = math.log2(max(SMOOTH_DELTA / self.delta, 1.0)) / halvings
        return min(0.9, max(by_delta, len(self.answers) / self.max_rounds))


@dataclass
class _Stage:
    side: str
    kind: str
    search: LouderSearch | SmoothnessSearch


@dataclass
class GuidedTest:
    """Both stages for each ear in turn, with one undo history across them.

    ``default_hz``: per side, where Stage 2 starts if Stage 1 falls back.
    ``status``: "testing", "unsure" (Stage 1's check failed: ask the customer
    to redo the ear or adjust by hand) or "finished".
    """

    sides: tuple[str, ...]
    default_hz: dict[str, float] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    hz_range: tuple[float, float] = CONTROL_RANGE
    stages: list[_Stage] = field(default_factory=list)
    results: dict[str, float] = field(default_factory=dict)
    status: str = "testing"

    def __post_init__(self):
        self.sides = tuple(self.sides)
        if not self.sides:
            raise ValueError("Nothing to test: no ears.")
        self.stages = [self._louder(self.sides[0])]

    def _louder(self, side: str) -> _Stage:
        return _Stage(side, LOUDER, LouderSearch(self.hz_range, self.rng))

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
    def round_number(self) -> int:
        """1-based number of the current round within this stage."""
        search = self.stage.search
        return len(search.answers) + 1 + (1 if getattr(search, "check_answer", None) else 0)

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
            if not found.confident and not found.fell_back:
                self.status = "unsure"
                return
            if found.fell_back:
                center, delta = self.default_hz.get(stage.side, sum(self.hz_range) / 2), SMOOTH_WIDE_DELTA
            else:
                center, delta = found.hz, SMOOTH_DELTA
            self.stages.append(_Stage(stage.side, SMOOTHNESS,
                                      SmoothnessSearch(center, delta, self.hz_range, self.rng)))
            return
        self.results[stage.side] = stage.search.result()
        index = self.sides.index(stage.side)
        if index + 1 < len(self.sides):
            self.stages.append(self._louder(self.sides[index + 1]))
        else:
            self.status = "finished"

    def undo(self) -> bool:
        """Take back the last answer, across stages and ears; False at the very start."""
        while True:
            stage = self.stage
            if self.status == "finished" or stage.kind == SMOOTHNESS:
                self.results.pop(stage.side, None)
            self.status = "testing"
            if stage.search.undo():
                return True
            if len(self.stages) == 1:
                return False
            self.stages.pop()

    def redo_ear(self) -> None:
        """Start the current ear again from Stage 1 (after an inconsistent check)."""
        side = self.side
        while len(self.stages) > 1 and self.stages[-2].side == side:
            self.stages.pop()
        self.stages[-1] = self._louder(side)
        self.results.pop(side, None)
        self.status = "testing"

    def best_guess(self, side: str) -> float | None:
        """What's been found for ``side`` so far: its result, or Stage 2's current centre."""
        if side in self.results:
            return self.results[side]
        for stage in reversed(self.stages):
            if stage.side == side and stage.kind == SMOOTHNESS:
                return stage.search.result()
        return None

    def progress(self) -> float:
        """Overall 0…1 across every ear and stage (each stage weighs the same)."""
        if self.status == "finished":
            return 1.0
        total = 2 * len(self.sides)
        done = 2 * self.sides.index(self.side) + (1 if self.kind == SMOOTHNESS else 0)
        return (done + self.stage.search.progress()) / total
