"""④ Listen & save and the history sidebar."""

import pytest

from eq_helper.calibration.rescal import write_rescal
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.ui.main_window import MainWindow

from .conftest import make_calibration


@pytest.fixture
def win(qtbot, tmp_path):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration(customer="Ada", created="2026-10-02"))
    w = MainWindow(AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app")), start_audio=False)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None and w.tuning.channel_eq is not None, timeout=20000)
    return w


def test_no_entries_until_saved(win):
    win.tuning.set_primary_hz(7300)
    win.tuning.regenerate()
    assert win.history_panel.list.count() == 0
    assert win.history.entries() == []


def test_save_tuning_with_note_shows_entry(win):
    win.tuning.ears.setCurrentIndex(0)
    win.tuning.set_primary_hz(7400)
    win.tuning.regenerate()
    win.listen.note.setText("bright tips")
    win.listen.save_button.click()
    entries = win.history.entries()
    assert len(entries) == 1 and entries[0].reason == "manual" and entries[0].note == "bright tips"
    text = win.history_panel.list.item(0).text()
    assert text.splitlines()[0] == "bright tips"  # the name first
    assert "PEQdB Diamond" in text and "7400" in text
    assert win.listen.note.text() == ""
    assert "Saved as “bright tips”" in win.listen.status.text()
    win.listen.save_button.click()  # the same tuning again: no second entry
    assert len(win.history.entries()) == 1 and "Already saved" in win.listen.status.text()


def test_unnamed_tunings_are_named_from_their_settings_and_can_be_renamed(win, monkeypatch):
    t = win.tuning
    t.ears.setCurrentIndex(0)
    t.set_primary_hz(7400)
    t.regenerate()
    win.listen.save_button.click()
    entry = win.history.entries()[0]
    first_line = win.history_panel.list.item(0).text().splitlines()[0]
    assert first_line.startswith(t.session.target_name) and "7400" in first_line and len(first_line) < 45
    monkeypatch.setattr(win.history_panel, "ask_name", lambda current: ("Night", True))
    win.history_panel.list.setCurrentRow(0)
    win.history_panel.rename_button.click()
    assert win.history.get(entry.id).note == "Night"
    assert win.history_panel.list.item(0).text().splitlines()[0] == "Night"


def test_reapply_restores_all_controls(win, qtbot):
    t = win.tuning
    t.ears.setCurrentIndex(0)
    t.set_primary_hz(7400)
    t.ears.setCurrentIndex(1)
    t.set_primary_hz(8200)
    t.upper_lock.setChecked(True)
    t.upper_slider.setValue(14000)
    t.regenerate()
    win.listen.save_button.click()
    saved_target = t.session.target_name
    # change everything
    t.set_primary_hz(6600)
    t.upper_lock.setChecked(False)
    other = next(i for i in range(t.target_combo.count()) if t.target_combo.itemText(i).startswith("Harman IE 2019"))
    t.target_combo.setCurrentIndex(other)
    win.history_panel.list.setCurrentRow(0)
    with qtbot.waitSignal(t.eqChanged, timeout=5000):
        win.history_panel.reapply_button.click()
    assert t.session.primary == {"L": 7400, "R": 8200}
    assert t.session.upper == {"R": 14000} and t.session.upper_enabled
    assert t.upper_lock.isChecked()
    assert t.session.target_name == saved_target
    assert t.target_combo.currentText().startswith(saved_target)
    assert t.peak_slider.value() in (7400, 8200)


def test_reapply_after_target_file_is_gone_uses_stored_curve(win, tmp_path, qtbot):
    import numpy as np
    f = np.geomspace(20, 20000, 80)
    p = tmp_path / "Gone.txt"
    p.write_text("\n".join(f"{x} {75 + np.sin(x / 3000)}" for x in f))
    assert win.tuning.import_target(p)
    win.listen.save_button.click()
    (win.ctx.paths.user_targets_dir / "Gone.txt").unlink()
    win.ctx.targets.scan()
    win.tuning.set_calibration(win.current)  # reload targets without the file
    assert win.tuning.session.target_name != "Gone"
    win.history_panel.list.setCurrentRow(0)
    win.history_panel.reapply_button.click()
    assert win.tuning.session.target_name == "Gone"
    assert "saved" in win.tuning.target_combo.currentText()


def test_eq_toggle_previews_in_app(win):
    assert not win.audio.eq_enabled
    win.tuning.power_button.click()
    assert win.audio.eq_enabled
    on, off = win.audio.preview_firs()
    assert set(on) == {"L", "R"} and set(off) == {"L", "R"}
    assert len(on["L"]) > 100 and len(off["L"]) == 1
    win.tuning.power_button.click()
    assert not win.audio.eq_enabled


def test_history_is_per_calibration(win, qtbot, tmp_path):
    win.listen.save_button.click()
    other = write_rescal(tmp_path / "b.rescal", make_calibration(customer="Bob", created="2026-10-20"))
    win.open_calibration_file(other)
    qtbot.waitUntil(lambda: win.current.manifest.customer.name == "Bob" and not win.loading, timeout=20000)
    assert win.history_panel.list.count() == 0
    win.select_calibration(next(e.calibration_id for e in win.ctx.library.list() if e.manifest.customer.name == "Ada"))
    qtbot.waitUntil(lambda: win.current.manifest.customer.name == "Ada" and not win.loading, timeout=20000)
    assert win.history_panel.list.count() == 1


def test_apply_button_hidden_without_apo(win):
    assert not win.listen.apply_button.isVisibleTo(win)
    assert win.listen.export_button.isVisibleTo(win)
