"""End-to-end dialog workflow using temporary APO files and no audio hardware."""

from pathlib import Path

import numpy as np
import pytest

from eq_helper.apo.presets import PresetLibrary
from eq_helper.audio.shared_state import ChannelMode, Mode
from tests.test_resonance import write_fit


@pytest.fixture
def resonance_window(qtbot, monkeypatch, tmp_path):
    from eq_helper.ui import main_window as module
    apo = tmp_path / 'apo'
    apo.mkdir()
    (apo / 'config.txt').write_text('Device: Nova {0123abcd-0000-0000-0000-000000000000}\nInclude: original.txt\n')
    (apo / 'original.txt').write_text('# original preset\n')
    monkeypatch.setattr(module, 'load_settings', lambda: {'apo_config_dir_override': str(apo)})
    monkeypatch.setattr(module, 'default_settings_path', lambda: tmp_path / 'settings.json')
    window = module.MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._open_resonance_dialog()
    yield window, window._resonance_dialog, PresetLibrary(apo)
    if window._resonance_dialog is not None:
        qtbot.waitUntil(lambda: window._resonance_dialog._loader is None, timeout=20000)
        window._resonance_dialog.reject()


def test_independent_peaks_routing_history_export_and_folder_reload(resonance_window, qtbot, monkeypatch, tmp_path):
    from eq_helper.ui.resonance_dialog import QFileDialog
    main, dialog, library = resonance_window
    target = tmp_path / 'target.txt'
    target.write_text('20 0\n1000 0\n20000 0\n')
    dialog.load_target(target)
    assert dialog.ears.count() == 2
    assert dialog.peak_spin.minimum() == 6000 and dialog.peak_spin.maximum() == 9000
    dialog._generate()
    dialog._move_peak(7300)
    dialog._commit_peak()
    dialog.ears.setCurrentIndex(1)
    assert dialog.side == 'R' and dialog.peak.value() == 7700
    assert dialog.peaks['L'] == 7300
    dialog._move_peak(8200)
    dialog._commit_peak()
    assert dialog.peaks == {'L': 7300, 'R': 8200}
    dialog.spectrum.frequencyClicked.emit(7833)
    assert main.engine.state.snapshot().freq == 7833
    assert main.engine.state.snapshot().channel_mode == ChannelMode.RIGHT
    dialog.spectrum.focusSweepRequested.emit(8123)
    assert main.engine.state.snapshot().mode == Mode.SWEEP
    assert main.engine._focus_center_hz == 8123
    dialog.listen_both.setChecked(True)
    assert main.engine.state.snapshot().channel_mode == ChannelMode.BOTH
    dialog.listen_both.setChecked(False)
    dialog.audition.setChecked(True)
    preview = Path(dialog._preview_eq.path)
    assert library.active_preset('Nova').name.startswith('Resonance audition')
    text = preview.read_text(encoding='utf-8')
    assert text.count('GraphicEQ:') == 2
    dialog.history.setCurrentRow(0)
    assert dialog.peaks == {'L': 7700, 'R': 7700}
    dialog.audition.setChecked(False)
    assert 'GraphicEQ:' not in preview.read_text(encoding='utf-8')
    export = tmp_path / 'export.txt'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(export), ''))
    dialog._save()
    assert export.read_text(encoding='utf-8') == dialog._selected.eq.apo_text()
    assert 'personal treble' not in export.read_text(encoding='utf-8').lower()

    # The optional second-resonance fit starts disabled and keeps the coupler EQ.
    dialog.ears.setCurrentIndex(0)
    dialog.history.setCurrentRow(0)
    assert not dialog.personal_treble_enabled.isChecked()
    original_l = dialog._selected.eq.channels['L']
    original_correction = original_l.correction_db.copy()
    dialog.personal_treble_enabled.setChecked(True)
    assert dialog.personal_treble_controls.isEnabled()
    dialog.personal_treble_spin.setValue(13500)
    assert dialog.personal_treble_peaks == {'L': 13500}
    assert dialog.personal_treble_enabled.isChecked()
    dialog._commit_personal_treble()
    assert dialog._selected.eq.channels['L'].personal_peak_hz == 13500
    assert dialog._selected.eq.channels['L'].coupler_treble_peak_hz is not None
    assert 'personal treble' in dialog.history.currentItem().text().lower()
    assert '# Personal higher-treble fit is optional' in dialog._selected.eq.apo_text()
    assert not np.array_equal(original_correction, dialog._selected.eq.channels['L'].correction_db)
    assert dialog._selected.eq.channels['R'].personal_peak_hz is None
    assert main._settings['resonance_personal_treble_enabled'] is True
    assert main._settings['resonance_personal_treble_peaks_hz'] == {'L': 13500}
    dialog.audition.setChecked(True)
    assert preview.read_text(encoding='utf-8') == dialog._selected.eq.apo_text()
    dialog._save()
    assert export.read_text(encoding='utf-8') == dialog._selected.eq.apo_text()
    assert dialog._selected.eq.apo_text().count('# Personal treble correction alignment') == 1
    assert '13500 Hz' in dialog._selected.eq.apo_text()
    dialog.audition.setChecked(False)

    export_before_reset = dialog._selected.eq.apo_text()
    dialog._reset_personal_treble()
    assert not dialog.personal_treble_enabled.isChecked()
    assert dialog.personal_treble_spin.value() == 15500
    assert dialog._selected.eq.channels['L'].personal_peak_hz is None
    assert dialog._selected.eq.apo_text() != export_before_reset
    dialog.personal_treble_enabled.setChecked(False)
    assert dialog._selected.eq.channels['L'].personal_peak_hz is None
    assert 'L' not in main._settings['resonance_personal_treble_peaks_hz']
    np.testing.assert_array_equal(original_correction, dialog._selected.eq.channels['L'].correction_db)
    assert 'personalized treble on' not in dialog.history.currentItem().text()

    # Import one side only, then recover a stereo version across dataset changes.
    folder = tmp_path / 'only-right'
    write_fit(folder / 'R 1.txt', 8100)
    dialog.load_measurements(str(folder))
    qtbot.waitUntil(lambda: dialog._loader is None, timeout=20000)
    assert dialog.ears.count() == 1 and set(dialog.peaks) == {'R'}
    assert 'shared' in dialog.generate.text()
    assert main._settings['resonance_measurements_dir'] == str(folder.resolve())
    assert library.active_preset('Nova').name == 'original'
    dialog.audition.setChecked(True)
    one_side_preview = Path(dialog._preview_eq.path)
    assert one_side_preview.read_text(encoding='utf-8').count('GraphicEQ:') == 1
    assert '\nChannel: R\n' not in one_side_preview.read_text(encoding='utf-8')
    dialog.history.setCurrentRow(0)
    assert dialog.ears.count() == 2
    assert one_side_preview.read_text(encoding='utf-8').count('GraphicEQ:') == 2
    dialog.reject()
    assert library.active_preset('Nova').name == 'original'
    assert not one_side_preview.exists()
    assert main.engine.state.snapshot().channel_mode == ChannelMode.BOTH
    assert export.exists()


def test_failed_folder_import_keeps_working_model(resonance_window, qtbot, monkeypatch, tmp_path):
    from eq_helper.ui.resonance_dialog import QMessageBox
    _main, dialog, _library = resonance_window
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    old = dialog.measurements
    dialog.load_measurements(str(tmp_path / 'missing'))
    qtbot.waitUntil(lambda: dialog._loader is None, timeout=20000)
    assert dialog.measurements is old
    assert warnings and 'not found' in warnings[0]
    assert dialog.load_measurements_button.isEnabled()
