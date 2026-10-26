"""Regressions for settings, real controls, and history during personal fitting."""
import numpy as np
from tests.test_resonance_ui import resonance_window


def load_flat(dialog, tmp_path):
    path = tmp_path / 'flat.txt'
    path.write_text('20 0\n1000 0\n20000 0\n')
    dialog.load_target(path)
    dialog._generate()


def test_toggle_off_on_retains_both_ear_preferences(resonance_window, tmp_path):
    main, dialog, _ = resonance_window
    load_flat(dialog, tmp_path)
    baseline = dialog._selected.eq
    dialog.personal_treble_enabled.setChecked(True)
    dialog.personal_treble_spin.setValue(13500)
    dialog._commit_personal_treble()
    dialog.ears.setCurrentIndex(1)
    dialog.personal_treble_slider._slider.setValue(14000 - 10500)
    assert dialog.personal_treble_spin.value() == 14000
    dialog._commit_personal_treble()
    dialog.personal_treble_enabled.setChecked(False)
    assert main._settings['resonance_personal_treble_peaks_hz'] == {'L': 13500, 'R': 14000}
    for side in ('L', 'R'):
        np.testing.assert_array_equal(dialog._selected.eq.channels[side].correction_db,
                                      baseline.channels[side].correction_db)
    dialog.personal_treble_enabled.setChecked(True)
    assert dialog._selected.eq.channels['L'].personal_peak_hz == 13500
    assert dialog._selected.eq.channels['R'].personal_peak_hz == 14000
    dialog._reset_personal_treble()
    assert dialog.personal_treble_enabled.isChecked()  # Left remains personalized.
    assert dialog._selected.eq.channels['L'].personal_peak_hz == 13500
    assert dialog._selected.eq.channels['R'].personal_peak_hz is None
    dialog.reject()
    main._open_resonance_dialog()
    reopened = main._resonance_dialog
    assert reopened.personal_treble_enabled.isChecked()
    assert reopened.personal_treble_peaks == {'L': 13500}


def test_saved_enabled_preference_with_no_target_does_not_require_selected_history(resonance_window):
    main, dialog, _ = resonance_window
    dialog.personal_treble_enabled.setChecked(True)
    dialog.personal_treble_spin.setValue(13500)
    dialog.reject()
    main._open_resonance_dialog()
    reopened = main._resonance_dialog
    assert reopened.personal_treble_enabled.isChecked()
    assert reopened.personal_treble_spin.value() == 13500


def test_live_audition_keeps_ear_filters_independent(resonance_window, tmp_path, qtbot):
    from pathlib import Path
    from eq_helper.dsp.graphic_eq import parse_graphic_eq_line
    from eq_helper.audio.shared_state import ChannelMode

    main, dialog, _ = resonance_window
    load_flat(dialog, tmp_path)
    dialog.personal_treble_enabled.setChecked(True)
    dialog.personal_treble_spin.setValue(13500)
    dialog._commit_personal_treble()
    dialog.ears.setCurrentIndex(1)
    dialog.personal_treble_spin.setValue(14400)
    dialog._commit_personal_treble()
    dialog.audition.setChecked(True)
    preview = Path(dialog._preview_eq.path)

    def filters():
        text = preview.read_text(encoding='utf-8')
        assert text == dialog._selected.eq.apo_text()
        result = {}
        side = None
        for line in text.splitlines():
            if line.startswith('Channel:'):
                side = line.partition(':')[2].strip()
            elif line.startswith('GraphicEQ:'):
                result[side] = np.array([(b.freq_hz, b.gain_db) for b in parse_graphic_eq_line(line).bands])
        return result

    before = filters()
    dialog.ears.setCurrentIndex(0)
    dialog.personal_treble_spin.setValue(12500)
    qtbot.waitUntil(lambda: dialog._selected.eq.channels['L'].personal_peak_hz == 12500, timeout=3000)
    after_left = filters()
    np.testing.assert_array_equal(after_left['R'], before['R'])
    assert not np.array_equal(after_left['L'], before['L'])
    dialog._play_sine(14000)
    assert main.engine.state.snapshot().channel_mode == ChannelMode.LEFT
    dialog.ears.setCurrentIndex(1)
    dialog.personal_treble_spin.setValue(15000)
    # Switch tabs before the debounce fires: each ear must keep its own value.
    dialog.ears.setCurrentIndex(0)
    qtbot.waitUntil(lambda: dialog._selected.eq.channels['R'].personal_peak_hz == 15000, timeout=3000)
    after_right = filters()
    np.testing.assert_array_equal(after_right['L'], after_left['L'])
    assert not np.array_equal(after_right['R'], after_left['R'])
    assert dialog.personal_treble_spin.value() == 12500
    dialog.audition.setChecked(False)
    assert 'GraphicEQ:' not in preview.read_text(encoding='utf-8')
    dialog.audition.setChecked(True)
    restored = filters()
    for side in ('L', 'R'):
        np.testing.assert_array_equal(restored[side], after_right[side])


def test_playback_toggle_and_export_flush_pending_frequency_edits(resonance_window, tmp_path, monkeypatch, qtbot):
    from pathlib import Path
    from eq_helper.ui.resonance_dialog import QFileDialog
    _main, dialog, _ = resonance_window
    load_flat(dialog, tmp_path)
    dialog.personal_treble_enabled.setChecked(True)
    dialog.personal_treble_spin.setValue(13500)
    assert dialog._commit_timer.isActive()
    dialog.audition.setChecked(True)
    qtbot.waitUntil(lambda: not dialog._commit_timer.isActive(), timeout=2000)
    assert dialog._selected.eq.channels['L'].personal_peak_hz == 13500
    preview = Path(dialog._preview_eq.path)
    assert 'requested 13500 Hz' in preview.read_text(encoding='utf-8')
    dialog.ears.setCurrentIndex(1)
    dialog.personal_treble_spin.setValue(14400)
    assert dialog._commit_timer.isActive()
    output = tmp_path / 'immediate-export.txt'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(output), ''))
    dialog._save()
    qtbot.waitUntil(lambda: not dialog._commit_timer.isActive(), timeout=2000)
    qtbot.waitUntil(lambda: output.exists(), timeout=2000)
    assert dialog._selected.eq.channels['R'].personal_peak_hz == 14400
    saved = output.read_text(encoding='utf-8')
    assert 'requested 13500 Hz' in saved
    assert 'requested 14400 Hz' in saved
    # Both ears should have personal alignment comments with different coupler peaks.
    assert saved.count('correction anchor') == 2
    assert 'Local correction warp only in' in saved  # Band restriction comment present.


def test_noop_adjustment_deduplicates_history(resonance_window, tmp_path, qtbot):
    from tests.test_resonance import write_fit
    main, dialog, _ = resonance_window
    folder = tmp_path / 'fits'
    write_fit(folder / 'L 1.txt')  # No second resonance.
    dialog.load_measurements(str(folder))
    qtbot.waitUntil(lambda: dialog._loader is None, timeout=20000)
    load_flat(dialog, tmp_path)
    dialog.personal_treble_enabled.setChecked(True)
    dialog.personal_treble_spin.setValue(13500)
    dialog._commit_personal_treble()
    count = dialog.history.count()
    dialog._generate()
    assert dialog.history.count() == count
    assert 'no resolved' in dialog.spectrum.title().lower()
