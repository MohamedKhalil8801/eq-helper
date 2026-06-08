"""Qt smoke test: the main window boots and its widgets wire up.

Uses ``pytest-qt`` with the offscreen Qt platform (set in ``conftest.py``) so
this runs without a display. It intentionally does NOT start real audio —
``AudioEngine.start_stream`` is a no-op when PortAudio isn't available, but
even when it is, we make sure the engine is left in IDLE mode.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")


def test_main_window_boots_and_has_panels(qtbot) -> None:
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    # Engine should exist and be idle.
    assert window.engine is not None
    from eq_helper.audio.shared_state import Mode

    assert window.engine.state.snapshot().mode == Mode.IDLE

    # Panels present.
    assert window.audio_panel is not None
    assert window.band_panel is not None
    assert window.profile_panel is not None
    assert window.spectrum_view is not None

    # Unified view: no listening/editor mode toggle; EQ Filters + Profiles are
    # merged into the drill-down library (browser shown first, editor on drill);
    # Test Songs is behind a button-launched dialog.
    assert not hasattr(window, "_mode_combo")
    assert window.eq_panel is not None
    assert window.eq_library.current_is_browser() is True
    assert window.graphic_eq_panel.isVisibleTo(window)
    assert window.test_songs_button is not None
    assert not window.test_songs_dialog.isVisible()

    # Band region should mirror the band panel defaults.
    fc = window.band_panel.fc_spin.value()
    bw = window.band_panel.bw_spin.value()
    assert fc > 0
    assert bw > 0


def test_audio_panel_sine_button_sets_mode(qtbot) -> None:
    from eq_helper.audio.shared_state import Mode
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    window.audio_panel.btn_sine.click()
    assert window.engine.state.snapshot().mode == Mode.SINE

    # Pressing the same button again stops playback.
    window.audio_panel.btn_sine.click()
    assert window.engine.state.snapshot().mode == Mode.IDLE


def test_spectrum_eq_dots_render(qtbot) -> None:
    from eq_helper.dsp.eq_chain import EQFilter
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    sv = window.spectrum_view

    # One enabled filter -> one rendered dot.
    sv.update_eq_dots([EQFilter(fc_hz=1000.0, gain_db=3.0, q=1.0, enabled=True)])
    assert len(sv._eq_dots.data) == 1

    # Highlighting an existing dot must not raise and keeps the dot.
    sv._render_dots(highlight_index=0)
    assert len(sv._eq_dots.data) == 1

    # Disabled filters are not drawn.
    sv.update_eq_dots([EQFilter(fc_hz=1000.0, gain_db=3.0, q=1.0, enabled=False)])
    assert len(sv._eq_dots.data) == 0


def test_eq_filter_create_index_captured_before_emit(qtbot, tmp_path) -> None:
    # eqFilterCreated round-trips synchronously and appends to the dot cache,
    # so the drag index must be captured BEFORE emitting. Capturing after would
    # point one past the new dot and break click-create-then-drag. This guards
    # that invariant (a no-op-on-emit or async chain would make the fix wrong).
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    sv = window.spectrum_view
    # Creating filters requires an EQ file to be selected (the editor edits a
    # file); bind one so add_filter is active.
    eqfile = tmp_path / "eq.txt"
    eqfile.write_text("Preamp: 0 dB\n", encoding="utf-8")
    window.eq_panel.set_eq_file(str(eqfile))

    index_before = len(sv._eq_filters_cache)
    sv.eqFilterCreated.emit(1000.0, 3.0)
    count_after = len(sv._eq_filters_cache)

    assert count_after == index_before + 1      # exactly one filter added
    assert index_before == count_after - 1      # captured index points to it
    assert 0 <= index_before < count_after      # and is in range


def test_focus_sweep_button_starts_ping_pong_sweep(qtbot) -> None:
    from eq_helper.audio.generators import PingPongSweepGenerator
    from eq_helper.audio.shared_state import Mode
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    # The Focus Sweep source button exists alongside the others.
    assert window.audio_panel.btn_focus is not None

    # Triggering it (as Shift+click does) puts the engine into a ping-pong sweep.
    window.audio_panel.trigger_focus_sweep()
    assert window.engine.state.snapshot().mode == Mode.SWEEP
    assert isinstance(window.engine._sweep, PingPongSweepGenerator)

    window.audio_panel.stop_all()
    assert window.engine.state.snapshot().mode == Mode.IDLE


def test_shift_click_focus_sweep_centres_band_on_click(qtbot) -> None:
    import math

    from eq_helper.audio.shared_state import Mode
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    # Emulate Shift+click at 2 kHz on the spectrum.
    window.spectrum_view.focusSweepRequested.emit(2000.0)

    snap = window.engine.state.snapshot()
    assert snap.mode == Mode.SWEEP
    # The focused band straddles the clicked frequency (default 1 octave).
    assert snap.sweep_f_start < 2000.0 < snap.sweep_f_end
    assert math.sqrt(snap.sweep_f_start * snap.sweep_f_end) == pytest.approx(2000.0, rel=0.05)


def test_volume_memory_switches_between_grey_and_regular(qtbot) -> None:
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    ap = window.audio_panel

    # Known, distinct levels for the two contexts.
    ap._volume.regular = 0.1
    ap._volume.grey = 0.45

    # Play Grey -> engine uses the loud grey level.
    ap.btn_grey.click()
    assert window.engine.state.snapshot().amp == pytest.approx(0.45)

    # A user volume change while Grey is active updates only the grey bucket.
    ap._on_volume_changed(0.6)
    assert ap._volume.grey == pytest.approx(0.6)
    assert ap._volume.regular == pytest.approx(0.1)

    # Switch to Sine -> reverts to the regular level.
    ap.btn_sine.click()
    assert window.engine.state.snapshot().amp == pytest.approx(0.1)

    # Back to Grey -> the remembered (updated) loud level returns.
    ap.btn_grey.click()
    assert window.engine.state.snapshot().amp == pytest.approx(0.6)

    # Stopping reverts to the regular level.
    ap.stop_all()
    assert window.engine.state.snapshot().amp == pytest.approx(0.1)


def test_band_panel_toggle_updates_engine(qtbot) -> None:
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    window.band_panel.enable_check.setChecked(True)
    assert window.engine.state.snapshot().band_enabled is True
    window.band_panel.enable_check.setChecked(False)
    assert window.engine.state.snapshot().band_enabled is False


# ----------------------------------------------------- EQ editor (file-bound)


def test_eq_editor_loads_file_and_preserves_preamp(qtbot, tmp_path) -> None:
    from eq_helper.audio.engine import AudioEngine
    from eq_helper.dsp.eq_chain import read_apo_file_full
    from eq_helper.ui.eq_mode_panel import EQModePanel

    eqfile = tmp_path / "eq.txt"
    eqfile.write_text(
        "Preamp: -5 dB\n"
        "Filter 1: ON PK Fc 100 Hz Gain 3 dB Q 1\n"
        "Filter 2: ON PK Fc 2000 Hz Gain -2 dB Q 2\n",
        encoding="utf-8",
    )
    panel = EQModePanel(AudioEngine())
    qtbot.addWidget(panel)

    # Disabled until a file is bound.
    assert panel.btn_add.isEnabled() is False
    panel.set_eq_file(str(eqfile))
    assert panel.btn_add.isEnabled() is True
    assert len(panel.filters()) == 2

    # Edit the first filter's gain; flush the debounced save.
    panel._rows[0].gain_spin.setValue(6.0)
    panel._save_timer.stop()
    panel._auto_save()

    filters, _geqs, preamp = read_apo_file_full(eqfile)
    assert preamp == -5.0  # preamp survives the load -> edit -> save round-trip
    assert any(abs(f.gain_db - 6.0) < 1e-6 for f in filters)


def test_eq_editor_no_write_without_file(qtbot) -> None:
    from eq_helper.audio.engine import AudioEngine
    from eq_helper.ui.eq_mode_panel import EQModePanel

    panel = EQModePanel(AudioEngine())
    qtbot.addWidget(panel)
    assert panel.eq_file() is None
    panel._auto_save()  # must be a safe no-op with no file bound


# --------------------------------------------------- Profiles panel (presets)


def test_profile_panel_lists_presets_and_emits_edit(qtbot, tmp_path) -> None:
    from eq_helper.ui.profile_panel import ProfilePanel

    eq_dir = tmp_path / "eq-helper" / "HD600" / "eq"
    presets_dir = tmp_path / "eq-helper" / "HD600" / "presets"
    eq_dir.mkdir(parents=True)
    presets_dir.mkdir(parents=True)
    eqfile = eq_dir / "base.txt"
    eqfile.write_text("Preamp: 0 dB\n", encoding="utf-8")
    preset = presets_dir / "P.txt"
    preset.write_text(f"Include: {eqfile}\n", encoding="utf-8")
    (tmp_path / "config.txt").write_text(
        f"Device: HD600\nInclude: {preset}\n", encoding="utf-8"
    )

    panel = ProfilePanel(get_override=lambda: str(tmp_path))
    qtbot.addWidget(panel)
    panel.apply_settings({})

    assert panel.device_combo.count() == 1
    assert panel._presets.count() == 1
    assert panel._stack.count() == 1
    assert panel._stack.item(0).text() == "base"

    with qtbot.waitSignal(panel.editEqFileRequested, timeout=1000) as blocker:
        panel._on_stack_double_clicked(panel._stack.item(0))
    assert blocker.args[0].replace("\\", "/").endswith("eq/base.txt")


# ------------------------------------------------- collapsible section + library


def test_collapsible_section_toggles_content(qtbot) -> None:
    from PySide6.QtWidgets import QLabel
    from eq_helper.ui.widgets import CollapsibleSection

    content = QLabel("body")
    sec = CollapsibleSection("Title", content, expanded=True)
    qtbot.addWidget(sec)
    assert sec.is_expanded() is True
    assert content.isVisibleTo(sec)

    sec.set_expanded(False)
    assert sec.is_expanded() is False
    assert not content.isVisibleTo(sec)


def test_eq_library_panel_drilldown(qtbot, tmp_path) -> None:
    from eq_helper.audio.engine import AudioEngine
    from eq_helper.ui.eq_library_panel import EqLibraryPanel
    from eq_helper.ui.eq_mode_panel import EQModePanel
    from eq_helper.ui.profile_panel import ProfilePanel

    eqfile = tmp_path / "eq.txt"
    eqfile.write_text("Preamp: 0 dB\n", encoding="utf-8")
    profile = ProfilePanel(get_override=lambda: None)
    editor = EQModePanel(AudioEngine())
    lib = EqLibraryPanel(profile, editor)
    qtbot.addWidget(lib)

    # Starts on the browser (preset/file list).
    assert lib.current_is_browser() is True
    # Opening a file drills into the editor.
    profile.editEqFileRequested.emit(str(eqfile))
    assert lib.current_is_browser() is False
    # Back returns to the browser.
    lib.show_browser()
    assert lib.current_is_browser() is True


# ----------------------------------------------- graphic-EQ region audition


def test_graphic_eq_audition_controls(qtbot) -> None:
    from eq_helper.ui.graphic_eq_panel import GraphicEQPanel

    p = GraphicEQPanel()
    qtbot.addWidget(p)
    assert p.audition_enabled() is False
    assert p.audition_noise() == "pink"  # default
    p.set_audition_noise("grey")
    assert p.audition_noise() == "grey"
    p.set_audition_enabled(True)
    assert p.audition_enabled() is True


def test_audition_isolate_sets_band_and_plays_noise(qtbot) -> None:
    from eq_helper.audio.shared_state import Mode
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.audio_panel.stop_all()  # ensure idle so it auto-plays
    window.graphic_eq_panel.set_audition_noise("pink")

    window.graphic_eq_panel.auditionIsolateRequested.emit(100.0, 200.0)
    snap = window.engine.state.snapshot()
    assert snap.band_enabled is True
    assert window.band_panel.enable_check.isChecked() is True
    assert window.band_panel.fc_spin.value() == pytest.approx(150.0, abs=1.0)
    assert window.band_panel.bw_spin.value() == pytest.approx(100.0, abs=1.0)
    assert snap.mode == Mode.PINK  # idle -> auto-played the selected noise


def test_audition_sweep_starts_focus_sweep_over_region(qtbot) -> None:
    from eq_helper.audio.shared_state import Mode
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.graphic_eq_panel.auditionSweepRequested.emit(100.0, 200.0)
    snap = window.engine.state.snapshot()
    assert snap.mode == Mode.SWEEP
    assert snap.sweep_f_start == pytest.approx(100.0, rel=0.05)
    assert snap.sweep_f_end == pytest.approx(200.0, rel=0.05)


def test_audition_mode_off_disables_isolation(qtbot) -> None:
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.graphic_eq_panel.auditionIsolateRequested.emit(100.0, 200.0)
    assert window.band_panel.enable_check.isChecked() is True
    window.graphic_eq_panel.auditionModeChanged.emit(False)
    assert window.band_panel.enable_check.isChecked() is False
    assert window.engine.state.snapshot().band_enabled is False
