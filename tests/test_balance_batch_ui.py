"""Offscreen UI tests: batch dialog, batch generation, preset reorder, cleanup."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from eq_helper.apo.presets import PresetLibrary
from eq_helper.dsp.balance_eq import detect_measurement_sets, find_best_balance_pairs
from eq_helper.ui.balance_batch_dialog import BalanceBatchDialog
from eq_helper.ui.profile_panel import ProfilePanel

DEV = "Device: HD600 {0123abcd-0000-0000-0000-000000000000}"

_CAND = re.compile(  # batch candidate name shape (anchored)
    r"^[LR]\d+(?:!!!)? [LR]\d+(?:!!!)? \(\d\.\d\d\)(?: \(\d+\))?$"
)


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


def _do_batch_helper(panel, folder, reference, invert, method="reference"):
    """Replaces the removed _do_balance_batch: find best pairs and generate."""
    left, right = detect_measurement_sets(folder)
    matches, skipped = find_best_balance_pairs(
        left, right, reference, invert=invert, method=method
    )
    created: list = []
    worst = 0.0
    for index, (name, _score, l_f, l_db, r_f, r_db) in enumerate(matches):
        created_name, max_dev = panel._do_generate_balance_for_pair(
            name, l_f, l_db, r_f, r_db, reference, invert, method,
            activate=(index == 0),
        )
        created.append(created_name)
        worst = max(worst, max_dev)
    return created, worst, len(skipped)


def test_batch_dialog_method_disables_reference(tmp_path, qtbot):
    dlg = BalanceBatchDialog()
    qtbot.addWidget(dlg)
    assert dlg.method() == "reference"
    assert dlg.ref_left.isEnabled()
    dlg.method_midpoint.setChecked(True)
    assert dlg.method() == "midpoint"
    assert not dlg.ref_left.isEnabled()  # reference irrelevant for midpoint


def test_do_balance_batch_midpoint_two_blocks(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 2, 2)
    panel = _panel(tmp_path, qtbot)
    created, worst, skipped = _do_batch_helper(panel, folder, "L", False, "midpoint")
    # no detectable features -> all 4 pairings tie at 0.00 and are kept
    assert len(created) == 4
    lib = PresetLibrary(tmp_path)
    for cand in (p for p in lib.list_presets("HD600") if _CAND.search(p.name)):
        content = Path(lib.list_eq_files(cand)[0].path).read_text(encoding="utf-8")
        assert "Channel: L" in content and "Channel: R" in content
        assert content.rstrip().endswith("Channel: all")


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
    assert "Channel: R" in dlg.target_label.text()
    dlg.ref_right.setChecked(True)
    assert "Channel: L" in dlg.target_label.text()  # reference R -> correct L
    dlg.invert_check.setChecked(True)
    assert "Channel: R" in dlg.target_label.text()


def _preset_eq_text(tmp_path: Path, name: str) -> str:
    lib = PresetLibrary(tmp_path)
    preset = next(p for p in lib.list_presets("HD600") if p.name == name)
    return Path(lib.list_eq_files(preset)[0].path).read_text(encoding="utf-8")


def test_batch_reference_right_corrects_left_channel(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 1, 1)
    panel = _panel(tmp_path, qtbot)

    created, _worst, _skipped = _do_batch_helper(panel, folder, "R", False)
    content = _preset_eq_text(tmp_path, created[0])
    lines = content.splitlines()
    assert lines[0].startswith("# Balance: reference side R")
    assert "Channel: L" in lines
    assert "Channel: R" not in lines  # reference side must stay untouched
    assert lines[-1] == "Channel: all"

    created, _worst, _skipped = _do_batch_helper(panel, folder, "R", True)
    content = _preset_eq_text(tmp_path, created[0])
    assert "Channel: R" in content.splitlines()  # config swap -> opposite APO channel


def test_do_balance_batch_creates_tied_presets_and_activates_first(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 3, 3)
    panel = _panel(tmp_path, qtbot)

    created, worst, skipped = _do_batch_helper(panel, folder, "L", False)
    # synthetic measurements have no detectable features -> all 9 pairings tie
    # at 0.00; every tied pairing is kept instead of one alphabetic pick
    assert len(created) == 9
    assert all(re.search(r"\(\d\.\d\d\)$", name) for name in created)
    assert skipped == 0

    lib = PresetLibrary(tmp_path)
    presets = lib.list_presets("HD600")
    bal = [p for p in presets if _CAND.search(p.name)]
    assert len(bal) == 9
    active = lib.active_preset("HD600")
    assert active is not None and active.name == created[0]
    content = Path(lib.list_eq_files(bal[0])[0].path).read_text(encoding="utf-8")
    assert "Channel: R" in content and content.rstrip().endswith("Channel: all")


def test_move_preset_reorders(tmp_path, qtbot):
    folder = tmp_path / "meas"
    _measurements(folder, 2, 1)
    panel = _panel(tmp_path, qtbot)
    _do_batch_helper(panel, folder, "L", False)
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


def test_balance_buttons_share_row_and_remove_candidates_is_gone(tmp_path, qtbot):
    panel = _panel(tmp_path, qtbot)
    assert not hasattr(panel, "btn_remove_others")
    assert panel.btn_balance.parentWidget() is panel
    assert panel.btn_balance_batch.parentWidget() is panel
