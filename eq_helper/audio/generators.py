"""Stateful audio source generators: sine, pink noise, and log sweep.

All generators return mono ``float32`` blocks of length ``frames``. They hold
their own phase/index state so successive blocks are seamless.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


class SineGenerator:
    """Continuously-phased sine wave with click-free frequency updates.

    Tracking phase (not sample index) lets the caller change frequency at any
    time without a discontinuity: we advance ``phase`` by ``2*pi*f*dt`` per
    sample rather than recomputing ``sin(2*pi*f*t)`` from a shared clock.
    """

    def __init__(self, sample_rate: int) -> None:
        self._sr = int(sample_rate)
        self._phase = 0.0

    def reset(self) -> None:
        self._phase = 0.0

    def next_block(self, frames: int, freq: float) -> np.ndarray:
        freq = float(freq)
        dphi = 2.0 * math.pi * freq / self._sr
        phases = self._phase + dphi * np.arange(frames, dtype=np.float64)
        block = np.sin(phases, dtype=np.float64).astype(np.float32)
        # Advance and wrap phase to keep it bounded.
        self._phase = float((self._phase + dphi * frames) % (2.0 * math.pi))
        return block


class PinkNoiseGenerator:
    """Voss-McCartney pink noise (~-3 dB/oct) with persistent state.

    Uses ``num_rows`` (default 16) octave-spaced random sources, each of which
    updates at a different rate. Sum of sources is roughly 1/f in the spectrum.
    """

    def __init__(
        self,
        sample_rate: int,
        num_rows: int = 16,
        seed: Optional[int] = None,
    ) -> None:
        self._sr = int(sample_rate)
        self._num_rows = int(num_rows)
        self._rng = np.random.default_rng(seed)
        self._rows = self._rng.standard_normal(self._num_rows).astype(np.float64)
        self._running_sum = float(self._rows.sum())
        self._counter = 0
        # Normalisation: empirical scaling so RMS is roughly similar to a
        # unit-amplitude signal. Tuned to avoid clipping at amp=0.5.
        self._scale = 1.0 / math.sqrt(self._num_rows)

    def next_block(self, frames: int) -> np.ndarray:
        out = np.empty(frames, dtype=np.float32)
        rows = self._rows
        running = self._running_sum
        counter = self._counter
        num_rows = self._num_rows
        rng_normal = self._rng.standard_normal
        scale = self._scale
        for i in range(frames):
            counter += 1
            # Find lowest set bit position; determines which row updates.
            # Voss-McCartney: row k updates every 2**k samples.
            if counter & ((1 << num_rows) - 1) == 0:
                row_index = num_rows - 1
            else:
                row_index = (counter & -counter).bit_length() - 1
                if row_index >= num_rows:
                    row_index = num_rows - 1
            new_val = rng_normal()
            running += new_val - rows[row_index]
            rows[row_index] = new_val
            # Add a white component for the highest octave
            white = rng_normal()
            out[i] = (running + white) * scale
        self._running_sum = running
        self._counter = counter
        return out


class LogSweepGenerator:
    """Logarithmic (exponential) sine sweep with live duration changes.

    Phase is computed from the closed-form integral of the instantaneous
    frequency

        f(t) = f_start * (f_end / f_start) ** (t / T)

    which integrates to

        phi(t) = 2 * pi * f_start * T / ln(R) * (R ** (t/T) - 1)

    where ``R = f_end / f_start`` and ``T`` is the sweep duration. Sampling
    this closed form every block avoids the drift that a discrete
    phase-accumulator would incur over long sweeps.

    When ``loop`` is True the sample counter wraps at ``T * sample_rate``, and
    because ``R ** 0 - 1 == 0`` the phase returns cleanly to 0 at wrap.
    """

    _MIN_DURATION_S = 0.1

    def __init__(
        self,
        f_start: float,
        f_end: float,
        duration_s: float,
        sample_rate: int,
        loop: bool = True,
    ) -> None:
        if f_start <= 0 or f_end <= 0:
            raise ValueError("sweep frequencies must be positive")
        if f_start == f_end:
            raise ValueError("sweep f_start must differ from f_end")
        self._sr = int(sample_rate)
        self._f_start = float(f_start)
        self._f_end = float(f_end)
        self._duration_s = max(self._MIN_DURATION_S, float(duration_s))
        self._loop = bool(loop)
        self._n = 0  # samples consumed since sweep start
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def duration_s(self) -> float:
        return self._duration_s

    @property
    def loop(self) -> bool:
        return self._loop

    @property
    def progress(self) -> float:
        """Fraction of the sweep that has elapsed, clamped to [0, 1]."""
        total = self._duration_s * self._sr
        if total <= 0:
            return 0.0
        return min(1.0, self._n / total)

    @property
    def current_freq(self) -> float:
        """Instantaneous frequency at the current sample index."""
        t = self._n / self._sr
        ratio = self._f_end / self._f_start
        return float(self._f_start * (ratio ** (t / self._duration_s)))

    def set_duration(self, duration_s: float) -> None:
        """Change the sweep duration while preserving the current freq.

        We remap ``n`` so that ``t/T`` (the position in log-freq space) is
        unchanged: the sweep continues forward at the same instantaneous
        frequency but now reaches ``f_end`` after the new duration.
        """
        new_duration = max(self._MIN_DURATION_S, float(duration_s))
        if new_duration == self._duration_s:
            return
        fraction = self._n / (self._duration_s * self._sr)
        self._duration_s = new_duration
        self._n = int(fraction * new_duration * self._sr)

    def reset(self) -> None:
        self._n = 0
        self._finished = False

    def next_block(self, frames: int) -> np.ndarray:
        if self._finished:
            return np.zeros(frames, dtype=np.float32)

        sr = self._sr
        T = self._duration_s
        ln_r = math.log(self._f_end / self._f_start)
        coeff = 2.0 * math.pi * self._f_start * T / ln_r

        out = np.empty(frames, dtype=np.float32)
        total_samples = int(round(T * sr))

        produced = 0
        while produced < frames:
            remaining_in_sweep = total_samples - self._n
            if remaining_in_sweep <= 0:
                if self._loop:
                    self._n = 0
                    remaining_in_sweep = total_samples
                else:
                    out[produced:] = 0.0
                    self._finished = True
                    return out

            take = min(frames - produced, remaining_in_sweep)
            idx = self._n + np.arange(take, dtype=np.float64)
            t = idx / sr
            # phi(t) = coeff * (exp(ln_r * t / T) - 1)
            phase = coeff * (np.exp(ln_r * t / T) - 1.0)
            out[produced : produced + take] = np.sin(phase).astype(np.float32)
            self._n += take
            produced += take

            # If we just consumed the last sample of a non-looping sweep, flag
            # it as finished so the next call returns silence immediately.
            if self._n >= total_samples and not self._loop:
                self._finished = True
                break

        return out


class PingPongSweepGenerator:
    """Continuously looping bidirectional ("ping-pong") log sweep.

    The instantaneous frequency traces a *triangle in log-frequency*: it glides
    up from ``f_start`` to ``f_end`` over ``duration_s`` (the one-way time),
    then back down over the same time, repeating forever. Because the frequency
    is continuous at the turn-arounds — and the band loops without a jump — the
    output has no pitch reset, which is what makes it good for judging the
    smoothness of a response around a target frequency.

    Implemented with a phase accumulator (not the closed form used by
    ``LogSweepGenerator``): over the few-second spans this is used for, accumulator
    drift is far below audibility, and it makes the bidirectional motion trivial.
    """

    _MIN_DURATION_S = 0.1

    def __init__(
        self,
        f_start: float,
        f_end: float,
        duration_s: float,
        sample_rate: int,
    ) -> None:
        if f_start <= 0 or f_end <= 0:
            raise ValueError("sweep frequencies must be positive")
        if f_start == f_end:
            raise ValueError("sweep f_start must differ from f_end")
        self._sr = int(sample_rate)
        self._f_start = float(f_start)
        self._f_end = float(f_end)
        self._duration_s = max(self._MIN_DURATION_S, float(duration_s))
        self._n = 0  # samples consumed since start (drives the triangle)
        self._phase = 0.0  # accumulated phase (rad), carried across blocks

    @property
    def duration_s(self) -> float:
        return self._duration_s

    def _freq_at(self, n: np.ndarray | float) -> np.ndarray:
        """Instantaneous frequency at sample index ``n`` (scalar or array)."""
        period = self._duration_s * self._sr  # one-way length in samples
        u = (np.asarray(n, dtype=np.float64) / period) % 2.0  # position in [0, 2)
        frac = np.where(u <= 1.0, u, 2.0 - u)  # log-triangle: 0 -> 1 -> 0
        log_start = math.log(self._f_start)
        log_end = math.log(self._f_end)
        return np.exp(log_start + frac * (log_end - log_start))

    @property
    def current_freq(self) -> float:
        return float(self._freq_at(float(self._n)))

    def set_params(
        self,
        f_start: Optional[float] = None,
        f_end: Optional[float] = None,
        duration_s: Optional[float] = None,
    ) -> None:
        """Update band/duration in place (phase and position are preserved)."""
        if f_start is not None:
            self._f_start = float(f_start)
        if f_end is not None:
            self._f_end = float(f_end)
        if duration_s is not None:
            self._duration_s = max(self._MIN_DURATION_S, float(duration_s))

    def reset(self) -> None:
        self._n = 0
        self._phase = 0.0

    def next_block(self, frames: int) -> np.ndarray:
        n = self._n + np.arange(frames, dtype=np.float64)
        freq = self._freq_at(n)
        dphi = 2.0 * np.pi * freq / self._sr
        phase = self._phase + np.cumsum(dphi)
        out = np.sin(phase).astype(np.float32)
        self._phase = float(phase[-1] % (2.0 * np.pi)) if frames > 0 else self._phase
        self._n += frames
        return out
