"""The balance check at the end of the guided test, without Qt.

With both resonances found right, the same sound in both ears should sound
centred at every frequency. The customer centres a series of narrow noise
bands (the lean they correct is measured in dB, positive = left louder).
The check never changes the EQ by itself:

- a lean shaped like a misplaced resonance (a narrow swing around it) names
  the ear and the direction, and the app offers a short re-check of that ear;
- a broad lean across the treble (fit or hearing) is only reported;
- a bass lean means one IEM isn't sealed as well: reseat it.

Each lean is measured relative to a reference band at 1.5 kHz, where the IEM
is steady and the EQ does little, so the customer's overall lean and any
channel difference don't count.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from eq_helper.dsp.resonance import CONTROL_RANGE, PERSONAL_TREBLE_RANGE, adjust_personal_treble, match_target

BAND_OCTAVES = 1 / 12        # width of the noise bands played around the primary resonances
WIDE_BAND_OCTAVES = 1 / 3    # the reference and bass bands (a narrower one at 100 Hz is a rumble)
UPPER_BAND_OCTAVES = 1 / 6
TREBLE_STEP = 1 / 12         # octaves between the bands around the primary resonances
TREBLE_MARGIN = 0.45         # octaves beyond the lowest / highest primary resonance
UPPER_STEP = 1 / 6
UPPER_MARGIN = 0.3
REFERENCE_HZ = 1500.0
BASS_HZ = (100.0, 250.0)
REPEATS = 2                  # bands asked twice, to see how steady the answers are
CENTRE_RANGE_DB = 8.0        # the centring control moves the sound up to this far each way

CENTRE_NOISE_DB = 0.7        # how precisely people centre a sound
UNSTEADY_DB = 2.0            # repeats further apart than this: answers less steady
UNSTEADY_NOISE_DB = 1.5
SHIFT_STEPS = tuple(k / 96 for k in range(-12, 13) if k)   # octaves: hypotheses up to ±9 %
MIN_SHIFT = 0.02             # octaves: smaller suggested moves aren't worth a re-check
EVIDENCE = 9.0               # a re-check needs the lean explained this much better (≈ 3 σ)
BALANCED_DB = 2.0            # residual leans within this count as balanced
BROAD_DB = 1.5               # a broad lean bigger than this is reported
BASS_DB = 2.0                # a bass lean bigger than this suggests reseating

LEFT, RIGHT = "L", "R"
PRIMARY, UPPER = "primary", "upper"


@dataclass(frozen=True)
class Band:
    hz: float
    kind: str                # "reference", "bass", "treble", "upper"
    repeat_of: int | None = None
    width: float = BAND_OCTAVES  # octaves


def plan_bands(primary: dict[str, float], upper: dict[str, float] | None = None,
               rng: random.Random | None = None) -> list[Band]:
    """The bands to centre, in playing order: reference, bass, treble upwards, upper treble, repeats."""
    rng = rng or random.Random()
    bands = [Band(REFERENCE_HZ, "reference", width=WIDE_BAND_OCTAVES)]
    bands += [Band(hz, "bass", width=WIDE_BAND_OCTAVES) for hz in BASS_HZ]
    lo, hi = min(primary.values()), max(primary.values())
    start = math.log2(max(lo / 2 ** TREBLE_MARGIN, 5000.0))
    stop = math.log2(min(hi * 2 ** TREBLE_MARGIN, 11500.0))
    n = int(round((stop - start) / TREBLE_STEP)) + 1
    bands += [Band(float(2 ** x), "treble") for x in np.linspace(start, stop, max(n, 2))]
    if upper:
        lo, hi = min(upper.values()), max(upper.values())
        start = math.log2(max(lo / 2 ** UPPER_MARGIN, PERSONAL_TREBLE_RANGE[0] * 0.95))
        stop = math.log2(min(hi * 2 ** UPPER_MARGIN, 18500.0))
        n = int(round((stop - start) / UPPER_STEP)) + 1
        bands += [Band(float(2 ** x), "upper", width=UPPER_BAND_OCTAVES) for x in np.linspace(start, stop, max(n, 2))]
    treble = [i for i, b in enumerate(bands) if b.kind == "treble"]
    for i in rng.sample(treble, min(REPEATS, len(treble))):
        bands.append(Band(bands[i].hz, bands[i].kind, repeat_of=i, width=bands[i].width))
    return bands


def correction_curve(model, target, target_name: str, primary_hz: float, upper_hz: float | None):
    """(freqs, correction dB) one ear's EQ applies for these resonances (as ``match_channels`` does)."""
    eq = match_target(model, primary_hz, *target, target_name)
    eq = adjust_personal_treble(eq, primary_hz, upper_hz)
    return np.asarray(eq.freqs, float), np.asarray(eq.correction_db, float)


def band_average(freqs, db, hz: float, width: float = BAND_OCTAVES) -> float:
    x = math.log2(hz) + np.linspace(-width / 2, width / 2, 9)
    return float(np.mean(np.interp(x, np.log2(freqs), db)))


@dataclass
class Suggestion:
    side: str
    target: str               # PRIMARY or UPPER
    current_hz: float
    hz: float                 # where the lean says it should be
    evidence: float           # how much better it explains the leans (χ² units)


@dataclass
class BalanceResult:
    status: str               # "balanced", "recheck" or "uneven"
    suggestion: Suggestion | None = None
    broad_lean_db: float = 0.0        # biggest broad lean across the treble (positive = left louder)
    uneven_hz: float | None = None    # where an unexplained lean is biggest
    reseat: str | None = None         # side whose seal looks weaker (less bass)
    unsteady: bool = False            # the repeated bands got rather different answers
    leans: dict[float, float] = field(default_factory=dict)  # Hz → lean relative to the reference


class BalanceModel:
    """Predicts how leans change when one ear's resonance moves (from the calibration and the tuning)."""

    def __init__(self, models: dict, target, target_name: str, primary: dict[str, float],
                 upper: dict[str, float] | None = None):
        self.models, self.target, self.target_name = models, target, target_name
        self.primary, self.upper = dict(primary), dict(upper or {})
        self._current = {side: correction_curve(models[side], target, target_name, primary[side],
                                                self.upper.get(side)) for side in models}

    def hypotheses(self):
        """(side, target, hz) candidates: each resonance moved by up to ±9 %."""
        out = []
        for side in self.models:
            for target, found in ((PRIMARY, self.primary), (UPPER, self.upper)):
                if side not in found:
                    continue
                lo, hi = CONTROL_RANGE if target == PRIMARY else PERSONAL_TREBLE_RANGE
                for step in SHIFT_STEPS:
                    hz = found[side] * 2 ** step
                    if lo <= hz <= hi:
                        out.append((side, target, float(hz), step))
        return out

    def lean_change(self, side: str, target: str, hz: float, bands: list[Band]) -> np.ndarray:
        """Lean (L − R, dB) at ``bands`` if ``side``'s resonance were really at ``hz``: that ear then
        hears the current EQ minus the EQ that would be right."""
        primary = hz if target == PRIMARY else self.primary[side]
        upper = hz if target == UPPER else self.upper.get(side)
        f, right_eq = correction_curve(self.models[side], self.target, self.target_name, primary, upper)
        f0, current = self._current[side]
        sign = 1.0 if side == LEFT else -1.0
        return np.array([sign * (band_average(f0, current, b.hz, b.width) - band_average(f, right_eq, b.hz, b.width))
                         for b in bands])


def analyse(bands: list[Band], leans: list[float], model: BalanceModel | None,
            uncertainty: dict[tuple[str, str], float] | None = None) -> BalanceResult:
    """Read the centred leans. ``uncertainty[(side, target)]``: how unsure (octaves, ~σ) that
    resonance still is — the less certain ear is the likelier one to be off."""
    leans = [float(v) for v in leans]
    reference = np.mean([v for b, v in zip(bands, leans) if b.kind == "reference"] or [0.0])
    rel = [v - reference for v in leans]

    pairs = [(rel[b.repeat_of], v) for b, v in zip(bands, rel) if b.repeat_of is not None]
    unsteady = any(abs(a - b) > UNSTEADY_DB for a, b in pairs)
    sigma = UNSTEADY_NOISE_DB if unsteady else CENTRE_NOISE_DB

    bass = [v for b, v in zip(bands, rel) if b.kind == "bass"]
    reseat = None
    if bass and abs(float(np.mean(bass))) > BASS_DB:
        reseat = RIGHT if np.mean(bass) > 0 else LEFT  # the weaker-sealed ear has less bass

    # treble and upper bands; a repeated band counts once, at its mean
    values: dict[int, list[float]] = {}
    for i, (b, v) in enumerate(zip(bands, rel)):
        if b.kind in ("treble", "upper"):
            values.setdefault(i if b.repeat_of is None else b.repeat_of, []).append(v)
    order = sorted(values, key=lambda i: bands[i].hz)
    measured = [bands[i] for i in order]
    hz = np.array([b.hz for b in measured])
    m = np.array([np.mean(values[i]) for i in order])
    result = BalanceResult("balanced", reseat=reseat, unsteady=unsteady,
                           leans={float(h): float(v) for h, v in zip(hz, m)})
    if len(m) < 3:
        return result

    # a broad lean (constant + tilt in log frequency) is fit or hearing: it's not explained, just reported
    basis = np.stack([np.ones_like(hz), np.log2(hz) - np.log2(hz).mean()], axis=1)
    project = np.eye(len(hz)) - basis @ np.linalg.pinv(basis)
    broad = basis @ np.linalg.pinv(basis) @ m
    result.broad_lean_db = float(broad[np.argmax(np.abs(broad))])
    null = float(np.sum((project @ m) ** 2)) / sigma ** 2

    best = None
    if model is not None:
        for side, target, h, step in model.hypotheses():
            predicted = model.lean_change(side, target, h, measured)
            fit = float(np.sum((project @ (m - predicted)) ** 2)) / sigma ** 2
            spread = (uncertainty or {}).get((side, target), 0.04)
            score = fit + (step / max(spread, 1e-3)) ** 2  # big moves of a well-found resonance are unlikely
            if best is None or score < best[0]:
                best = (score, side, target, h, step)
    if best is not None:
        score, side, target, h, step = best
        evidence = null - score
        if evidence > EVIDENCE and abs(step) >= MIN_SHIFT:
            current = (model.primary if target == PRIMARY else model.upper)[side]
            result.status = "recheck"
            result.suggestion = Suggestion(side, target, current, h, evidence)
            return result

    residual = project @ m
    worst = int(np.argmax(np.abs(residual)))
    if abs(residual[worst]) > BALANCED_DB or abs(result.broad_lean_db) > BROAD_DB:
        result.status = "uneven"
        result.uneven_hz = float(hz[worst]) if abs(residual[worst]) > BALANCED_DB else None
    return result
