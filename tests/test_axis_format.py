"""Tests for the custom log-frequency axis labels (Equalizer APO style)."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("pyqtgraph")

from eq_helper.ui.spectrum_view import LogFrequencyAxis, _format_hz


def test_format_hz_common_values() -> None:
    assert _format_hz(20) == "20"
    assert _format_hz(50) == "50"
    assert _format_hz(100) == "100"
    assert _format_hz(500) == "500"
    assert _format_hz(1000) == "1k"
    assert _format_hz(2000) == "2k"
    assert _format_hz(5000) == "5k"
    assert _format_hz(10000) == "10k"
    assert _format_hz(20000) == "20k"


def test_tick_values_cover_audible_decades(qtbot) -> None:
    axis = LogFrequencyAxis(orientation="bottom")
    # Use the padded range matching SpectrumView's setXRange (±0.03 padding)
    # so edge ticks (20, 20k) are always inside the visible area.
    levels = axis.tickValues(math.log10(20) - 0.03, math.log10(20000) + 0.03, 1000)
    assert len(levels) == 2
    majors_hz = sorted(round(10 ** v) for v in levels[0][1])
    # Expect the 1/2/5 multipliers across each audible decade.
    for expected in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
        assert expected in majors_hz, f"missing {expected} in majors: {majors_hz}"


def test_tick_strings_format_with_k_suffix(qtbot) -> None:
    axis = LogFrequencyAxis(orientation="bottom")
    levels = axis.tickValues(math.log10(20), math.log10(20000), 1000)
    labels = axis.tickStrings(levels[0][1], 1.0, 1.0)
    assert "20" in labels
    assert "100" in labels
    assert "1k" in labels
    assert "10k" in labels
    assert "20k" in labels
    # No SI-prefixed kHz-as-fraction labels.
    for label in labels:
        assert "." not in label, f"unexpected fractional label: {label}"
        assert "e" not in label.lower(), f"unexpected scientific label: {label}"
