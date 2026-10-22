"""Tests for live balance matching dialog and signal wiring."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.generators import LogSweepGenerator
from eq_helper.dsp.overlay import (
    load_frequency_response,
    load_measurement_csv,
    save_measurement_csv,
)
from eq_helper.dsp.sweep_measurement import measurement_from_sweep_recording
from eq_helper.ui.live_balance_dialog import LiveBalanceDialog
from eq_helper.ui.profile_panel import ProfilePanel


def _write_measurement(path: Path, peak_hz: float, peak_db: float) -> None:
    f = np.geomspace(20, 20000, 600)
    lf = np.log10(f)
    db = 85.0 + peak_db * np.exp(-((lf - np.log10(peak_hz)) ** 2) / (2 * 0.05 ** 2))
    lines = ["Frequency (Hz),Raw (dB)"]
    lines += [f"{fr:.3f},{d:.4f}" for fr, d in zip(f, db)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_profile_panel_live_match_signal(qtbot, tmp_path):
    panel = ProfilePanel(get_override=lambda: str(tmp_path))
    qtbot.addWidget(panel)
    called = []

    panel.liveMatchRequested.connect(lambda: called.append(True))
    panel.btn_live_match.click()

    assert called == [True]


def test_live_balance_dialog_scores_same_frequency(qtbot, tmp_path):
    ref_path = tmp_path / "ref.csv"
    _write_measurement(ref_path, 10000.0, 12.0)

    engine = AudioEngine()
    dlg = LiveBalanceDialog(engine)
    qtbot.addWidget(dlg)
    dlg.load_reference_measurement(str(ref_path))

    assert load_measurement_csv(str(ref_path))[0].size > 0

    t = np.arange(2048, dtype=np.float32) / 48000.0
    mic = np.sin(2.0 * np.pi * 10000.0 * t).astype(np.float32)
    engine.simulate_mic_input(mic)

    dlg._update_match_score()
    score_text = dlg._score_label.text()
    assert score_text.startswith("Score:")
    score = float(score_text.split(": ")[1])
    assert score > 0.9


def test_live_balance_dialog_saves_best_measurement(qtbot, tmp_path):
    ref_path = tmp_path / "ref.csv"
    _write_measurement(ref_path, 10000.0, 12.0)

    engine = AudioEngine()
    dlg = LiveBalanceDialog(engine)
    qtbot.addWidget(dlg)
    dlg.load_reference_measurement(str(ref_path))

    t = np.arange(2048, dtype=np.float32) / 48000.0
    mic = np.sin(2.0 * np.pi * 10000.0 * t).astype(np.float32)
    engine.simulate_mic_input(mic)

    dlg._update_match_score()
    assert dlg._best_score > 0.0
    assert dlg._best_path is not None
    assert Path(dlg._best_path).exists()
    assert Path(dlg._best_path).suffix == ".txt"


def test_live_balance_dialog_enables_mic_monitoring(qtbot, tmp_path):
    ref_path = tmp_path / "ref.csv"
    _write_measurement(ref_path, 10000.0, 12.0)

    engine = AudioEngine()
    assert not engine.state.snapshot().mic_monitoring
    dlg = LiveBalanceDialog(engine)
    qtbot.addWidget(dlg)
    dlg.load_reference_measurement(str(ref_path))

    assert engine.state.snapshot().mic_monitoring is True


def test_save_measurement_txt_roundtrips(tmp_path):
    path = tmp_path / "out.txt"
    f = np.geomspace(20.0, 20000.0, 10)
    db = np.linspace(-5.0, 5.0, len(f))
    save_measurement_csv(path, f, db)
    f2, db2 = load_frequency_response(path)
    assert np.allclose(f2, f, atol=1e-3)
    assert np.allclose(db2, db, atol=1e-2)


def test_save_measurement_csv_roundtrips(tmp_path):
    path = tmp_path / "out.csv"
    f = np.geomspace(20.0, 20000.0, 10)
    db = np.linspace(-5.0, 5.0, len(f))
    save_measurement_csv(path, f, db)
    f2, db2 = load_measurement_csv(path)
    assert np.allclose(f2, f)
    assert np.allclose(db2, db)


def test_sweep_measurement_preserves_peak_frequency() -> None:
    sr = 48000
    f_start = 20.0
    f_end = 20000.0
    duration = 8.0

    sweep = LogSweepGenerator(f_start, f_end, duration, sr, loop=False)
    n = int(sr * duration)
    out = np.empty(n, dtype=np.float64)
    written = 0
    while written < n:
        frames = min(2048, n - written)
        out[written : written + frames] = sweep.next_block(frames).astype(np.float64)
        written += frames

    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    mag = np.ones_like(freqs, dtype=np.float64)
    mag[(freqs > 7000) & (freqs < 9000)] *= 2.0
    ir = np.fft.irfft(mag)
    response = np.convolve(out, ir, mode="full")[:n] + 0.001 * np.random.randn(n)

    measured_freqs, _ = measurement_from_sweep_recording(response, sr, f_start, f_end, duration)
    idx = int(np.argmin(np.abs(measured_freqs - 8000.0)))

    assert measured_freqs[idx] == pytest.approx(8000.0, rel=0.05)
