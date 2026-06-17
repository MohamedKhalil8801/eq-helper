"""Tests for the oratory1990-style CSV overlay loader."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eq_helper.dsp.overlay import (
    OverlayLoadError,
    load_frequency_response,
    load_measurement_csv,
    normalize_at_1khz,
)


def _write_oratory_csv(path: Path) -> None:
    # 5 rows: 100 Hz, 500 Hz, 1 kHz, 2 kHz, 10 kHz — with a header row.
    path.write_text(
        "Frequency (Hz),Raw (dB)\n"
        "100,85.0\n"
        "500,86.0\n"
        "1000,88.0\n"
        "2000,89.0\n"
        "10000,85.0\n",
        encoding="utf-8",
    )


def test_load_measurement_csv_parses_header(tmp_path: Path) -> None:
    csv = tmp_path / "hp.csv"
    _write_oratory_csv(csv)
    freqs, db = load_measurement_csv(csv)
    assert freqs.shape == (5,)
    assert db.shape == (5,)
    np.testing.assert_allclose(freqs, [100, 500, 1000, 2000, 10000])
    np.testing.assert_allclose(db, [85.0, 86.0, 88.0, 89.0, 85.0])


def test_load_measurement_csv_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OverlayLoadError):
        load_measurement_csv(tmp_path / "nope.csv")


def test_normalize_at_1khz_zeros_the_reference(tmp_path: Path) -> None:
    csv = tmp_path / "hp.csv"
    _write_oratory_csv(csv)
    freqs, db = load_measurement_csv(csv)
    out = normalize_at_1khz(freqs, db)
    # At 1 kHz the normalised value should be zero.
    idx_1k = int(np.argmin(np.abs(freqs - 1000.0)))
    assert abs(out[idx_1k]) < 1e-9
    # Relative shape preserved.
    assert out[idx_1k] - out[0] == pytest.approx(db[idx_1k] - db[0])


def test_load_measurement_csv_no_header(tmp_path: Path) -> None:
    csv = tmp_path / "plain.csv"
    csv.write_text("100,85\n1000,90\n10000,80\n", encoding="utf-8")
    freqs, db = load_measurement_csv(csv)
    assert freqs.shape == (3,)
    np.testing.assert_allclose(freqs, [100, 1000, 10000])


def test_load_frequency_response_rew_text(tmp_path: Path) -> None:
    p = tmp_path / "rew.txt"
    p.write_text(
        "* Measurement data measured by REW V5\n"
        "* Freq(Hz) SPL(dB) Phase(degrees)\n"
        "20.000\t85.10\t-12.3\n"
        "1000.0\t88.00\t0.0\n"
        "10000\t84.5\t44.1\n",
        encoding="utf-8",
    )
    f, db = load_frequency_response(p)
    np.testing.assert_allclose(f, [20.0, 1000.0, 10000.0])
    np.testing.assert_allclose(db, [85.10, 88.00, 84.5])


def test_load_frequency_response_csv_still_works(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_text("Frequency (Hz),Raw (dB)\n100,85\n1000,90\n", encoding="utf-8")
    f, db = load_frequency_response(p)
    np.testing.assert_allclose(f, [100.0, 1000.0])
    np.testing.assert_allclose(db, [85.0, 90.0])


def test_load_frequency_response_space_delimited_no_comments(tmp_path: Path) -> None:
    p = tmp_path / "plain.txt"
    p.write_text("100 85\n1000 90\n10000 80\n", encoding="utf-8")
    f, db = load_frequency_response(p)
    np.testing.assert_allclose(f, [100.0, 1000.0, 10000.0])
    np.testing.assert_allclose(db, [85.0, 90.0, 80.0])


def test_load_frequency_response_empty_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.txt"
    p.write_text("* only comments\n* nothing else\n", encoding="utf-8")
    with pytest.raises(OverlayLoadError):
        load_frequency_response(p)


def test_load_frequency_response_dedupes_frequencies(tmp_path: Path) -> None:
    # Duplicate frequencies (some exporters repeat) must be collapsed so
    # downstream np.interp sees a strictly-increasing axis.
    p = tmp_path / "dup.txt"
    p.write_text("100 85\n100 86\n1000 90\n1000 91\n", encoding="utf-8")
    f, db = load_frequency_response(p)
    np.testing.assert_allclose(f, [100.0, 1000.0])
    assert np.all(np.diff(f) > 0)
