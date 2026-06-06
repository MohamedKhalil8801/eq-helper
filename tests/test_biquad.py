"""Tests for Audio EQ Cookbook biquad filter design."""

from __future__ import annotations

import numpy as np
import pytest

from eq_helper.dsp.biquad import (
    high_shelf_sos,
    highpass_sos,
    low_shelf_sos,
    lowpass_sos,
    peaking_sos,
    sos_magnitude_response,
)

FS = 48000
FREQS = np.geomspace(20.0, 20000.0, 500)


def _response_at(sos, freq):
    """Get the magnitude response in dB at a single frequency."""
    return float(sos_magnitude_response(sos, np.array([freq]), FS)[0])


def test_peaking_boost_at_center() -> None:
    sos = peaking_sos(1000.0, 6.0, 1.0, FS)
    assert sos.shape == (1, 6)
    # +6 dB at 1 kHz.
    assert _response_at(sos, 1000.0) == pytest.approx(6.0, abs=0.1)
    # Near 0 dB far away.
    assert abs(_response_at(sos, 50.0)) < 0.5
    assert abs(_response_at(sos, 15000.0)) < 0.5


def test_peaking_cut_at_center() -> None:
    sos = peaking_sos(2000.0, -4.0, 2.0, FS)
    assert _response_at(sos, 2000.0) == pytest.approx(-4.0, abs=0.1)


def test_peaking_zero_gain_is_flat() -> None:
    sos = peaking_sos(1000.0, 0.0, 1.0, FS)
    resp = sos_magnitude_response(sos, FREQS, FS)
    np.testing.assert_allclose(resp, 0.0, atol=0.01)


def test_low_shelf_boosts_bass() -> None:
    sos = low_shelf_sos(200.0, 5.0, 0.7, FS)
    low = _response_at(sos, 30.0)
    high = _response_at(sos, 10000.0)
    assert low > 3.0
    assert abs(high) < 1.0


def test_high_shelf_boosts_treble() -> None:
    sos = high_shelf_sos(5000.0, 4.0, 0.7, FS)
    low = _response_at(sos, 50.0)
    high = _response_at(sos, 18000.0)
    assert abs(low) < 1.0
    assert high > 2.5


def test_lowpass_attenuates_above_cutoff() -> None:
    sos = lowpass_sos(1000.0, 0.707, FS)
    below = _response_at(sos, 100.0)
    above = _response_at(sos, 10000.0)
    assert abs(below) < 1.0
    assert above < -20.0


def test_highpass_attenuates_below_cutoff() -> None:
    sos = highpass_sos(1000.0, 0.707, FS)
    below = _response_at(sos, 50.0)
    above = _response_at(sos, 10000.0)
    assert below < -20.0
    assert abs(above) < 1.0


def test_all_sos_shapes_are_valid() -> None:
    for fn in [peaking_sos, low_shelf_sos, high_shelf_sos]:
        sos = fn(1000.0, 3.0, 1.0, FS)
        assert sos.shape == (1, 6)
        assert np.isfinite(sos).all()
    for fn in [lowpass_sos, highpass_sos]:
        sos = fn(1000.0, 1.0, FS)
        assert sos.shape == (1, 6)
        assert np.isfinite(sos).all()
