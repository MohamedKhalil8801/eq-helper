"""Offscreen tests for the BalanceEqDialog and ProfilePanel balance action."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialogButtonBox

from eq_helper.apo.presets import PresetLibrary
from eq_helper.apo.writer import atomic_write_with_backup
from eq_helper.ui.balance_eq_dialog import BalanceEqDialog
from eq_helper.ui.profile_panel import ProfilePanel

DEV = "Device: HD600 {0123abcd-0000-0000-0000-000000000000}"


def test_dialog_ok_disabled_until_both_files(qtbot):
    dlg = BalanceEqDialog()
    qtbot.addWidget(dlg)
    ok = dlg._buttons.button(QDialogButtonBox.Ok)
    assert not ok.isEnabled()
    dlg.set_left_path("L.txt")
    assert not ok.isEnabled()
    dlg.set_right_path("R.txt")
    assert ok.isEnabled()
    assert dlg.left_path() == "L.txt"
    assert dlg.right_path() == "R.txt"
    assert dlg.reference() == "L"  # default
    dlg.ref_right.setChecked(True)
    assert dlg.reference() == "R"


def _write_measurement(path: Path, peak_hz: float, peak_db: float) -> None:
    f = np.geomspace(20, 20000, 600)
    lf = np.log10(f)
    db = 85.0 + peak_db * np.exp(-((lf - np.log10(peak_hz)) ** 2) / (2 * 0.05 ** 2))
    lines = ["Frequency (Hz),Raw (dB)"]
    lines += [f"{fr:.3f},{d:.4f}" for fr, d in zip(f, db)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _panel(tmp_path: Path, qtbot) -> ProfilePanel:
    (tmp_path / "config.txt").write_text(f"{DEV}\n", encoding="utf-8")
    lib = PresetLibrary(tmp_path)
    preset = lib.create_preset("HD600", "Base")
    eq = lib.create_eq_file(preset, "filters")
    atomic_write_with_backup(Path(eq.path), "Preamp: 0 dB\n")
    lib.activate(preset)
    panel = ProfilePanel(get_override=lambda: str(tmp_path))
    qtbot.addWidget(panel)
    panel.set_sample_rate(48000)
    panel.apply_settings({})
    return panel


def test_do_balance_adds_channel_scoped_eq(tmp_path, qtbot):
    left = tmp_path / "L.csv"
    right = tmp_path / "R.csv"
    _write_measurement(left, 3000, 4.0)   # L has a 3 kHz peak
    _write_measurement(right, 6000, -3.0)  # R has a 6 kHz dip
    panel = _panel(tmp_path, qtbot)

    eq, max_dev, other = panel._do_balance(str(left), str(right), "L")
    assert other == "R"
    assert max_dev < 0.1

    content = Path(eq.path).read_text(encoding="utf-8")
    assert "Channel: R" in content
    assert "GraphicEQ:" in content
    assert content.rstrip().endswith("Channel: all")

    lib = PresetLibrary(tmp_path)
    preset = lib.active_preset("HD600")
    names = [e.name for e in lib.list_eq_files(preset)]
    assert any("balance R to L" in n for n in names)


def test_do_balance_without_active_preset_raises(tmp_path, qtbot):
    (tmp_path / "config.txt").write_text(f"{DEV}\n", encoding="utf-8")
    panel = ProfilePanel(get_override=lambda: str(tmp_path))
    qtbot.addWidget(panel)
    panel.apply_settings({})
    with pytest.raises(ValueError):
        panel._do_balance("L.csv", "R.csv", "L")
