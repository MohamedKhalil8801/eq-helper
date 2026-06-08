"""Collapse a combined EQ response into a single APO GraphicEQ, verified against
APO's exact min-phase-FIR realization; and build region-bypass corrections.

Two user-facing operations are powered here (both Qt-free, pure functions):

- :func:`optimize_profile` — combine a whole include tree (parametric filters +
  preamp + graphic EQs) into ONE equivalent ``GraphicEQ``. Because APO realizes
  a ``GraphicEQ`` as a fixed-cost (~15 biquads) minimum-phase 16384-tap FIR, one
  GraphicEQ replacing hundreds of biquads is *lighter*, with ~0 added latency.
  We return the GraphicEQ plus the measured max deviation of APO's realization
  from the true target, so the UI can promise "exact to X dB".

- :func:`region_bypass_groups` — per-channel ``GraphicEQ`` corrections that cancel
  the combined EQ inside a chosen band (smooth raised-cosine edges, flat/zero
  outside), so adding them flattens the net response there while leaving
  everything else untouched.

The fit (:func:`fit_graphic_eq`) places points so APO's interpolation (linear dB
along log-frequency) reproduces the target within tolerance. Verification
(:func:`realized_response_db`) simulates APO's GraphicEQ engine end to end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from eq_helper.dsp.eq_response import (
    ALL_CHANNELS,
    load_profile_channel_responses,
    load_profile_feature_freqs,
)
from eq_helper.dsp.graphic_eq import (
    GraphicEQ,
    GraphicEQBand,
    graphic_eq_to_apo_text,
)

# Dense log grid used to sample the combined response before fitting.
_FIT_FREQS = np.geomspace(20.0, 20000.0, 4000)

# Octave offsets sprinkled around each feature (filter center / GraphicEQ point)
# so narrow, high-Q peaks/notches are always resolved by both the fit and the
# verification grid — a peak whose Fc lands between coarse samples is otherwise
# invisible (the fit can't shape it and max_dev can't detect the resulting error).
_FLANK_OCT = np.array(
    [0.0005, 0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.064, 0.125, 0.25]
)

_F_MIN, _F_MAX = 20.0, 20000.0


def _build_eval_grid(feature_freqs: List[float], n_base: int) -> np.ndarray:
    """A log grid of ``n_base`` points over [20, 20000] augmented with each
    feature frequency and a multiscale cluster of flanking points around it, so
    the steep sides and apex of narrow features are sampled."""
    pts = [np.geomspace(_F_MIN, _F_MAX, n_base)]
    for fc in feature_freqs:
        fc = float(min(max(fc, _F_MIN), _F_MAX))
        pts.append(np.array([fc]))
        pts.append(fc * (2.0 ** _FLANK_OCT))
        pts.append(fc * (2.0 ** (-_FLANK_OCT)))
    grid = np.clip(np.concatenate(pts), _F_MIN, _F_MAX)
    return np.unique(grid)


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


# --------------------------------------------------------- channel grouping


@dataclass
class ChannelEQ:
    """One GraphicEQ scoped to a set of channels.

    ``channels=None`` means "all channels" — rendered as a bare ``GraphicEQ:``
    line with no ``Channel:`` directive (applies everywhere). A non-None tuple
    (e.g. ``("L",)``) renders a ``Channel: L`` directive before the GraphicEQ.
    """

    channels: Optional[Tuple[str, ...]]
    geq: GraphicEQ


def render_channel_eqs(groups: List[ChannelEQ]) -> str:
    """Render channel-scoped GraphicEQs to APO text.

    A single all-channel group is one bare ``GraphicEQ:`` line. Otherwise each
    group is a ``Channel: <ids>`` directive followed by its GraphicEQ, and the
    block ends with ``Channel: all`` so the selection never leaks into EQ files
    APO processes afterwards.
    """
    if len(groups) == 1 and groups[0].channels is None:
        return graphic_eq_to_apo_text(groups[0].geq) + "\n"
    lines: List[str] = []
    for g in groups:
        chans = "all" if g.channels is None else " ".join(g.channels)
        lines.append(f"Channel: {chans}")
        lines.append(graphic_eq_to_apo_text(g.geq))
    lines.append("Channel: all")  # reset — don't leak channel state downstream
    return "\n".join(lines) + "\n"


def _require_stereo(resps: Dict[str, np.ndarray]) -> None:
    """Raise if a profile references channels beyond stereo L/R. This is a
    headphone/IEM tool; multichannel (center/sub/surround) configs can't be
    safely reproduced without the device's physical layout, so refuse rather
    than silently drop those channels."""
    extra = sorted(set(resps) - {ALL_CHANNELS, "L", "R"})
    if extra:
        raise ValueError(
            "Optimize/Bypass currently supports stereo (L/R) profiles. "
            "This profile uses channel(s): " + ", ".join(extra) + "."
        )


def _responses_all_equal(resps: Dict[str, np.ndarray], atol: float = 0.01) -> bool:
    vals = list(resps.values())
    return all(np.max(np.abs(v - vals[0])) <= atol for v in vals[1:])


def _is_bare(resps: Dict[str, np.ndarray]) -> bool:
    """Whether the result is one bare all-channel GraphicEQ: either no channels
    at all, or the full stereo pair carrying the same EQ on both sides."""
    keys = set(resps)
    if keys == {ALL_CHANNELS}:
        return True
    return keys == {"L", "R"} and _responses_all_equal(resps)


def _group_channels_by_response(
    resps: Dict[str, np.ndarray], atol: float = 0.01
) -> List[Tuple[List[str], np.ndarray]]:
    """Group channels whose responses match within ``atol`` dB. Returns a list
    of ``(sorted_channel_ids, representative_response)``."""
    groups: List[Tuple[List[str], np.ndarray]] = []
    for ch in sorted(resps):
        r = resps[ch]
        for chans, rep in groups:
            if np.max(np.abs(rep - r)) <= atol:
                chans.append(ch)
                break
        else:
            groups.append(([ch], r))
    return groups


# ------------------------------------------------------------- optimize a stack


def optimize_profile(
    profile_path,
    sample_rate: int,
    *,
    tol_db: float = 0.02,
) -> Tuple[List[ChannelEQ], float]:
    """Combine the whole include tree at ``profile_path`` into one GraphicEQ per
    distinct channel response.

    Returns ``(groups, max_deviation_db)``. When every channel has the same EQ
    (or the config references no channels) ``groups`` is a single all-channel
    GraphicEQ. When L/R (etc.) differ, each distinct response becomes its own
    channel-scoped GraphicEQ. ``max_dev`` is the worst per-channel deviation of
    APO's exact min-phase-FIR realization from the true response, measured on an
    independent denser grid seeded with every feature frequency (so narrow
    high-Q peaks are resolved and the deviation can't under-report). Raises
    ``ValueError`` if the profile can't be read.
    """
    path = Path(profile_path)
    features = load_profile_feature_freqs(path)
    fit_freqs = _build_eval_grid(features, n_base=8000)
    ver_freqs = _build_eval_grid(features, n_base=40000)

    fit_resps = load_profile_channel_responses(path, fit_freqs)
    if fit_resps is None:
        raise ValueError(f"could not read profile: {profile_path}")
    _require_stereo(fit_resps)
    ver_resps = load_profile_channel_responses(path, ver_freqs)

    def _fit_and_verify(
        target: np.ndarray, verify_channels: List[str]
    ) -> Tuple[GraphicEQ, float]:
        bands = fit_graphic_eq(fit_freqs, target, tol_db=tol_db)
        realized = realized_response_db(bands, ver_freqs, sample_rate)
        # Verify against EVERY channel the GraphicEQ serves, not just one, so a
        # merged group's reported deviation can't under-report a folded channel.
        dev = max(
            float(np.max(np.abs(realized - ver_resps[ch]))) for ch in verify_channels
        )
        return GraphicEQ(name="Optimized", bands=bands), dev

    # One bare GraphicEQ for no-channel configs or a symmetric stereo pair.
    if _is_bare(fit_resps):
        key = next(iter(fit_resps))
        verify = [ALL_CHANNELS] if set(fit_resps) == {ALL_CHANNELS} else list(fit_resps)
        geq, max_dev = _fit_and_verify(fit_resps[key], verify)
        return [ChannelEQ(channels=None, geq=geq)], max_dev

    groups: List[ChannelEQ] = []
    max_dev = 0.0
    for chans, resp in _group_channels_by_response(fit_resps):
        geq, dev = _fit_and_verify(resp, chans)
        groups.append(ChannelEQ(channels=tuple(chans), geq=geq))
        max_dev = max(max_dev, dev)
    return groups, max_dev


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


def region_bypass_groups(
    profile_path,
    lo: float,
    hi: float,
    *,
    transition_oct: float = 1.0 / 6.0,
    tol_db: float = 0.02,
) -> List[ChannelEQ]:
    """Per-channel ``GraphicEQ`` corrections that cancel the combined EQ inside
    ``[lo, hi]`` on every channel.

    For each channel the correction is ``-R(f) * W(f)`` (``R`` = that channel's
    combined response, ``W`` = a raised-cosine band window). Channels that share
    the same EQ collapse to one bare GraphicEQ; channels that differ get their
    own ``Channel:``-scoped correction. Adding the result makes each channel's
    net response flat inside the band and unchanged outside. The fit runs on a
    feature-aware grid (seeded with every filter center) so narrow in-band
    features are cancelled accurately. Raises ``ValueError`` if the profile
    can't be read or uses non-stereo channels."""
    path = Path(profile_path)
    lo, hi = float(min(lo, hi)), float(max(lo, hi))
    features = load_profile_feature_freqs(path)
    freqs = _build_eval_grid(features, n_base=8000)
    resps = load_profile_channel_responses(path, freqs)
    if resps is None:
        raise ValueError(f"could not read profile: {profile_path}")
    _require_stereo(resps)
    W = _raised_cosine_window(freqs, lo, hi, transition_oct)

    def _corr(resp: np.ndarray) -> GraphicEQ:
        bands = fit_graphic_eq(freqs, -resp * W, tol_db=tol_db)
        return GraphicEQ(name="bypass", bands=bands)

    if _is_bare(resps):
        key = next(iter(resps))
        return [ChannelEQ(channels=None, geq=_corr(resps[key]))]

    return [
        ChannelEQ(channels=tuple(chans), geq=_corr(resp))
        for chans, resp in _group_channels_by_response(resps)
    ]
