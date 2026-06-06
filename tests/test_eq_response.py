"""Tests for APO parametric EQ frequency response computation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eq_helper.dsp.eq_response import compute_eq_response, load_profile_eq_response

FREQS = np.geomspace(20.0, 20000.0, 500)


def test_flat_with_no_filters() -> None:
    response = compute_eq_response("", FREQS)
    np.testing.assert_allclose(response, 0.0)


def test_preamp_only() -> None:
    response = compute_eq_response("Preamp: -6.5 dB\n", FREQS)
    np.testing.assert_allclose(response, -6.5)


def test_peaking_filter_boosts_at_center() -> None:
    text = "Filter 1: ON PK Fc 1000 Hz Gain 6 dB Q 1.0\n"
    response = compute_eq_response(text, FREQS)
    # At 1 kHz the response should be close to +6 dB.
    idx_1k = int(np.argmin(np.abs(FREQS - 1000.0)))
    assert response[idx_1k] == pytest.approx(6.0, abs=0.5)
    # Far away (e.g. 50 Hz) should be near 0 dB.
    idx_50 = int(np.argmin(np.abs(FREQS - 50.0)))
    assert abs(response[idx_50]) < 1.0


def test_low_shelf_boosts_low_end() -> None:
    text = "Filter 1: ON LS Fc 200 Hz Gain 5 dB Q 0.7\n"
    response = compute_eq_response(text, FREQS)
    idx_50 = int(np.argmin(np.abs(FREQS - 50.0)))
    idx_10k = int(np.argmin(np.abs(FREQS - 10000.0)))
    # Low frequencies boosted, high frequencies nearly flat.
    assert response[idx_50] > 3.0
    assert abs(response[idx_10k]) < 1.0


def test_high_shelf_boosts_high_end() -> None:
    text = "Filter 1: ON HS Fc 4000 Hz Gain 4 dB Q 0.7\n"
    response = compute_eq_response(text, FREQS)
    idx_50 = int(np.argmin(np.abs(FREQS - 50.0)))
    idx_15k = int(np.argmin(np.abs(FREQS - 15000.0)))
    assert abs(response[idx_50]) < 1.0
    assert response[idx_15k] > 2.5


def test_off_filters_are_skipped() -> None:
    text = "Filter 1: OFF PK Fc 1000 Hz Gain 10 dB Q 1.0\n"
    response = compute_eq_response(text, FREQS)
    np.testing.assert_allclose(response, 0.0, atol=1e-10)


def test_multiple_filters_sum() -> None:
    text = (
        "Preamp: -3 dB\n"
        "Filter 1: ON PK Fc 500 Hz Gain 4 dB Q 2.0\n"
        "Filter 2: ON PK Fc 3000 Hz Gain -2 dB Q 1.5\n"
    )
    response = compute_eq_response(text, FREQS)
    idx_500 = int(np.argmin(np.abs(FREQS - 500.0)))
    idx_3k = int(np.argmin(np.abs(FREQS - 3000.0)))
    # At 500 Hz: preamp(-3) + peak(+4) ≈ +1
    assert response[idx_500] == pytest.approx(1.0, abs=1.0)
    # At 3 kHz: preamp(-3) + peak(-2) ≈ -5
    assert response[idx_3k] == pytest.approx(-5.0, abs=1.0)


def test_load_profile_eq_response_from_file(tmp_path: Path) -> None:
    profile = tmp_path / "test_profile.txt"
    profile.write_text(
        "Preamp: -2 dB\n"
        "Filter 1: ON PK Fc 1000 Hz Gain 5 dB Q 1.0\n",
        encoding="utf-8",
    )
    response = load_profile_eq_response(profile, FREQS)
    assert response is not None
    idx_1k = int(np.argmin(np.abs(FREQS - 1000.0)))
    assert response[idx_1k] == pytest.approx(3.0, abs=0.5)  # -2 + 5


def test_load_profile_missing_file(tmp_path: Path) -> None:
    result = load_profile_eq_response(tmp_path / "nope.txt", FREQS)
    assert result is None


# ----------------------------------------------------------------- recursive


def test_recursive_include_sums_filters(tmp_path: Path) -> None:
    """A profile that includes another file should sum both files' filters."""
    # child.txt: +6 dB peak at 1 kHz
    child = tmp_path / "child.txt"
    child.write_text(
        "Filter 1: ON PK Fc 1000 Hz Gain 6 dB Q 1.0\n",
        encoding="utf-8",
    )
    # parent.txt: -3 dB preamp + includes child
    parent = tmp_path / "parent.txt"
    parent.write_text(
        "Preamp: -3 dB\n"
        "Include: child.txt\n",
        encoding="utf-8",
    )
    response = load_profile_eq_response(parent, FREQS)
    assert response is not None
    idx_1k = int(np.argmin(np.abs(FREQS - 1000.0)))
    # At 1 kHz: preamp(-3) + peak(+6) = +3
    assert response[idx_1k] == pytest.approx(3.0, abs=0.5)


def test_deeply_nested_includes(tmp_path: Path) -> None:
    """Three levels deep: grandchild -> child -> parent."""
    grandchild = tmp_path / "grandchild.txt"
    grandchild.write_text(
        "Filter 1: ON PK Fc 5000 Hz Gain 4 dB Q 2.0\n",
        encoding="utf-8",
    )
    child = tmp_path / "child.txt"
    child.write_text(
        "Filter 1: ON PK Fc 200 Hz Gain 3 dB Q 1.0\n"
        "Include: grandchild.txt\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.txt"
    parent.write_text(
        "Preamp: -2 dB\n"
        "Include: child.txt\n",
        encoding="utf-8",
    )
    response = load_profile_eq_response(parent, FREQS)
    assert response is not None
    idx_200 = int(np.argmin(np.abs(FREQS - 200.0)))
    idx_5k = int(np.argmin(np.abs(FREQS - 5000.0)))
    # 200 Hz: preamp(-2) + child peak(+3) = +1
    assert response[idx_200] == pytest.approx(1.0, abs=0.8)
    # 5 kHz: preamp(-2) + grandchild peak(+4) = +2
    assert response[idx_5k] == pytest.approx(2.0, abs=0.8)


def test_absolute_path_include(tmp_path: Path) -> None:
    """Include with an absolute path should resolve correctly."""
    sub = tmp_path / "subdir"
    sub.mkdir()
    child = sub / "filters.txt"
    child.write_text(
        "Filter 1: ON PK Fc 3000 Hz Gain 5 dB Q 1.5\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.txt"
    parent.write_text(
        f"Include: {child}\n",
        encoding="utf-8",
    )
    response = load_profile_eq_response(parent, FREQS)
    assert response is not None
    idx_3k = int(np.argmin(np.abs(FREQS - 3000.0)))
    assert response[idx_3k] == pytest.approx(5.0, abs=0.5)


def test_cycle_detection_does_not_loop(tmp_path: Path) -> None:
    """Two files that include each other should not cause infinite recursion."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text(
        "Filter 1: ON PK Fc 1000 Hz Gain 2 dB Q 1.0\n"
        "Include: b.txt\n",
        encoding="utf-8",
    )
    b.write_text(
        "Filter 1: ON PK Fc 2000 Hz Gain 3 dB Q 1.0\n"
        "Include: a.txt\n",
        encoding="utf-8",
    )
    # Should not hang or crash.
    response = load_profile_eq_response(a, FREQS)
    assert response is not None
    # Both filters should be present exactly once.
    idx_1k = int(np.argmin(np.abs(FREQS - 1000.0)))
    idx_2k = int(np.argmin(np.abs(FREQS - 2000.0)))
    assert response[idx_1k] > 1.0
    assert response[idx_2k] > 1.0


def test_commented_include_is_skipped(tmp_path: Path) -> None:
    """Commented-out Include lines should not be followed."""
    child = tmp_path / "child.txt"
    child.write_text(
        "Filter 1: ON PK Fc 1000 Hz Gain 10 dB Q 1.0\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.txt"
    parent.write_text(
        "# Include: child.txt\n",
        encoding="utf-8",
    )
    response = load_profile_eq_response(parent, FREQS)
    assert response is not None
    np.testing.assert_allclose(response, 0.0, atol=1e-10)


def test_missing_included_file_is_skipped(tmp_path: Path) -> None:
    """An Include pointing to a nonexistent file should be silently skipped."""
    parent = tmp_path / "parent.txt"
    parent.write_text(
        "Preamp: -1 dB\n"
        "Include: does_not_exist.txt\n"
        "Filter 1: ON PK Fc 1000 Hz Gain 3 dB Q 1.0\n",
        encoding="utf-8",
    )
    response = load_profile_eq_response(parent, FREQS)
    assert response is not None
    idx_1k = int(np.argmin(np.abs(FREQS - 1000.0)))
    # preamp(-1) + peak(+3) = +2, missing include silently skipped
    assert response[idx_1k] == pytest.approx(2.0, abs=0.5)
