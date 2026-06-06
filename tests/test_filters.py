"""Tests for the band-pass biquad filter."""

from __future__ import annotations

import numpy as np

from eq_helper.audio.filters import BandPassFilter, make_bandpass_sos
from eq_helper.audio.generators import SineGenerator


SR = 48000


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def test_bandpass_attenuates_out_of_band() -> None:
    flt = BandPassFilter(SR)
    flt.set_params(fc=1000.0, bw=200.0)

    sine = SineGenerator(SR)
    # In-band: 1 kHz should pass.
    in_band = sine.next_block(8192, 1000.0)
    sine.reset()
    out_in = flt.process(in_band.copy())
    # Reset state before second test.
    flt.reset()
    low = sine.next_block(8192, 100.0)
    out_low = flt.process(low.copy())
    flt.reset()
    sine.reset()
    high = sine.next_block(8192, 10000.0)
    out_high = flt.process(high.copy())

    rms_in = _rms(out_in)
    rms_low = _rms(out_low)
    rms_high = _rms(out_high)

    # In-band signal should be within a few dB of the input.
    assert rms_in > 0.3 * _rms(in_band)
    # Out-of-band signals should be at least 10 dB quieter (factor ~3.16).
    assert rms_low < rms_in / 3.0
    assert rms_high < rms_in / 3.0


def test_bandpass_block_boundary_is_continuous() -> None:
    flt = BandPassFilter(SR)
    flt.set_params(fc=500.0, bw=200.0)
    sine = SineGenerator(SR)
    parts = []
    for _ in range(10):
        block = sine.next_block(480, 500.0)
        parts.append(flt.process(block))
    full = np.concatenate(parts)
    diffs = np.diff(full)
    # Max neighbour delta should stay bounded — no DC steps or clicks.
    assert np.max(np.abs(diffs)) < 0.3, np.max(np.abs(diffs))
    assert np.isfinite(full).all()


def test_bandpass_set_params_keeps_shape_stable() -> None:
    flt = BandPassFilter(SR)
    flt.set_params(fc=500.0, bw=200.0)
    first_shape = flt.sos_copy().shape  # type: ignore[union-attr]
    flt.set_params(fc=2000.0, bw=1000.0)
    second_shape = flt.sos_copy().shape  # type: ignore[union-attr]
    assert first_shape == second_shape


def test_make_bandpass_sos_clamps_to_nyquist() -> None:
    sos = make_bandpass_sos(fc=19000.0, bw=4000.0, sample_rate=SR)
    assert sos.shape[1] == 6
    assert np.isfinite(sos).all()
