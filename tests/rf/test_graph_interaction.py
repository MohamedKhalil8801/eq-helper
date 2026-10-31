"""Graph: hover shows the frequency, one press plays (drag follows), both resonances have a line;
pink noise through the EQ while moving the resonance; the "turn off other EQs" warning."""

import math

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from eq_helper.audio.shared_state import ChannelMode, Mode
from resonancefit.audio import AudioService
from resonancefit.ui.tuning_panel import LIVE_REGENERATE_DELAY_MS, TuningPanel

from .test_tuning_panel import ctx, loaded  # noqa: F401 - fixtures/helpers


@pytest.fixture
def panel(qtbot, ctx, tmp_path):  # noqa: F811
    p = TuningPanel(ctx, AudioService())
    qtbot.addWidget(p)
    p.resize(1000, 700)
    p.show()
    qtbot.waitExposed(p)
    with qtbot.waitSignal(p.eqChanged, timeout=10000):
        p.set_calibration(loaded(ctx, tmp_path))
    return p


def _point_for(graph, hz, db=0.0):
    """Viewport pixel for (hz, dB) on the graph."""
    vb = graph.plot.getPlotItem().vb
    scene = vb.mapViewToScene(QPointF(math.log10(hz), db))
    return graph.plot.mapFromScene(scene)


def _send(graph, kind, pos, buttons):
    viewport = graph.plot.viewport()
    button = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    global_pos = viewport.mapToGlobal(pos)
    event = QMouseEvent(kind, QPointF(pos), QPointF(global_pos), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(viewport, event)


def test_hover_shows_the_frequency_under_the_mouse(qtbot, panel):
    graph = panel.graph
    _send(graph, QEvent.MouseMove, _point_for(graph, 5000, -20), Qt.NoButton)
    assert graph.hover_hz == pytest.approx(5000, rel=0.02)
    assert graph.hover_label.isVisible() and graph.crosshair.isVisible()
    assert "Hz" in graph.hover_label.toPlainText() and graph.hover_label.toPlainText().startswith(("4,", "5,"))
    QApplication.sendEvent(graph.plot.viewport(), QEvent(QEvent.Leave))
    assert graph.hover_hz is None and not graph.hover_label.isVisible()


def test_single_press_plays_and_drag_follows(qtbot, panel):
    graph, engine = panel.graph, panel.audio.engine
    panel.ears.setCurrentIndex(1)
    _send(graph, QEvent.MouseMove, _point_for(graph, 1000, -20), Qt.NoButton)   # hover line under the mouse
    assert graph.crosshair.isVisible()
    _send(graph, QEvent.MouseButtonPress, _point_for(graph, 1000, -20), Qt.LeftButton)
    snap = engine.state.snapshot()
    assert snap.mode == Mode.SINE and snap.channel_mode == ChannelMode.RIGHT
    assert snap.freq == pytest.approx(1000, rel=0.03)
    qtbot.wait(40)
    _send(graph, QEvent.MouseMove, _point_for(graph, 3000, -20), Qt.LeftButton)
    assert engine.state.snapshot().freq == pytest.approx(3000, rel=0.03)
    _send(graph, QEvent.MouseButtonRelease, _point_for(graph, 3000, -20), Qt.NoButton)
    assert engine.state.snapshot().mode == Mode.SINE  # keeps playing like EQ Helper; Space stops
    assert panel.session.primary["R"] != pytest.approx(3000)  # listening doesn't move the resonance
    panel.audio.stop_all()


def test_upper_resonance_has_its_own_line_like_the_primary(qtbot, panel):
    graph = panel.graph
    assert graph.marker.isVisible() and not graph.upper_marker.isVisible()
    panel.upper_manual.setChecked(True)
    assert graph.upper_marker.isVisible()
    assert graph.upper_marker_hz() == pytest.approx(panel.upper_slider.value(), rel=1e-6)
    panel.ears.setCurrentIndex(0)
    graph.upper_marker.setValue(math.log10(14200))
    graph.upper_marker.sigPositionChanged.emit(graph.upper_marker)
    assert panel.upper_slider.value() == 14200 and panel.upper_spin.value() == 14200
    assert panel.session.upper == {"L": 14200}
    panel.upper_spin.setValue(15000)
    assert graph.upper_marker_hz() == pytest.approx(15000, rel=1e-6)
    # one set of sound buttons; turning the upper treble on selects it, touching a slider selects that one
    assert panel.focus == "upper" and panel.upper.isVisibleTo(panel) and panel.focus_upper.isChecked()
    panel.tone_button.click()
    assert panel.audio.engine.state.snapshot().freq == pytest.approx(15000)
    panel.upper_slider.setValue(13000)
    assert panel.audio.engine.state.snapshot().freq == pytest.approx(13000)
    panel.peak_slider.setValue(7600)                                  # the tone follows whichever you move
    assert panel.focus == "primary" and panel.audio.engine.state.snapshot().freq == pytest.approx(7600)
    panel.focus_upper.click()                                         # or pick it explicitly
    assert panel.audio.engine.state.snapshot().freq == pytest.approx(13000)
    panel.sweep_button.click()
    assert panel.audio.engine.state.snapshot().mode == Mode.SWEEP
    panel.audio.stop_all()
    panel.upper_auto.setChecked(True)
    assert not graph.upper_marker.isVisible()


def test_pink_noise_through_the_eq_follows_the_resonance(qtbot, panel):
    audio = panel.audio
    assert not audio.eq_enabled
    panel.pink_button.click()
    assert audio.playing == "pink" and audio.eq_enabled
    assert panel.pink_button.isChecked() and panel.pink_hint.isVisibleTo(panel)
    emitted = []
    panel.eqChanged.connect(emitted.append)
    panel.peak_slider.setValue(7400)
    assert panel._regen.interval() == LIVE_REGENERATE_DELAY_MS
    qtbot.waitUntil(lambda: bool(emitted), timeout=2000)
    assert emitted[-1].channels[panel.side].peak_hz == pytest.approx(7400)
    panel.pink_button.click()
    assert audio.playing is None and not panel.pink_hint.isVisibleTo(panel)


def test_grey_noise_through_the_eq_works_like_pink(qtbot, panel):
    audio = panel.audio
    panel.grey_button.click()
    assert audio.playing == "grey" and audio.eq_enabled
    assert audio.engine.state.snapshot().mode == Mode.GREY
    assert panel.grey_button.isChecked() and panel.grey_button.text() == "■ Stop noise"
    assert not panel.pink_button.isChecked() and panel.pink_hint.isVisibleTo(panel)
    panel.peak_slider.setValue(7400)
    assert panel._regen.interval() == LIVE_REGENERATE_DELAY_MS
    panel.grey_button.click()
    assert audio.playing is None and not panel.grey_button.isChecked()


def test_other_eq_warning(qtbot, panel):
    panel.set_other_eqs([], False)
    assert "other EQ" in panel.other_eq.text() and not panel.apo_notice.isVisibleTo(panel)
    panel.set_other_eqs(["Equalizer APO", "Peace"], False)
    assert "Equalizer APO, Peace" in panel.other_eq.text() and panel.other_eq.objectName() == "warning"
    panel.set_other_eqs(["Equalizer APO"], True)
    # one box: what's wrong, what to do, and its button inside it; the general advice steps aside
    assert panel.apo_notice.isVisibleTo(panel) and not panel.other_eq.isVisibleTo(panel)
    assert "switched on in Equalizer APO" in panel.apo_title.text() and "twice" in panel.apo_text.text()
    assert panel.apo_pause.isVisibleTo(panel) and panel.apo_pause.parent() is panel.apo_notice
    assert panel.apo_pause.text() == "Pause it while I tune"
    assert panel.guided.intro_pause.isVisibleTo(panel.guided) or not panel.guided.isVisible()
    with qtbot.waitSignal(panel.apoOffRequested, timeout=1000):
        panel.apo_pause.click()
    with qtbot.waitSignal(panel.apoOffRequested, timeout=1000):
        panel.guided.intro_pause.click()  # the test's first screen offers it too
    panel.set_other_eqs(["Equalizer APO"], False)  # off now: say so, and how to switch it back on
    assert panel.apo_notice.isVisibleTo(panel) and not panel.apo_pause.isVisibleTo(panel)
    assert "Paused in Equalizer APO" in panel.apo_title.text() and "Apply to Equalizer APO" in panel.apo_text.text()
    assert not panel.guided.intro_pause.isVisibleTo(panel.guided)


def test_main_window_names_installed_system_eqs_and_turns_ours_off(qtbot, tmp_path, monkeypatch):
    from eq_helper.calibration.rescal import write_rescal
    from resonancefit.context import AppContext
    from resonancefit.paths import AppPaths
    from resonancefit.ui.main_window import MainWindow

    from .conftest import make_calibration

    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration())
    ctx = AppContext.create(AppPaths("windows", tmp_path / "data", tmp_path / "app"))

    class FakeApo:
        name, app_id, uses_devices = "Equalizer APO", "equalizer-apo", True
        device, on, offs = "Speakers", True, 0
        def unavailable_text(self): return ""
        def available(self): return True
        def is_on(self): return self.on
        def turn_off(self): self.on, self.offs = False, self.offs + 1
        def devices(self): return ["Speakers"]

    apo = FakeApo()
    monkeypatch.setattr("resonancefit.ui.settings_page.SettingsPage.refresh_apo", lambda self: None)
    w = MainWindow(ctx, start_audio=False, apo=apo)
    qtbot.addWidget(w)
    w._apps_detected({"equalizer-apo", "foobar2000"})       # foobar2000 is a player, not system-wide
    assert "twice" in w.tuning.apo_text.text() and w.tuning.apo_pause.isVisibleTo(w)
    w.tuning.apo_pause.click()
    assert apo.offs == 1
    text = w.tuning.other_eq.text()
    assert "Equalizer APO" in text and "foobar2000" not in text and not w.tuning.apo_pause.isVisibleTo(w)
    assert w._eq_watch.isActive()  # re-checked every few seconds while the window is active
    apo.on = True  # switched back on in Equalizer APO itself
    w.sync_other_eqs()  # what the watch does
    assert w.tuning.apo_pause.isVisibleTo(w)


def test_dragging_the_resonance_line_with_the_mouse_moves_the_resonance(qtbot, panel):
    graph = panel.graph
    panel.ears.setCurrentIndex(0)
    start = panel.session.primary["L"]
    _send(graph, QEvent.MouseMove, _point_for(graph, start, -20), Qt.NoButton)
    _send(graph, QEvent.MouseButtonPress, _point_for(graph, start, -20), Qt.LeftButton)
    for hz in (start * 1.02, start * 1.05, 8600):
        qtbot.wait(40)  # pyqtgraph ignores moves closer than ~20 ms apart
        _send(graph, QEvent.MouseMove, _point_for(graph, hz, -20), Qt.LeftButton)
    _send(graph, QEvent.MouseButtonRelease, _point_for(graph, 8600, -20), Qt.NoButton)
    assert panel.session.primary["L"] == pytest.approx(8600, rel=0.02)
    assert panel.peak_slider.value() == panel.session.primary["L"]
    assert panel.audio.playing is None  # grabbing the line doesn't start a tone


def test_a_line_shows_what_is_playing(qtbot, panel):
    graph = panel.graph
    assert not graph.playing_line.isVisible()
    panel.tone_button.click()
    hz = panel.session.primary[panel.side]
    assert graph.playing_line.isVisible() and graph.playing_hz == pytest.approx(hz)
    assert graph.playing_line.label.toPlainText() == f"▶ {hz:,.0f} Hz"
    panel.peak_slider.setValue(8100)
    assert graph.playing_hz == pytest.approx(8100)
    _send(graph, QEvent.MouseButtonPress, _point_for(graph, 2000, -20), Qt.LeftButton)
    assert graph.playing_hz == pytest.approx(2000, rel=0.03)
    _send(graph, QEvent.MouseButtonRelease, _point_for(graph, 2000, -20), Qt.NoButton)
    panel.sweep_button.click()                      # the line follows the sweep
    first = graph.playing_hz
    for _ in range(40):
        panel.audio.engine.process_block(512)   # no sound card in tests: run the engine by hand
    qtbot.wait(100)
    assert graph.playing_hz is not None and graph.playing_hz != pytest.approx(first, rel=1e-6)
    panel.audio.stop_all()
    assert not graph.playing_line.isVisible()


def test_each_sound_button_starts_and_stops_its_sound(panel):
    audio = panel.audio
    assert not panel.focus_primary.isVisibleTo(panel)        # only one resonance: no "at" choice
    panel.tone_button.click()
    assert audio.playing == "tone" and panel.tone_button.isChecked()
    assert panel.tone_button.text().startswith("■")
    panel.sweep_button.click()                              # another sound replaces it
    assert audio.playing == "focus" and not panel.tone_button.isChecked()
    assert panel.tone_button.text().startswith("▶")
    panel.sweep_button.click()                              # pressing again stops
    assert audio.playing is None and not panel.sweep_button.isChecked()
    panel.pink_button.click()
    panel.pink_button.click()
    assert audio.playing is None
