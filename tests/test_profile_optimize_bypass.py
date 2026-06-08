"""Offscreen UI tests for the ProfilePanel Optimize and Bypass-band actions.

These exercise the dialog-free core (`_do_optimize` / `_do_bypass`) so no modal
QMessageBox blocks the run.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from eq_helper.apo.presets import PresetLibrary
from eq_helper.apo.writer import atomic_write_with_backup
from eq_helper.dsp.eq_response import load_profile_eq_response
from eq_helper.ui.profile_panel import ProfilePanel

DEV = "Device: HD600 {0123abcd-0000-0000-0000-000000000000}"

_FILTERS = (
    "Preamp: -5 dB\n"
    "Filter 1: ON PK Fc 120 Hz Gain 5 dB Q 1.0\n"
    "Filter 2: ON PK Fc 1000 Hz Gain -4 dB Q 2.0\n"
    "Filter 3: ON PK Fc 5000 Hz Gain 8 dB Q 1.5\n"
    "Filter 4: ON HS Fc 9000 Hz Gain 3 dB Q 0.7\n"
)


def _setup_active_preset(tmp_path: Path) -> PresetLibrary:
    (tmp_path / "config.txt").write_text(f"{DEV}\n", encoding="utf-8")
    lib = PresetLibrary(tmp_path)
    preset = lib.create_preset("HD600", "Base")
    eq = lib.create_eq_file(preset, "filters")
    atomic_write_with_backup(Path(eq.path), _FILTERS)
    lib.activate(preset)
    return lib


def _panel(tmp_path: Path, qtbot) -> ProfilePanel:
    panel = ProfilePanel(
        get_override=lambda: str(tmp_path),
        get_isolation_band=lambda: (5000.0, 3000.0),
    )
    qtbot.addWidget(panel)
    panel.set_sample_rate(48000)
    panel.apply_settings({})
    return panel


def test_optimize_creates_optimized_preset(tmp_path, qtbot):
    _setup_active_preset(tmp_path)
    panel = _panel(tmp_path, qtbot)

    new, n_points, max_dev = panel._do_optimize()

    assert new.name == "Base (optimized)"
    assert n_points >= 2
    assert max_dev < 0.1

    lib = PresetLibrary(tmp_path)
    names = [p.name for p in lib.list_presets("HD600")]
    assert "Base (optimized)" in names

    # The optimized preset holds exactly one GraphicEQ file.
    opt = next(p for p in lib.list_presets("HD600") if p.name == "Base (optimized)")
    eqs = lib.list_eq_files(opt)
    assert len(eqs) == 1
    content = Path(eqs[0].path).read_text(encoding="utf-8")
    assert content.lstrip().startswith("GraphicEQ:")

    # Its chord response reproduces the original combined response.
    freqs = np.geomspace(30, 16000, 600)
    orig = load_profile_eq_response(
        Path(lib.active_preset("HD600").path), freqs
    )
    optimized = load_profile_eq_response(Path(opt.path), freqs)
    assert np.max(np.abs(optimized - orig)) < 0.1


def test_optimize_without_active_preset_raises(tmp_path, qtbot):
    (tmp_path / "config.txt").write_text(f"{DEV}\n", encoding="utf-8")
    panel = _panel(tmp_path, qtbot)
    with pytest.raises(ValueError):
        panel._do_optimize()


def test_bypass_flattens_active_preset_band(tmp_path, qtbot):
    _setup_active_preset(tmp_path)
    panel = _panel(tmp_path, qtbot)

    eq = panel._do_bypass(4000.0, 7000.0)
    assert "bypass" in eq.name

    lib = PresetLibrary(tmp_path)
    preset = lib.active_preset("HD600")
    eqs = lib.list_eq_files(preset)
    assert len(eqs) == 2  # original filters + the appended bypass correction

    freqs = np.geomspace(20, 20000, 2000)
    # original = filters-only file (eqs[0]); net = whole stack incl. the bypass.
    orig = load_profile_eq_response(Path(eqs[0].path), freqs)
    net = load_profile_eq_response(Path(preset.path), freqs)

    inside = (freqs >= 4500) & (freqs <= 6500)
    assert np.max(np.abs(net[inside])) < 0.25  # flattened inside the band
    assert np.max(np.abs(orig[inside])) > 2.0  # original was clearly non-flat there

    # Far outside the band the correction is ~zero, so net == original.
    outside = (freqs < 3000) | (freqs > 9000)
    assert np.max(np.abs(net[outside] - orig[outside])) < 0.25
