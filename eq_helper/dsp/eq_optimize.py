"""Collapse a combined EQ response into a single APO GraphicEQ, verified against
APO's exact min-phase-FIR realization; and build region-bypass corrections.

Two user-facing operations are powered here (both Qt-free, pure functions):

- :func:`optimize_profile` — combine a whole include tree (parametric filters +
  preamp + graphic EQs) into ONE equivalent ``GraphicEQ``. Because APO realizes
  a ``GraphicEQ`` as a fixed-cost (~15 biquads) minimum-phase 16384-tap FIR, one
  GraphicEQ replacing hundreds of biquads is *lighter*, with ~0 added latency.
  We return the GraphicEQ plus the measured max deviation of APO's realization
  from the true target, so the UI can promise "exact to X dB".

- :func:`region_bypass_correction` — a ``GraphicEQ`` that cancels the combined EQ
  inside a chosen band (smooth raised-cosine edges, flat/zero outside), so adding
  it flattens the net response there while leaving everything else untouched.

The fit (:func:`fit_graphic_eq`) places points so APO's interpolation (linear dB
along log-frequency) reproduces the target within tolerance. Verification
(:func:`realized_response_db`) simulates APO's GraphicEQ engine end to end.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Tuple

import numpy as np

from eq_helper.dsp.eq_response import load_profile_eq_response
from eq_helper.dsp.graphic_eq import GraphicEQ, GraphicEQBand

# Dense log grid used to sample the combined response before fitting.
_FIT_FREQS = np.geomspace(20.0, 20000.0, 4000)


# --------------------------------------------------------------- adaptive fit


def fit_graphic_eq(
    freqs: np.ndarray,
    target_db: np.ndarray,
    *,
    tol_db: float = 0.05,
    max_points: int = 600,
) -> List[GraphicEQBand]:
    """Greedy adaptive piecewise-linear fit in (dB, log-frequency).

    APO interpolates ``GraphicEQ`` points linearly in dB along log-frequency, so
    we place points so that interpolation reproduces ``target_db`` within
    ``tol_db`` everywhere on ``freqs``. Returns >= 2 sorted bands; gains rounded
    to 0.01 dB (well below the tolerance) for clean APO text.
    """
    freqs = np.asarray(freqs, dtype=float)
    target_db = np.asarray(target_db, dtype=float)
    order = np.argsort(freqs)
    freqs, target_db = freqs[order], target_db[order]
    logf = np.log10(np.maximum(freqs, 1e-9))

    idx = [0, len(freqs) - 1]
    while len(idx) < max_points:
        sel = sorted(idx)
        approx = np.interp(logf, logf[sel], target_db[sel])
        err = np.abs(approx - target_db)
        j = int(np.argmax(err))
        if err[j] <= tol_db or j in idx:
            break
        idx.append(j)

    sel = sorted(set(idx))
    return [
        GraphicEQBand(freq_hz=float(freqs[i]), gain_db=round(float(target_db[i]), 2))
        for i in sel
    ]


# ----------------------------------------------- APO min-phase-FIR realization


def _min_phase_fir(mag_lin_full: np.ndarray, n_taps: int) -> np.ndarray:
    """Homomorphic (cepstral) minimum-phase FIR from a full-grid magnitude.

    ``mag_lin_full`` is the linear magnitude sampled on a length-N FFT grid
    (N even, conjugate-symmetric implied). Returns the first ``n_taps`` samples
    of the minimum-phase impulse response.
    """
    mag = np.maximum(np.asarray(mag_lin_full, dtype=float), 1e-8)
    logmag = np.log(mag)
    cep = np.fft.ifft(logmag).real  # real cepstrum
    n = len(cep)
    w = np.zeros(n)
    w[0] = 1.0
    half = n // 2
    w[1:half] = 2.0
    if n % 2 == 0:
        w[half] = 1.0
    complex_cep_spectrum = np.fft.fft(cep * w)
    H = np.exp(complex_cep_spectrum)
    h = np.fft.ifft(H).real
    return h[:n_taps]


def _interp_db_loglinear(bands: List[GraphicEQBand], freqs_hz: np.ndarray) -> np.ndarray:
    """APO-style interpolation: linear dB along log-frequency, flat-held outside
    the configured point range (``np.interp`` clamps to the end values)."""
    sb = sorted(bands, key=lambda b: b.freq_hz)
    bf = np.array([math.log10(max(b.freq_hz, 1e-9)) for b in sb])
    bd = np.array([b.gain_db for b in sb], dtype=float)
    lf = np.log10(np.maximum(np.asarray(freqs_hz, dtype=float), 1e-9))
    return np.interp(lf, bf, bd)


def realized_response_db(
    bands: List[GraphicEQBand],
    freqs: np.ndarray,
    sample_rate: int,
    *,
    n_taps: int = 16384,
    n_fft: int = 131072,
) -> np.ndarray:
    """Magnitude (dB) at ``freqs`` of APO's min-phase-FIR realization of
    ``bands`` at ``sample_rate`` — i.e. what APO's GraphicEQ engine actually
    produces. Used to verify that an optimized GraphicEQ is exact."""
    half = n_fft // 2
    bin_f = np.arange(half + 1) * (sample_rate / n_fft)
    mag_db_half = _interp_db_loglinear(bands, np.maximum(bin_f, 1e-9))
    mag_half = 10.0 ** (mag_db_half / 20.0)
    mag_full = np.concatenate([mag_half, mag_half[-2:0:-1]])  # even length n_fft

    h = _min_phase_fir(mag_full, n_taps)

    fft_len = max(n_fft, 2 * n_taps)
    H = np.fft.rfft(h, n=fft_len)
    grid = np.fft.rfftfreq(fft_len, d=1.0 / sample_rate)
    realized_db_grid = 20.0 * np.log10(np.abs(H) + 1e-12)
    return np.interp(np.asarray(freqs, dtype=float), grid, realized_db_grid)


# ------------------------------------------------------------- optimize a stack


def optimize_profile(
    profile_path,
    sample_rate: int,
    *,
    tol_db: float = 0.02,
    name: str = "Optimized",
) -> Tuple[GraphicEQ, float]:
    """Combine the whole include tree at ``profile_path`` into one ``GraphicEQ``.

    Returns ``(GraphicEQ, max_deviation_db)`` where the deviation is APO's exact
    min-phase-FIR realization measured against the true combined response across
    the audible range. Raises ``ValueError`` if the profile can't be read.
    """
    freqs = _FIT_FREQS
    target = load_profile_eq_response(Path(profile_path), freqs)
    if target is None:
        raise ValueError(f"could not read profile: {profile_path}")
    bands = fit_graphic_eq(freqs, target, tol_db=tol_db)
    geq = GraphicEQ(name=name, bands=bands)
    realized = realized_response_db(bands, freqs, sample_rate)
    audible = (freqs >= 20.0) & (freqs <= 20000.0)
    max_dev = float(np.max(np.abs(realized[audible] - target[audible])))
    return geq, max_dev


# --------------------------------------------------------------- region bypass


def _raised_cosine_window(
    freqs: np.ndarray, lo: float, hi: float, transition_oct: float
) -> np.ndarray:
    """1 inside ``[lo, hi]``, raised-cosine ramp to 0 over ``transition_oct``
    octaves at each edge. The ramps lie OUTSIDE ``[lo, hi]`` so the full band is
    flattened; outside the ramps the window is 0 (EQ left untouched)."""
    lf = np.log10(np.maximum(np.asarray(freqs, dtype=float), 1e-9))
    llo, lhi = math.log10(lo), math.log10(hi)
    t = max(transition_oct * math.log10(2.0), 1e-6)
    w = np.zeros_like(lf)
    w[(lf >= llo) & (lf <= lhi)] = 1.0
    left = (lf >= llo - t) & (lf < llo)
    w[left] = 0.5 * (1.0 - np.cos(np.pi * (lf[left] - (llo - t)) / t))
    right = (lf > lhi) & (lf <= lhi + t)
    w[right] = 0.5 * (1.0 + np.cos(np.pi * (lf[right] - lhi) / t))
    return w


def region_bypass_correction(
    profile_path,
    lo: float,
    hi: float,
    freqs: np.ndarray,
    *,
    transition_oct: float = 1.0 / 6.0,
    tol_db: float = 0.02,
    name: str = "bypass",
) -> GraphicEQ:
    """A ``GraphicEQ`` that cancels the combined EQ inside ``[lo, hi]``.

    The correction is ``-R(f) * W(f)`` where ``R`` is the combined response and
    ``W`` is a raised-cosine band window. Adding it makes the net response flat
    inside the band (smooth at the edges) and unchanged outside. Raises
    ``ValueError`` if the profile can't be read."""
    lo, hi = float(min(lo, hi)), float(max(lo, hi))
    R = load_profile_eq_response(Path(profile_path), freqs)
    if R is None:
        raise ValueError(f"could not read profile: {profile_path}")
    W = _raised_cosine_window(freqs, lo, hi, transition_oct)
    correction = -R * W
    bands = fit_graphic_eq(freqs, correction, tol_db=tol_db)
    return GraphicEQ(name=name, bands=bands)
