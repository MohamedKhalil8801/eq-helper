"""First launch: walkthrough, listening safety, keyboard and contrast."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from eq_helper.audio.shared_state import Mode
from eq_helper.calibration.rescal import write_rescal
from resonancefit.audio import SAFE_START_VOLUME, AudioService
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.ui import walkthrough as walkthrough_module
from resonancefit.ui.common import contrast_ratio, theme_colors
from resonancefit.ui.main_window import MainWindow
from resonancefit.ui.walkthrough import Walkthrough

from .conftest import make_calibration


@pytest.fixture
def shown(monkeypatch):
    calls = []
    monkeypatch.setattr(walkthrough_module.Walkthrough, "exec", lambda self: calls.append(self) or 1)
    return calls


def _window(qtbot, tmp_path, confirm=None):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True, exist_ok=True)
    if not any(folder.iterdir()):
        write_rescal(folder / "a.rescal", make_calibration())
    w = MainWindow(AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app")), start_audio=False)
    if confirm is not None:
        w.confirm_quiet_start = confirm
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None and w.tuning.channel_eq is not None, timeout=20000)
    return w


def test_walkthrough_once_then_from_settings(qtbot, tmp_path, shown):
    w = _window(qtbot, tmp_path)
    qtbot.waitUntil(lambda: len(shown) == 1, timeout=5000)
    w2 = _window(qtbot, tmp_path)
    qtbot.wait(200)
    assert len(shown) == 1
    w2.settings_page.findChild(type(w2.settings_page.open_data), "walkthroughButton").click()
    assert len(shown) == 2


def test_walkthrough_has_three_steps(qtbot):
    dialog = Walkthrough("Product")
    qtbot.addWidget(dialog)
    assert dialog.count() == 3
    assert dialog.back_button.isHidden() or not dialog.back_button.isEnabled()
    dialog.next_button.click()
    dialog.next_button.click()
    assert dialog.index() == 2
    assert dialog.next_button.text() == "Start"


def test_volume_starts_low_every_launch():
    audio = AudioService()
    audio.set_volume(0.8)
    again = AudioService()
    assert again.volume == SAFE_START_VOLUME
    assert again.engine.state.snapshot().amp == pytest.approx(SAFE_START_VOLUME)


def test_quiet_warning_once_before_first_sound(qtbot, tmp_path, shown):
    asked = []
    w = _window(qtbot, tmp_path, confirm=lambda: asked.append(1) or False)
    w.tuning.tone_button.click()
    assert asked == [1]
    assert w.audio.engine.state.snapshot().mode == Mode.IDLE  # declined: nothing plays
    w.confirm_quiet_start = lambda: asked.append(2) or True
    w.tuning.tone_button.click()
    assert asked == [1, 2]
    assert w.audio.engine.state.snapshot().mode == Mode.SINE
    w.audio.stop_all()
    w.tuning.pink_button.click()
    assert asked == [1, 2]  # accepted once: never asked again
    assert w.ctx.settings.get("quiet_warning_seen") is True


def test_sound_bar_visible_with_output_volume_and_stop(qtbot, tmp_path, shown):
    w = _window(qtbot, tmp_path, confirm=lambda: True)
    bar = w.sound_bar
    assert bar.isVisibleTo(w)
    assert bar.output.combo.itemText(0) == "System default"
    w.tuning.tone_button.click()
    bar.stop_button.click()
    assert w.audio.engine.state.snapshot().mode == Mode.IDLE


def test_space_stops_sound_but_types_in_text_fields(qtbot, tmp_path, shown):
    w = _window(qtbot, tmp_path, confirm=lambda: True)
    w.show()
    qtbot.waitExposed(w)
    w.tuning.tone_button.click()
    w.tuning.tone_button.setFocus()
    qtbot.keyClick(w.tuning.tone_button, Qt.Key_Space)
    assert w.audio.engine.state.snapshot().mode == Mode.IDLE
    w.tunings.note.setFocus()
    qtbot.keyClicks(w.tunings.note, "a b")
    assert w.tunings.note.text() == "a b"


@pytest.mark.parametrize("dark", [False, True])
def test_hint_and_warning_text_meet_contrast(dark):
    pal = QPalette()
    base, text = (QColor("#1e1e1e"), QColor("#f0f0f0")) if dark else (QColor("#ffffff"), QColor("#202020"))
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.Window, base)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.WindowText, text)
    colors = theme_colors(pal)
    assert colors["dark"] == dark
    for surface in ("bg", "surface", "raised", "control"):  # text: 4.5:1 on every surface it sits on
        for key in ("text", "hint", "accent", "warning"):
            assert contrast_ratio(colors[key], colors[surface]) >= 4.5, (key, surface)
    assert contrast_ratio(colors["on_accent"], colors["accent"]) >= 4.5
    for key in ("border", "target", "corrected", "upper", "focus"):  # edges and graph lines: 3:1
        for surface in ("bg", "surface", "control"):
            assert contrast_ratio(colors[key], colors[surface]) >= 3.0, (key, surface)
