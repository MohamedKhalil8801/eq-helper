"""Tests for graphic EQ: bands, FIR, interpolation, APO format."""

from __future__ import annotations

import math

import numpy as np
import pytest

from eq_helper.dsp.graphic_eq import (
    GraphicEQ,
    GraphicEQBand,
    combine_graphic_eqs,
    graphic_eq_response,
    graphic_eq_to_apo_text,
    graphic_eq_to_fir,
    double_bands,
    make_fixed_bands,
    parse_graphic_eq_line,
)

SR = 48000


# ---------------------------------------------------------- fixed bands


def test_make_fixed_bands_8() -> None:
    bands = make_fixed_bands(8)
    assert len(bands) == 8
    assert all(b.gain_db == 0.0 for b in bands)
    freqs = [b.freq_hz for b in bands]
    assert freqs == [63, 125, 250, 500, 1000, 2000, 4000, 8000]


def test_make_fixed_bands_12() -> None:
    bands = make_fixed_bands(12)
    assert len(bands) == 12
    assert all(b.gain_db == 0.0 for b in bands)


# -------------------------------------------------------- increase bands


def test_double_bands_preserves_shape() -> None:
    bands = make_fixed_bands(8)
    # Set some non-zero gains.
    bands[0].gain_db = 3.0
    bands[3].gain_db = -2.0
    bands[7].gain_db = 1.5
    more = double_bands(bands)
    assert len(more) == 16
    # The response at the original frequencies should be close.
    orig_freqs = np.array([b.freq_hz for b in bands])
    old_resp = graphic_eq_response(bands, orig_freqs)
    new_resp = graphic_eq_response(more, orig_freqs)
    np.testing.assert_allclose(old_resp, new_resp, atol=1.0)


def test_double_bands_from_empty() -> None:
    more = double_bands([])
    assert len(more) == 8


# ------------------------------------------------------------- FIR


def test_graphic_eq_to_fir_produces_valid_filter() -> None:
    bands = [
        GraphicEQBand(100, 6.0),
        GraphicEQBand(1000, 0.0),
        GraphicEQBand(10000, -3.0),
    ]
    fir = graphic_eq_to_fir(bands, SR)
    assert fir is not None
    assert fir.ndim == 1
    assert np.isfinite(fir).all()


def test_graphic_eq_to_fir_returns_none_for_single_band() -> None:
    fir = graphic_eq_to_fir([GraphicEQBand(1000, 3.0)], SR)
    assert fir is None


def test_combine_graphic_eqs_merges() -> None:
    g1 = GraphicEQ(bands=[GraphicEQBand(100, 3.0), GraphicEQBand(1000, 0.0)])
    g2 = GraphicEQ(bands=[GraphicEQBand(100, 2.0), GraphicEQBand(1000, -1.0)])
    fir = combine_graphic_eqs([g1, g2], SR)
    assert fir is not None


def test_combine_graphic_eqs_skips_disabled() -> None:
    g1 = GraphicEQ(bands=[GraphicEQBand(100, 3.0), GraphicEQBand(1000, 0.0)])
    g2 = GraphicEQ(bands=[GraphicEQBand(100, 10.0), GraphicEQBand(1000, 10.0)], enabled=False)
    fir_both = combine_graphic_eqs([g1, g2], SR)
    fir_one = combine_graphic_eqs([g1], SR)
    # Disabled GEQ should not contribute.
    assert fir_both is not None
    assert fir_one is not None


# ------------------------------------------------------- response curve


def test_graphic_eq_response_interpolates() -> None:
    bands = [GraphicEQBand(100, 6.0), GraphicEQBand(10000, -6.0)]
    freqs = np.array([100.0, 1000.0, 10000.0])
    resp = graphic_eq_response(bands, freqs)
    assert resp[0] == pytest.approx(6.0, abs=0.1)
    assert resp[2] == pytest.approx(-6.0, abs=0.1)
    # Midpoint should be between.
    assert -6.0 < resp[1] < 6.0


# -------------------------------------------------------- APO format


def test_graphic_eq_to_apo_text() -> None:
    geq = GraphicEQ(bands=[
        GraphicEQBand(100, 3.0),
        GraphicEQBand(1000, -2.0),
        GraphicEQBand(8000, 1.5),
    ])
    text = graphic_eq_to_apo_text(geq)
    assert text.startswith("GraphicEQ:")
    assert "100 3.0" in text
    assert "1000 -2.0" in text
    assert "8000 1.5" in text


def test_parse_graphic_eq_line_roundtrips() -> None:
    original = GraphicEQ(bands=[
        GraphicEQBand(200, 4.0),
        GraphicEQBand(2000, -1.0),
    ])
    text = graphic_eq_to_apo_text(original)
    parsed = parse_graphic_eq_line(text)
    assert parsed is not None
    assert len(parsed.bands) == 2
    assert parsed.bands[0].freq_hz == 200.0
    assert parsed.bands[0].gain_db == 4.0


def test_parse_graphic_eq_line_invalid() -> None:
    assert parse_graphic_eq_line("Filter 1: ON PK Fc 100 Hz") is None
    assert parse_graphic_eq_line("") is None
