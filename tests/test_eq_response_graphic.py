"""GraphicEQ: lines must contribute to the combined EQ response (gap fix)."""

import numpy as np

from eq_helper.dsp.eq_response import compute_eq_response


def test_graphic_eq_line_contributes():
    text = "GraphicEQ: 100 0; 1000 6; 10000 0\n"
    freqs = np.array([100.0, 1000.0, 10000.0])
    resp = compute_eq_response(text, freqs)
    assert abs(resp[1] - 6.0) < 0.01  # +6 dB at 1 kHz
    assert abs(resp[0] - 0.0) < 0.01
    assert abs(resp[2] - 0.0) < 0.01


def test_graphic_eq_sums_with_filters():
    text = "Preamp: -3 dB\nGraphicEQ: 100 2; 1000 2; 10000 2\n"
    freqs = np.array([1000.0])
    resp = compute_eq_response(text, freqs)
    assert abs(resp[0] - (-1.0)) < 0.01  # -3 preamp + 2 geq


def test_graphic_eq_plus_peaking_filter():
    text = (
        "GraphicEQ: 100 0; 1000 4; 10000 0\n"
        "Filter 1: ON PK Fc 1000 Hz Gain 3 dB Q 3.0\n"
    )
    freqs = np.array([1000.0])
    resp = compute_eq_response(text, freqs)
    assert abs(resp[0] - 7.0) < 0.05  # 4 (geq) + 3 (PK peak)
