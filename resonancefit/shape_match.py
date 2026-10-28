"""Shape match: find the primary resonance from loudness matches, without Qt.

The customer hears two noise bands in turn and moves a slider until they
sound equally loud. Each match measures, in dB, how much louder one band is
heard than the other. A handful of them traces the shape of what this ear
hears across the treble, and the calibration's model says where that shape
appears: its prediction for every resonance frequency is compared with the
matches, and the belief over the resonance follows.

What a match measures is the IEM *plus* the listener: hearing sensitivity
changes with frequency (equal-loudness contours, and each person's own), and
a higher band may sound more prominent just for its pitch. Both are smooth
across the treble, while a resonance is narrow, so the fit carries a smooth
"hearing" curve (a tilt and a bend, in dB) of its own and never mistakes it
for the resonance. It also allows the resonance to be a little higher or
lower than the model predicts.

The bands sit next to each other or on the anchor: the band (near 4.5 kHz)
that changes least between insertion depths in the calibration, so it is the
same for every candidate and every ear. Small pitch gaps keep matches easy
and the equal-loudness differences small; the fit trusts matches across a
wider gap a little less.

Every match after the first is chosen where it is expected to teach most
about the resonance. The search starts from a starting point (the
calibration's measurements, or the other ear's result), but always keeps a
part of its belief spread over the whole range, so a wrong start costs a few
matches, never the answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from eq_helper.dsp.resonance import CONTROL_RANGE

BAND = 1 / 6                 # octaves: each band's width
STEP = 1 / 12                # octaves between the band centres
TOP_HZ = 11000.0             # the highest band centre
ANCHOR_RANGE = (3500.0, 5500.0)  # where the steadiest band is looked for
BAND_POINTS = 9              # how finely a band's level is averaged
GRID_OCTAVES = 1 / 192       # resolution of the belief over the resonance
MAX_GAP = 0.75               # octaves: no match across a wider pitch gap

MATCH_NOISE_DB = 1.0         # how precisely people match two neighbouring bands
GAP_NOISE_DB = 1.0           # … and how much less precisely per octave between them
TILT_SD = 8.0                # dB per octave: the hearing curve's tilt (prior spread)
BEND_SD = 6.0                # dB per octave²: … and its bend
HEIGHT_SD = 0.3              # the resonance may be this much (fraction) higher or lower than modelled
START_SHARE = 0.7            # belief near the starting point; the rest is spread over the range
START_SD = 0.1               # octaves: how near

MIN_MATCHES = 6
MIN_MATCHES_FROM_OTHER_EAR = 4
MAX_MATCHES = 14
STOP_RATIO = 1.03            # done when 80 % of the belief lies within this ratio
CONFIDENT_RATIO = 1.06
CREDIBLE = 0.8
REPEAT_SIGMAS = 3.0          # a match this far off the fit is asked again (once)
AGREE_DB = 3.0               # … and kept only if the second answer is this close to it
MATCH_RANGE_DB = 12.0        # the slider moves a band this far each way


def band_level(freqs, db, hz: float, width: float = BAND) -> float:
    """The level (dB) a noise band ``width`` octaves wide around ``hz`` is heard at: the power average."""
    points = hz * 2.0 ** np.linspace(-width / 2, width / 2, BAND_POINTS)
    values = np.interp(np.log(points), np.log(freqs), db)
    return float(10 * np.log10(np.mean(10 ** (values / 10))))


def stable_anchor(freqs, curves, hz_range=ANCHOR_RANGE) -> float:
    """The band centre (in ``hz_range``) whose level changes least between the measured insertion depths."""
    centres = 2.0 ** np.arange(math.log2(hz_range[0]), math.log2(hz_range[1]) + 1e-9, 1 / 24)
    spread = [np.std([band_level(freqs, c, hz) for c in curves]) for hz in centres]
    return float(centres[int(np.argmin(spread))])


@dataclass(frozen=True)
class Match:
    """One question: make ``adjust`` (Hz) sound as loud as ``reference`` (Hz)."""

    reference: float
    adjust: float
    repeat: bool = False      # asked again because the first answer didn't fit the others


@dataclass(frozen=True)
class ShapeResult:
    hz: float                 # best estimate (the belief's median)
    confident: bool
    matches: int
    spread: float             # ratio covering 80 % of the belief


class ShapeMatch:
    """The belief over one ear's resonance, from loudness matches.

    ``predict(hz)`` returns ``(freqs, db)``: the calibration model's
    response for a resonance at ``hz``. ``anchor``: the steadiest band
    (see :func:`stable_anchor`). ``start``: where the search starts
    (``None``: anywhere in the range); ``from_other_ear`` lowers the
    number of matches asked before stopping.
    """

    def __init__(self, predict: Callable[[float], tuple[np.ndarray, np.ndarray]], anchor: float,
                 start: float | None = None, hz_range=CONTROL_RANGE, from_other_ear: bool = False,
                 max_matches: int = MAX_MATCHES, stop_ratio: float = STOP_RATIO):
        self.lo, self.hi = (float(v) for v in hz_range)
        self.anchor = float(anchor)
        self.max_matches = max_matches
        self.min_matches = MIN_MATCHES_FROM_OTHER_EAR if from_other_ear else MIN_MATCHES
        self.stop_ratio = stop_ratio
        lo2, hi2 = math.log2(self.lo), math.log2(self.hi)
        self.grid = 2.0 ** np.linspace(lo2, hi2, max(2, round((hi2 - lo2) / GRID_OCTAVES) + 1))
        first = math.log2(self.anchor) + STEP
        self.bands = np.r_[self.anchor, 2.0 ** np.arange(first, math.log2(TOP_HZ) + 1e-9, STEP)]
        # the model's level for every band, for every candidate resonance: (grid, bands)
        rows = []
        for hz in self.grid:
            freqs, db = predict(float(hz))
            rows.append([band_level(freqs, db, b) for b in self.bands])
        self.levels = np.array(rows)
        x = np.log2(self.bands / 7000.0)
        self._smooth = np.stack([x, x * x], axis=1)                       # (bands, 2): tilt, bend
        self._prior_cov = np.diag([TILT_SD ** 2, BEND_SD ** 2, HEIGHT_SD ** 2])
        g = np.log2(self.grid)
        flat = np.full(len(g), (1 - START_SHARE) / len(g))
        if start is None:
            prior = np.full(len(g), 1.0 / len(g))
        else:
            near = np.exp(-0.5 * ((g - math.log2(start)) / START_SD) ** 2)
            prior = flat + START_SHARE * near / near.sum()
        self._log_prior = np.log(prior / prior.sum())
        i, j = np.triu_indices(len(self.bands), k=1)
        gap = np.log2(self.bands[j] / self.bands[i])
        keep = gap <= MAX_GAP + 1e-9
        self._pairs = np.stack([i[keep], j[keep]], axis=1)
        self.answers: list[tuple[Match, int, int, float]] = []  # (match, reference index, adjust index, dB)
        self._question: Match | None = None
        self._repeated: set[tuple[int, int]] = set()
        self._dropped: set[int] = set()  # answers a repeat contradicted: left out of the fit
        self._fit = None

    # ── the fit ──────────────────────────────────────────────────────────
    def _noise(self, i, j):
        return MATCH_NOISE_DB + GAP_NOISE_DB * np.abs(np.log2(self.bands[j] / self.bands[i]))

    def _design(self, i, j):
        """For pairs (i → j): the model's difference per candidate (grid, K) and the nuisance rows (grid, K, 3)."""
        diff = self.levels[:, j] - self.levels[:, i]
        smooth = np.broadcast_to(self._smooth[j] - self._smooth[i], (len(self.grid), len(i), 2))
        return diff, np.concatenate([smooth, diff[..., None]], axis=-1)

    def _fitted(self):
        """Per candidate: log posterior, and the nuisance's posterior mean and covariance."""
        if self._fit is None:
            n = len(self.grid)
            if not self.answers:
                self._fit = (self._log_prior.copy(), np.zeros((n, 3)), np.broadcast_to(self._prior_cov, (n, 3, 3)))
            else:
                used = [a for k, a in enumerate(self.answers) if k not in self._dropped]
                i = np.array([a[1] for a in used])
                j = np.array([a[2] for a in used])
                d = np.array([a[3] for a in used])
                diff, rows = self._design(i, j)
                y = d[None, :] - diff                                          # (n, K)
                noise = self._noise(i, j) ** 2
                cov = rows @ self._prior_cov @ rows.transpose(0, 2, 1) + np.diag(noise)[None]
                sign, logdet = np.linalg.slogdet(cov)
                solved = np.linalg.solve(cov, y[..., None])[..., 0]
                log_post = self._log_prior - 0.5 * (np.sum(y * solved, axis=1) + logdet)
                precision = np.linalg.inv(self._prior_cov)[None] + rows.transpose(0, 2, 1) @ (rows / noise[None, :, None])
                post_cov = np.linalg.inv(precision)
                mean = (post_cov @ (rows.transpose(0, 2, 1) @ (y / noise)[..., None]))[..., 0]
                self._fit = (log_post, mean, post_cov)
        return self._fit

    def posterior(self) -> np.ndarray:
        log_post = self._fitted()[0]
        p = np.exp(log_post - log_post.max())
        return p / p.sum()

    def _quantiles(self, *qs: float) -> list[float]:
        cdf = np.cumsum(self.posterior())
        g = np.log2(self.grid)
        return [float(2.0 ** np.interp(q, cdf, g)) for q in qs]

    @property
    def spread(self) -> float:
        lo, hi = self._quantiles((1 - CREDIBLE) / 2, (1 + CREDIBLE) / 2)
        return hi / lo

    def estimate(self) -> float:
        return self._quantiles(0.5)[0]

    def _predicted(self, i, j):
        """Mean and variance of a match's answer (dB, adjust − reference) for each candidate."""
        _log_post, mean, cov = self._fitted()
        diff, rows = self._design(i, j)
        m = diff + np.einsum("nkc,nc->nk", rows, mean)
        v = np.einsum("nkc,ncd,nkd->nk", rows, cov, rows) + self._noise(i, j)[None] ** 2
        return m, v

    # ── questions ────────────────────────────────────────────────────────
    @property
    def done(self) -> bool:
        if self._question is not None and self._question.repeat:
            return False
        n = len(self.answers)
        return n >= self.max_matches or (n >= self.min_matches and self.spread <= self.stop_ratio)

    def _surprising(self) -> tuple[int, int] | None:
        """The last match, when it lies far off what the others say (and wasn't asked again yet)."""
        if len(self.answers) < 3:
            return None
        _match, i, j, d = self.answers[-1]
        if (i, j) in self._repeated:
            return None
        saved = self.answers.pop()
        self._fit = None
        m, v = self._predicted(np.array([i]), np.array([j]))
        self.answers.append(saved)
        self._fit = None
        w = self.posterior()
        z = abs(d - float(np.sum(w * m[:, 0]))) / math.sqrt(float(np.sum(w * v[:, 0])))
        return (i, j) if z > REPEAT_SIGMAS else None

    def _best_pair(self) -> tuple[int, int]:
        i, j = self._pairs[:, 0], self._pairs[:, 1]
        m, v = self._predicted(i, j)
        w = self.posterior()[:, None]
        mean = np.sum(w * m, axis=0)
        total = np.sum(w * (v + m * m), axis=0) - mean ** 2
        within = np.sum(w * v, axis=0)
        gain = 0.5 * np.log(total / within)
        asked = {(a[1], a[2]) for a in self.answers}
        gain = gain - np.array([0.02 * sum(1 for k in asked if k == (a, b)) for a, b in zip(i, j)])
        k = int(np.argmax(gain))
        return int(i[k]), int(j[k])

    def question(self) -> Match:
        if self.done:
            raise RuntimeError("The shape match is finished.")
        if self._question is None:
            again = self._surprising()
            if again is not None:
                self._repeated.add(again)
                i, j = again
                self._question = Match(float(self.bands[i]), float(self.bands[j]), repeat=True)
            else:
                i, j = self._best_pair()
                self._question = Match(float(self.bands[i]), float(self.bands[j]))
        return self._question

    def answer(self, louder_db: float) -> None:
        """``louder_db``: how much louder (dB) the adjusted band was heard than the reference — that is,
        minus the gain the slider set to make them match."""
        q = self.question()
        i = int(np.flatnonzero(np.isclose(self.bands, q.reference))[0])
        j = int(np.flatnonzero(np.isclose(self.bands, q.adjust))[0])
        d = float(np.clip(louder_db, -MATCH_RANGE_DB, MATCH_RANGE_DB))
        if q.repeat:  # asked again: the first answer counts only if this one agrees with it
            first = max(k for k, a in enumerate(self.answers) if (a[1], a[2]) == (i, j))
            if abs(self.answers[first][3] - d) > AGREE_DB:
                self._dropped.add(first)
        self.answers.append((q, i, j, d))
        self._question = None
        self._fit = None

    def undo(self) -> bool:
        if not self.answers:
            return False
        q, i, j, _d = self.answers.pop()
        if q.repeat:
            self._repeated.discard((i, j))
            self._dropped.discard(max(k for k, a in enumerate(self.answers) if (a[1], a[2]) == (i, j)))
        self._question = q
        self._fit = None
        return True

    def result(self) -> ShapeResult:
        spread = self.spread
        return ShapeResult(self.estimate(), spread <= CONFIDENT_RATIO, len(self.answers), spread)

    def heard_curve(self) -> tuple[np.ndarray, np.ndarray]:
        """(band centres, dB): the listener's fitted hearing curve (tilt and bend) at the best estimate."""
        k = int(np.argmax(self._fitted()[0]))
        mean = self._fitted()[1][k]
        return self.bands.copy(), self._smooth @ mean[:2]

    def progress(self) -> float:
        if self.done:
            return 1.0
        full = math.log(self.hi / self.lo) / math.log(self.stop_ratio)
        narrowed = math.log(max(math.log(self.hi / self.lo) / math.log(max(self.spread, 1.0001)), 1.0))
        by_spread = narrowed / math.log(full) if full > 1 else 1.0
        return min(0.9, max(by_spread, len(self.answers) / self.max_matches))
