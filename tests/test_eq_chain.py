"""Tests for EQ chain: SOS building, persistence, and engine integration."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eq_helper.audio.engine import AudioEngine
from eq_helper.dsp.eq_chain import (
    EQFilter,
    build_chain_sos,
    compute_chain_response,
    export_to_apo,
    load_filters,
    read_apo_file_full,
    save_filters,
)

SR = 48000
FREQS = np.geomspace(20.0, 20000.0, 500)


def test_read_apo_file_full_round_trips_preamp(tmp_path: Path) -> None:
    f = tmp_path / "eq.txt"
    f.write_text(
        "Preamp: -6.5 dB\nFilter 1: ON PK Fc 100 Hz Gain 3 dB Q 1\n", encoding="utf-8"
    )
    filters, geqs, preamp = read_apo_file_full(f)
    assert preamp == pytest.approx(-6.5)
    assert len(filters) == 1 and filters[0].fc_hz == 100.0
    assert geqs == []


def test_read_apo_file_full_defaults_preamp_zero(tmp_path: Path) -> None:
    f = tmp_path / "eq.txt"
    f.write_text("Filter 1: ON PK Fc 100 Hz Gain 3 dB Q 1\n", encoding="utf-8")
    _filters, _geqs, preamp = read_apo_file_full(f)
    assert preamp == 0.0


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


# -------------------------------------------------------- SOS chain building


def test_build_chain_sos_empty() -> None:
    assert build_chain_sos([], SR) is None


def test_build_chain_sos_single() -> None:
    sos = build_chain_sos([EQFilter("PK", 1000, 3, 1.0)], SR)
    assert sos is not None
    assert sos.shape == (1, 6)


def test_build_chain_sos_multiple() -> None:
    filters = [
        EQFilter("PK", 500, 4, 2.0),
        EQFilter("LS", 200, -2, 0.7),
        EQFilter("HS", 8000, 3, 0.7),
    ]
    sos = build_chain_sos(filters, SR)
    assert sos is not None
    assert sos.shape == (3, 6)


def test_build_chain_sos_skips_disabled() -> None:
    filters = [
        EQFilter("PK", 1000, 3, 1.0, enabled=True),
        EQFilter("PK", 2000, 5, 1.0, enabled=False),
    ]
    sos = build_chain_sos(filters, SR)
    assert sos is not None
    assert sos.shape == (1, 6)


def test_compute_chain_response_shows_boost() -> None:
    filters = [EQFilter("PK", 1000, 6, 1.0)]
    resp = compute_chain_response(filters, FREQS, SR)
    idx_1k = int(np.argmin(np.abs(FREQS - 1000.0)))
    assert resp[idx_1k] == pytest.approx(6.0, abs=0.5)


# ----------------------------------------------------------- persistence


def test_save_and_load_filters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "eq_helper.dsp.eq_chain._eq_filters_dir", lambda: tmp_path
    )
    filters = [
        EQFilter("PK", 500, 3.0, 1.4),
        EQFilter("LS", 200, -2.0, 0.7, enabled=False),
    ]
    save_filters("Test Device", filters)
    loaded_filters, loaded_geqs = load_filters("Test Device")
    assert len(loaded_filters) == 2
    assert loaded_filters[0].filter_type == "PK"
    assert loaded_filters[0].fc_hz == 500.0
    assert loaded_filters[1].enabled is False
    assert loaded_geqs == []


def test_load_missing_device(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "eq_helper.dsp.eq_chain._eq_filters_dir", lambda: tmp_path
    )
    filters, geqs = load_filters("Nonexistent")
    assert filters == []
    assert geqs == []


# ----------------------------------------------------------- APO export


def test_export_to_apo_creates_file(tmp_path: Path) -> None:
    filters = [
        EQFilter("PK", 1000, 3.0, 1.4),
        EQFilter("HS", 8000, -2.0, 0.7),
        EQFilter("LP", 16000, 0.0, 0.707),
    ]
    path = export_to_apo(filters, tmp_path, "Test Device")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Preamp:" in text and "0" in text.splitlines()[0]
    assert "Filter 1: ON PK Fc 1000" in text
    assert "Gain 3.0 dB" in text
    assert "Filter 3: ON LP Fc 16000" in text
    # LP should have no Gain field.
    lines = text.splitlines()
    lp_line = [l for l in lines if "LP" in l][0]
    assert "Gain" not in lp_line


# -------------------------------------------------------- engine integration


def test_engine_eq_chain_applies_boost() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=4096)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    # Get baseline RMS without EQ.
    base = engine.process_block(4096)
    base_rms = _rms(base[:, 0])

    # Apply a +6 dB boost at 1 kHz.
    sos = build_chain_sos([EQFilter("PK", 1000, 6, 1.0)], SR)
    engine.set_eq_chain(sos, enabled=True)
    boosted = engine.process_block(4096)
    boosted_rms = _rms(boosted[:, 0])

    # Boosted should be louder (6 dB ≈ 2x amplitude).
    assert boosted_rms > base_rms * 1.5


def test_engine_eq_chain_disabled_is_transparent() -> None:
    engine = AudioEngine(sample_rate=SR, block_size=4096)
    engine.start_sine(freq=1000.0)
    engine.set_volume(0.5)
    sos = build_chain_sos([EQFilter("PK", 1000, 6, 1.0)], SR)
    engine.set_eq_chain(sos, enabled=False)
    out = engine.process_block(4096)
    # With EQ disabled, output should be same as without EQ.
    engine2 = AudioEngine(sample_rate=SR, block_size=4096)
    engine2.start_sine(freq=1000.0)
    engine2.set_volume(0.5)
    ref = engine2.process_block(4096)
    assert abs(_rms(out[:, 0]) - _rms(ref[:, 0])) < 0.01
