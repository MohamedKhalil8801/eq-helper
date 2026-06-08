"""Adaptive GraphicEQ fit reproduces an arbitrary target within tolerance."""

import numpy as np

from eq_helper.dsp.eq_optimize import fit_graphic_eq
from eq_helper.dsp.graphic_eq import graphic_eq_response


def test_fit_matches_target_within_tol():
    freqs = np.geomspace(20, 20000, 3000)
    lf = np.log10(freqs)
    target = (
        6 * np.exp(-((lf - np.log10(1000)) ** 2) / (2 * 0.05 ** 2))
        - 4 * np.exp(-((lf - np.log10(4000)) ** 2) / (2 * 0.03 ** 2))
    )
    bands = fit_graphic_eq(freqs, target, tol_db=0.05)
    approx = graphic_eq_response(bands, freqs)
    assert np.max(np.abs(approx - target)) <= 0.07  # chord error ~ tol (+rounding)


def test_fit_flat_target_is_trivial():
    freqs = np.geomspace(20, 20000, 500)
    bands = fit_graphic_eq(freqs, np.full_like(freqs, 2.5), tol_db=0.05)
    assert len(bands) >= 2
    approx = graphic_eq_response(bands, freqs)
    assert np.allclose(approx, 2.5, atol=0.05)


def test_fit_high_q_peak():
    freqs = np.geomspace(20, 20000, 4000)
    lf = np.log10(freqs)
    target = 9 * np.exp(-((lf - np.log10(3000)) ** 2) / (2 * 0.012 ** 2))
    bands = fit_graphic_eq(freqs, target, tol_db=0.05)
    approx = graphic_eq_response(bands, freqs)
    assert np.max(np.abs(approx - target)) <= 0.08
