"""Balance EQ: correction = reference - other, fit to an exact channel EQ."""

import numpy as np
import pytest

from eq_helper.dsp.balance_eq import balance_correction, generate_balance_eq
from eq_helper.dsp.graphic_eq import graphic_eq_response


def test_correction_is_ref_minus_other():
    f = np.geomspace(20, 20000, 400)
    grid, corr = balance_correction(f, 3.0 + 0 * f, f, 1.0 + 0 * f)
    assert np.allclose(corr, 2.0, atol=1e-6)  # full level matched


def test_match_level_false_removes_mean():
    f = np.geomspace(20, 20000, 400)
    grid, corr = balance_correction(f, 3.0 + 0 * f, f, 1.0 + 0 * f, match_level=False)
    assert abs(float(np.mean(corr))) < 1e-6


def test_shape_difference_preserved():
    f = np.geomspace(20, 20000, 600)
    lf = np.log10(f)
    ref = 5 * np.exp(-((lf - np.log10(2000)) ** 2) / (2 * 0.05 ** 2))
    oth = np.zeros_like(f)
    grid, corr = balance_correction(f, ref, f, oth)
    # correction at 2 kHz ~ +5 dB (ref had a peak there, other was flat)
    i2k = int(np.argmin(np.abs(grid - 2000)))
    assert corr[i2k] > 4.5


def test_no_overlap_raises():
    with pytest.raises(ValueError):
        balance_correction(
            np.array([20.0, 200.0]), np.array([0.0, 0.0]),
            np.array([2000.0, 9000.0]), np.array([0.0, 0.0]),
        )


def test_corrected_side_matches_reference():
    f = np.geomspace(20, 20000, 800)
    lf = np.log10(f)
    ref = 4 * np.exp(-((lf - np.log10(3000)) ** 2) / (2 * 0.04 ** 2))   # peak
    oth = -3 * np.exp(-((lf - np.log10(6000)) ** 2) / (2 * 0.05 ** 2))  # dip
    ceq, max_dev = generate_balance_eq(f, ref, f, oth, "R", 48000)
    assert ceq.channels == ("R",)
    assert max_dev < 0.1
    corr = graphic_eq_response(ceq.geq.bands, f)
    assert np.max(np.abs((oth + corr) - ref)) < 0.15  # other -> reference


def test_overlap_clamps_to_shared_range():
    ref_f = np.geomspace(10, 24000, 500)
    oth_f = np.geomspace(30, 18000, 500)
    grid, corr = balance_correction(ref_f, 0 * ref_f, oth_f, 0 * oth_f)
    assert grid.min() >= 30 - 1e-6
    assert grid.max() <= 18000 + 1e-6
