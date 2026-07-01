"""Offscreen UI tests: batch dialog, batch generation, preset reorder, cleanup."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

_CAND = re.compile(  # batch candidate name shape (anchored)
    r"^[LR]\d+(?:!!!)? [LR]\d+(?:!!!)? \(\d\.\d\d\)(?: \(\d+\))?$"
)

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from eq_helper.apo.presets import PresetLibrary
from eq_helper.ui.balance_batch_dialog import BalanceBatchDialog
from eq_helper.ui.profile_panel import ProfilePanel

DEV = "Device: HD600 {0123abcd-0000-0000-0000-000000000000}"


def _write_meas(path: Path, peak_hz: float, peak_db: float) -> None:
    f = np.geomspace(20, 20000, 400)
    lf = np.log10(f)
    db = 85 + peak_db * np.exp(-((lf - np.log10(peak_hz)) ** 2) / (2 * 0.05 ** 2))
    path.write_text(
        "Frequency (Hz),Raw (dB)\n"
        + "\n".join(f"{a:.3f},{b:.4f}" for a, b in zip(f, db)),
        encoding="utf-8",
    )


def _measurements(folder: Path, n_left: int, n_right: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n_left + 1):
        _write_meas(folder / f"L {i}.txt", 1000 + 150 * i, 3.0)
    for j in range(1, n_right + 1):
        _write_meas(folder / f"R {j}.txt", 5000 + 200 * j, -2.0)


def _panel(tmp_path: Path, qtbot) -> ProfilePanel:
    (tmp_path / "config.txt").write_text(f"{DEV}\n", encoding="utf-8")
    lib = PresetLibrary(tmp_path)
    lib.create_preset("HD600", "Base")  # a device + a preset must exist
    panel = ProfilePanel(get_override=lambda: str(tmp_path))
    qtbot.addWidget(panel)
    panel.set_sample_rate(48000)
    panel.apply_settings({})
    return panel


def test_candidate_regex_rejects_normal_presets():
    from eq_helper.ui.profile_panel import _CANDIDATE_RE
    for good in ("R1 L1 (0.94)", "R1!!! L1 (0.95)", "L2 R3!!! (0.00)",
                 "R1 L1 (0.94) (2)", "R10 L2 (1.00)"):
        assert _CANDIDATE_RE.search(good), good
    for bad in ("Harman (1.00)", "JM-1 (0.71)", "Base", "Base (optimized)",
                "My EQ (v0.95)", "L1 (0.9)"):
        assert not _CANDIDATE_RE.search(bad), bad


def test_batch_dialog_counts_and_ok(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 3, 2)
    dlg = BalanceBatchDialog()
    qtbot.addWidget(dlg)
    assert not dlg._buttons.button(QDialogButtonBox.Ok).isEnabled()
    dlg.set_folder(str(folder))
    assert dlg._buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert "3 Left" in dlg._found_label.text()
    assert "2 Right" in dlg._found_label.text()
    assert dlg.reference() == "L"


def test_do_balance_batch_creates_presets_and_activates_first(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 3, 3)
    panel = _panel(tmp_path, qtbot)

    created, worst, skipped = panel._do_balance_batch(str(folder), "L", False)
    assert len(created) == 9
    assert skipped == 0

    lib = PresetLibrary(tmp_path)
    presets = lib.list_presets("HD600")
    bal = [p for p in presets if _CAND.search(p.name)]
    assert len(bal) == 9
    # first candidate is the active one
    active = lib.active_preset("HD600")
    assert active is not None and _CAND.search(active.name)
    # each candidate's EQ file is channel-scoped to R (reference L, no invert)
    content = Path(lib.list_eq_files(bal[0])[0].path).read_text(encoding="utf-8")
    assert "Channel: R" in content and content.rstrip().endswith("Channel: all")


def test_move_preset_reorders(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 2, 1)
    panel = _panel(tmp_path, qtbot)
    panel._do_balance_batch(str(folder), "L", False)  # 2 candidates
    panel._refresh_presets()
    # select the last preset and move it up
    last = panel._presets.count() - 1
    panel._presets.setCurrentRow(last)
    names = lambda: [
        panel._presets.item(i).data(Qt.UserRole).name
        for i in range(panel._presets.count())
    ]
    before = names()
    panel._move_preset(-1)
    after = names()
    assert before[last] == after[last - 1]  # moved up one


def test_remove_other_candidates_keeps_active(tmp_path, qtbot, monkeypatch):
    folder = tmp_path / "meas"
    _measurements(folder, 3, 1)  # 3 candidates
    panel = _panel(tmp_path, qtbot)
    panel._do_balance_batch(str(folder), "L", False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    panel._on_remove_other_candidates()

    lib = PresetLibrary(tmp_path)
    presets = lib.list_presets("HD600")
    bal = [p for p in presets if _CAND.search(p.name)]
    assert len(bal) == 1  # only the active candidate remains
    assert bal[0].is_active


def test_remove_other_candidates_never_deletes_all(tmp_path, qtbot, monkeypatch):
    # Footgun guard: even when a NON-candidate preset is active, one candidate
    # must be kept (the selected one), never all removed.
    folder = tmp_path / "meas"
    _measurements(folder, 3, 1)  # 3 candidates
    panel = _panel(tmp_path, qtbot)
    panel._do_balance_batch(str(folder), "L", False)
    lib = PresetLibrary(tmp_path)
    base = next(p for p in lib.list_presets("HD600") if p.name == "Base")
    lib.activate(base)  # active preset is NOT a candidate
    panel._refresh_presets()
    # select the first candidate in the list
    for i in range(panel._presets.count()):
        if _CAND.search(panel._presets.item(i).data(Qt.UserRole).name):
            panel._presets.setCurrentRow(i)
            break
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    panel._on_remove_other_candidates()

    bal = [p for p in lib.list_presets("HD600") if _CAND.search(p.name)]
    assert len(bal) == 1  # exactly one kept, not zero
