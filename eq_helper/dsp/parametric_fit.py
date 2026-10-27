"""Fit a parametric EQ (peaking / shelf biquads) to a correction curve.

Many EQ apps only take a handful of parametric filters, each app with its own
limits (filter count, gain and Q ranges, allowed types, a fixed preamp, step
sizes). The fitter honours those limits and reports how close it got, e.g.
"within ±1 dB up to 10 kHz".

Method: on a log-spaced grid, greedily place a filter where the weighted
residual is largest (sized from the residual's own width), then refine every
filter jointly with bounded least squares over (log fc, gain, log Q). Errors
above 10 kHz count less, and above 16 kHz much less: coupler data there is least reliable and many apps
can't follow fine treble detail anyway. No randomness, so fits are repeatable.
Responses are exact cookbook biquads at the target sample rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import least_squares

FILTER_TYPES = ("PK", "LSC", "HSC")
ACCURACY_LIMITS_HZ = (1000, 2000, 4000, 6000, 8000, 10000, 12000, 16000, 20000)


@dataclass(frozen=True)
class PeqFilter:
    type: str  # "PK" | "LSC" | "HSC"
    fc: float
    gain_db: float
    q: float


@dataclass(frozen=True)
class PeqLimits:
    max_filters: int = 10
    gain_range: tuple[float, float] = (-12.0, 12.0)
    q_range: tuple[float, float] = (0.3, 10.0)
    fc_range: tuple[float, float] = (20.0, 20000.0)
    types: tuple[str, ...] = FILTER_TYPES
    fixed_preamp: float | None = None
    sample_rate: int = 48000
    gain_step: float | None = None
    q_step: float | None = None
    fc_step: float | None = None

    def __post_init__(self):
        bad = [t for t in self.types if t not in FILTER_TYPES]
        if bad or not self.types:
            raise ValueError(f"Unsupported filter type(s) {bad}; allowed: {FILTER_TYPES}.")
        if self.max_filters < 1:
            raise ValueError("At least one filter is required.")
        if "PK" not in self.types and self.max_filters > 2:
            object.__setattr__(self, "max_filters", 2)  # one of each shelf at most

    @property
    def fc_max(self) -> float:
        return min(self.fc_range[1], 0.45 * self.sample_rate)


@dataclass(frozen=True)
class Accuracy:
    max_error_db: dict[int, float]

    def within_hz(self, tolerance_db: float = 1.0) -> int:
        """Highest checked frequency up to which the fit stays within tolerance."""
        best = 0
        for limit in sorted(self.max_error_db):
            if self.max_error_db[limit] <= tolerance_db:
                best = limit
            else:
                break
        return best

    def describe(self, tolerance_db: float = 1.0) -> str:
        upto = self.within_hz(tolerance_db)
        if not upto:
            return f"more than ±{tolerance_db:g} dB off below 1 kHz"
        return f"within ±{tolerance_db:g} dB up to {upto / 1000:g} kHz"


@dataclass(frozen=True)
class ParametricEQ:
    filters: list[PeqFilter]
    preamp_db: float
    sample_rate: int
    accuracy: Accuracy = field(default_factory=lambda: Accuracy({}))


# ── responses ───────────────────────────────────────────────────────────────

def _coeffs(filt: PeqFilter, fs: int):
    a = 10.0 ** (filt.gain_db / 40.0)
    w0 = 2.0 * math.pi * filt.fc / fs
    alpha = math.sin(w0) / (2.0 * filt.q)
    c = math.cos(w0)
    if filt.type == "PK":
        b = (1 + alpha * a, -2 * c, 1 - alpha * a)
        den = (1 + alpha / a, -2 * c, 1 - alpha / a)
    else:
        s = 2.0 * math.sqrt(a) * alpha
        if filt.type == "LSC":
            b = (a * ((a + 1) - (a - 1) * c + s), 2 * a * ((a - 1) - (a + 1) * c), a * ((a + 1) - (a - 1) * c - s))
            den = ((a + 1) + (a - 1) * c + s, -2 * ((a - 1) + (a + 1) * c), (a + 1) + (a - 1) * c - s)
        else:
            b = (a * ((a + 1) + (a - 1) * c + s), -2 * a * ((a - 1) + (a + 1) * c), a * ((a + 1) + (a - 1) * c - s))
            den = ((a + 1) - (a - 1) * c + s, 2 * ((a - 1) - (a + 1) * c), (a + 1) - (a - 1) * c - s)
    return b, den


def filters_response_db(filters, freqs, fs: int) -> np.ndarray:
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs, dtype=float) / fs)
    total = np.zeros(len(z))
    for filt in filters:
        b, a = _coeffs(filt, fs)
        h = (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
        total += 20 * np.log10(np.abs(h) + 1e-30)
    return total


def peaking_db(freqs, fc, gain_db, q, fs: int = 48000) -> np.ndarray:
    return filters_response_db([PeqFilter("PK", fc, gain_db, q)], freqs, fs)


def max_error_db(peq: ParametricEQ, freqs, target_db, upto: float = 20000) -> float:
    f = np.asarray(freqs, dtype=float)
    mask = (f >= 20) & (f <= upto)
    err = filters_response_db(peq.filters, f[mask], peq.sample_rate) - np.asarray(target_db, dtype=float)[mask]
    return float(np.max(np.abs(err))) if mask.any() else 0.0


# ── fitting ─────────────────────────────────────────────────────────────────

def _grid(limits: PeqLimits) -> np.ndarray:
    return np.geomspace(20.0, min(20000.0, 0.49 * limits.sample_rate), 480)


def _weights(grid: np.ndarray) -> np.ndarray:
    return np.where(grid <= 10000, 1.0, np.where(grid <= 16000, 0.35, 0.1))


def _initial_filter(grid, residual, weights, limits: PeqLimits, used_shelves) -> PeqFilter:
    lo_g, hi_g = limits.gain_range
    lo_q, hi_q = limits.q_range
    low = grid <= 120
    high = grid >= 9000
    if "LSC" in limits.types and "LSC" not in used_shelves and abs(np.mean(residual[low])) > 1.0:
        return PeqFilter("LSC", 105.0, float(np.clip(np.mean(residual[low]), lo_g, hi_g)), float(np.clip(0.7, lo_q, hi_q)))
    if "PK" not in limits.types:
        if "HSC" in limits.types and "HSC" not in used_shelves:
            return PeqFilter("HSC", 10000.0, float(np.clip(np.mean(residual[high]), lo_g, hi_g)), float(np.clip(0.7, lo_q, hi_q)))
        return PeqFilter("LSC", 105.0, float(np.clip(np.mean(residual[low]), lo_g, hi_g)), float(np.clip(0.7, lo_q, hi_q)))
    allowed = (grid >= limits.fc_range[0]) & (grid <= limits.fc_max)
    score = np.where(allowed, np.abs(residual) * weights, -1)
    i = int(np.argmax(score))
    gain = float(residual[i])
    # Width where the residual falls to half its value, on the same sign.
    half = abs(gain) / 2
    j, k = i, i
    while j > 0 and residual[j - 1] * np.sign(gain) > half:
        j -= 1
    while k < len(grid) - 1 and residual[k + 1] * np.sign(gain) > half:
        k += 1
    bw_oct = max(np.log2(grid[k] / grid[j]), 1 / 24)
    q = math.sqrt(2 ** bw_oct) / (2 ** bw_oct - 1)
    return PeqFilter("PK", float(np.clip(grid[i], limits.fc_range[0], limits.fc_max)),
                     float(np.clip(gain, lo_g, hi_g)), float(np.clip(q, lo_q, hi_q)))


def _refine(filters, grid, target, weights, limits: PeqLimits):
    lo_g, hi_g = limits.gain_range
    lo_q, hi_q = limits.q_range
    lo_f, hi_f = np.log(limits.fc_range[0]), np.log(limits.fc_max)
    eps = 1e-9
    x0, lb, ub = [], [], []
    for filt in filters:
        x0 += [np.clip(np.log(filt.fc), lo_f, hi_f), np.clip(filt.gain_db, lo_g, hi_g),
               np.clip(np.log(filt.q), np.log(lo_q), np.log(hi_q))]
        lb += [lo_f - eps, lo_g - eps, np.log(lo_q) - eps]
        ub += [hi_f + eps, hi_g + eps, np.log(hi_q) + eps]
    types = [f.type for f in filters]

    def unpack(x):
        return [PeqFilter(t, float(np.exp(x[3 * n])), float(x[3 * n + 1]), float(np.exp(x[3 * n + 2])))
                for n, t in enumerate(types)]

    def residual(x):
        return (filters_response_db(unpack(x), grid, limits.sample_rate) - target) * weights

    result = least_squares(residual, np.array(x0), bounds=(np.array(lb), np.array(ub)),
                           x_scale=np.tile([0.3, 3.0, 0.5], len(filters)), max_nfev=300 * len(filters))
    fitted = unpack(result.x)
    return [replace(f, fc=float(np.clip(f.fc, limits.fc_range[0], limits.fc_max)),
                    gain_db=float(np.clip(f.gain_db, lo_g, hi_g)),
                    q=float(np.clip(f.q, lo_q, hi_q))) for f in fitted]


def _quantise(filters, limits: PeqLimits):
    def step(value, size, lo, hi):
        if not size:
            return value
        return float(np.clip(round(round(value / size) * size, 10), lo, hi))
    return [PeqFilter(f.type,
                      step(f.fc, limits.fc_step, limits.fc_range[0], limits.fc_max),
                      step(f.gain_db, limits.gain_step, *limits.gain_range),
                      step(f.q, limits.q_step, *limits.q_range)) for f in filters]


def _accuracy(filters, freqs, target, fs) -> Accuracy:
    peq = ParametricEQ(filters, 0.0, fs)
    return Accuracy({limit: max_error_db(peq, freqs, target, upto=limit) for limit in ACCURACY_LIMITS_HZ})


def _fit_filters(freqs, target_db, limits: PeqLimits) -> list[PeqFilter]:
    f, y = np.asarray(freqs, dtype=float), np.asarray(target_db, dtype=float)
    order = np.argsort(f)
    f, y = f[order], y[order]
    grid = _grid(limits)
    target = np.interp(np.log(grid), np.log(f), y)
    weights = _weights(grid)
    filters: list[PeqFilter] = []
    for _ in range(limits.max_filters):
        residual = target - filters_response_db(filters, grid, limits.sample_rate)
        if np.max(np.abs(residual) * weights) < 0.05:
            break
        used = {x.type for x in filters if x.type != "PK"}
        candidate = _initial_filter(grid, residual, weights, limits, used)
        trial = _refine(filters + [candidate], grid, target, weights, limits)
        before = np.sqrt(np.mean((residual * weights) ** 2))
        after = np.sqrt(np.mean(((target - filters_response_db(trial, grid, limits.sample_rate)) * weights) ** 2))
        if after >= before - 1e-4:
            break  # another filter no longer helps
        filters = trial
    return _quantise(filters, limits)


def _preamp(filters_by_side, limits: PeqLimits) -> float:
    if limits.fixed_preamp is not None:
        return float(limits.fixed_preamp)
    probe = np.geomspace(20, min(20000.0, 0.49 * limits.sample_rate), 4000)
    worst = max((filters_response_db(fl, probe, limits.sample_rate).max() for fl in filters_by_side), default=0.0)
    return -max(0.0, float(worst)) - 1.0


def fit_parametric(freqs, target_db, limits: PeqLimits = PeqLimits()) -> ParametricEQ:
    filters = _fit_filters(freqs, target_db, limits)
    return ParametricEQ(filters, _preamp([filters], limits), limits.sample_rate,
                        _accuracy(filters, freqs, target_db, limits.sample_rate))


def fit_stereo(curves, limits: PeqLimits = PeqLimits()) -> dict[str, ParametricEQ]:
    """Fit each side independently; both share one preamp so balance is kept."""
    fitted = {side: _fit_filters(f, y, limits) for side, (f, y) in curves.items()}
    preamp = _preamp(fitted.values(), limits)
    return {side: ParametricEQ(fl, preamp, limits.sample_rate,
                               _accuracy(fl, *curves[side], limits.sample_rate))
            for side, fl in fitted.items()}
