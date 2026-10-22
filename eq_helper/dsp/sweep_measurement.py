"""Helpers for converting a recorded log sweep into a frequency response."""

from __future__ import annotations

from typing import Tuple

import numpy as np

from eq_helper.audio.generators import LogSweepGenerator
from eq_helper.dsp.overlay import normalize_at_1khz


def sweep_instantaneous_frequencies(
    f_start: float,
    f_end: float,
    duration_s: float,
    sample_rate: int,
    n_samples: int,
) -> np.ndarray:
    """Return the instantaneous frequency for each sweep sample."""
    if n_samples <= 0:
        return np.empty((0,), dtype=np.float64)
    t = np.arange(n_samples, dtype=np.float64) / float(sample_rate)
    ratio = float(f_end) / float(f_start)
    return np.asarray(
        float(f_start) * (ratio ** (t / float(duration_s))),
        dtype=np.float64,
    )


def _generate_reference_sweep(
    sample_rate: int,
    f_start: float,
    f_end: float,
    duration_s: float,
    n_samples: int,
) -> np.ndarray:
    """Generate a reference log sweep of the requested length."""
    if n_samples <= 0:
        return np.empty((0,), dtype=np.float64)
    sweep = LogSweepGenerator(
        f_start=float(f_start),
        f_end=float(f_end),
        duration_s=float(duration_s),
        sample_rate=int(sample_rate),
        loop=False,
    )
    out = np.empty(n_samples, dtype=np.float64)
    written = 0
    while written < n_samples:
        frames = min(2048, n_samples - written)
        out[written : written + frames] = sweep.next_block(frames).astype(np.float64)
        written += frames
    return out


def _estimate_lag(recorded: np.ndarray, reference: np.ndarray) -> int:
    """Estimate a coarse time offset between the recording and the reference."""
    n = min(int(recorded.size), int(reference.size))
    if n < 8:
        return 0
    rec = recorded[:n].astype(np.float64, copy=False)
    ref = reference[:n].astype(np.float64, copy=False)
    rec = rec - np.mean(rec)
    ref = ref - np.mean(ref)
    corr = np.correlate(rec, ref, mode="full")
    center = corr.size // 2
    return int(np.argmax(corr) - center)


def measurement_from_sweep_recording(
    recorded: np.ndarray,
    sample_rate: int,
    f_start: float,
    f_end: float,
    duration_s: float,
    n_bins: int = 600,
) -> Tuple[np.ndarray, np.ndarray]:
    """Convert a recorded log sweep into a frequency-response measurement.

    The response is estimated by deconvolving the recorded sweep with a generated
    reference sweep in the frequency domain after aligning the recording to the
    reference sweep. This keeps peak positions stable instead of drifting with
    timing offsets or sample-rate mismatches.
    """
    recorded = np.asarray(recorded, dtype=np.float64)
    if recorded.size == 0:
        raise ValueError("recorded sweep is empty")

    n_samples = int(recorded.size)
    reference = _generate_reference_sweep(
        sample_rate=sample_rate,
        f_start=f_start,
        f_end=f_end,
        duration_s=duration_s,
        n_samples=n_samples,
    )
    if reference.size == 0:
        raise ValueError("failed to generate sweep reference")

    lag = _estimate_lag(recorded, reference)
    if lag > 0:
        recorded = recorded[lag:]
    elif lag < 0:
        recorded = np.pad(recorded, (int(-lag), 0))

    n = min(recorded.size, reference.size)
    if n < 8:
        raise ValueError("sweep recording too short for response estimation")

    recorded = recorded[:n]
    reference = reference[:n]

    recorded = recorded - np.mean(recorded)
    reference = reference - np.mean(reference)

    window = np.hanning(n)
    rec_win = recorded * window
    ref_win = reference * window

    recorded_fft = np.fft.rfft(rec_win)
    reference_fft = np.fft.rfft(ref_win)
    freqs = np.fft.rfftfreq(n, d=1.0 / float(sample_rate))

    transfer = np.zeros_like(recorded_fft, dtype=np.complex128)
    denom = np.abs(reference_fft) > 1e-8
    transfer[denom] = recorded_fft[denom] / reference_fft[denom]

    magnitude = np.maximum(np.abs(transfer), 1e-12)
    db = 20.0 * np.log10(magnitude)

    lo = max(20.0, float(f_start))
    hi = min(20000.0, float(f_end))
    if lo >= hi:
        raise ValueError("invalid sweep range")

    bin_edges = np.geomspace(lo, hi, n_bins + 1)
    bin_centers = np.sqrt(bin_edges[:-1] * bin_edges[1:])
    valid = (freqs >= lo) & (freqs <= hi)
    if valid.sum() < 2:
        raise ValueError("insufficient sweep bandwidth for measurement")

    freqs_valid = freqs[valid]
    db_valid = db[valid]
    measured = np.interp(np.log10(bin_centers), np.log10(freqs_valid), db_valid)

    normalized = normalize_at_1khz(bin_centers, measured)
    return bin_centers, normalized
