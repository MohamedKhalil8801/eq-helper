"""Unit tests for focused-sweep band/duration math (Qt-free, pure)."""

from __future__ import annotations

import math

import pytest

from eq_helper.audio.sweep_math import focused_sweep_params


class TestFocusedSweepParams:
    def test_one_octave_is_geometrically_centred(self) -> None:
        f_start, f_end, _ = focused_sweep_params(1000.0, 1.0, 3.0)
        # ±half octave: f/√2 … f·√2
        assert f_start == pytest.approx(1000.0 / math.sqrt(2.0), rel=1e-6)
        assert f_end == pytest.approx(1000.0 * math.sqrt(2.0), rel=1e-6)
        # The centre is the geometric mean of the band ends.
        assert math.sqrt(f_start * f_end) == pytest.approx(1000.0, rel=1e-6)

    def test_half_octave_width(self) -> None:
        f_start, f_end, _ = focused_sweep_params(1000.0, 0.5, 3.0)
        assert f_start == pytest.approx(1000.0 * 2.0 ** -0.25, rel=1e-6)
        assert f_end == pytest.approx(1000.0 * 2.0 ** 0.25, rel=1e-6)

    def test_two_octave_width(self) -> None:
        f_start, f_end, _ = focused_sweep_params(1000.0, 2.0, 3.0)
        assert f_start == pytest.approx(500.0, rel=1e-6)
        assert f_end == pytest.approx(2000.0, rel=1e-6)

    def test_duration_is_width_times_rate(self) -> None:
        assert focused_sweep_params(1000.0, 1.0, 3.0)[2] == pytest.approx(3.0)
        assert focused_sweep_params(1000.0, 2.0, 3.0)[2] == pytest.approx(6.0)
        assert focused_sweep_params(1000.0, 0.5, 3.0)[2] == pytest.approx(1.5)

    def test_band_clamped_at_low_extreme(self) -> None:
        f_start, f_end, _ = focused_sweep_params(25.0, 2.0, 3.0)
        assert f_start == pytest.approx(20.0)        # 12.5 -> clamped to 20
        assert f_end == pytest.approx(50.0, rel=1e-6)

    def test_band_clamped_at_high_extreme(self) -> None:
        f_start, f_end, _ = focused_sweep_params(15000.0, 2.0, 3.0)
        assert f_start == pytest.approx(7500.0, rel=1e-6)
        assert f_end == pytest.approx(20000.0)       # 30000 -> clamped to 20k

    def test_band_is_always_increasing_and_positive(self) -> None:
        for center in (20.0, 20000.0, 100.0, 10000.0):
            f_start, f_end, _ = focused_sweep_params(center, 1.0, 3.0)
            assert 0 < f_start < f_end
