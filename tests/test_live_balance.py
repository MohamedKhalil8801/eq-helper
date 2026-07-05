"""Tests for live balance matching dialog and signal wiring."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from eq_helper.audio.engine import AudioEngine
from eq_helper.dsp.overlay import load_measurement_csv
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
