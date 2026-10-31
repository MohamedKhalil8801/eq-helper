"""Tuning panel: find your resonance (ears and Centre, sliders, upper treble lock, power button), target."""

import math

import numpy as np
import pytest

from eq_helper.audio.shared_state import ChannelMode, Mode
from eq_helper.dsp.resonance import CONTROL_RANGE
from resonancefit.audio import SAFE_START_VOLUME, AudioService
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.ui.main_window import LoadedCalibration
from resonancefit.ui.tuning_panel import TuningPanel

from .conftest import make_calibration


@pytest.fixture
def ctx(tmp_path):
    return AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app"))


def loaded(ctx, tmp_path, **kwargs):
    from eq_helper.calibration.rescal import write_rescal
    entry = ctx.library.import_file(write_rescal(tmp_path / "c.rescal", make_calibration(**kwargs)))
    cal = ctx.library.load(entry.calibration_id)
    return LoadedCalibration(entry, cal, cal.measurements())


@pytest.fixture
def panel(qtbot, ctx, tmp_path):
    p = TuningPanel(ctx, AudioService())
    qtbot.addWidget(p)
    with qtbot.waitSignal(p.eqChanged, timeout=10000):
        p.set_calibration(loaded(ctx, tmp_path))
    return p


def test_sides_default_target_and_first_eq(panel):
    assert [panel.ears.tabText(i) for i in range(panel.ears.count())] == ["Left", "Right", "Centre"]
    assert panel.session.target_name == "PEQdB Diamond"
    assert "default" in panel.target_combo.currentText().lower()
    eq = panel.channel_eq
    ref = panel.session.channel_eq()
    assert np.allclose(eq.channels["L"].correction_db, ref.channels["L"].correction_db)
    assert panel.graph.curve_names() == ["Your IEM", "Target", "With your EQ"]


def test_slider_moves_only_the_selected_side_and_is_debounced(qtbot, panel):
    panel.ears.setCurrentIndex(0)
    right_before = panel.session.primary["R"]
    emitted = []
    panel.eqChanged.connect(emitted.append)
    for hz in (7000, 7100, 7200):
        panel.peak_slider.setValue(hz)
    assert panel.session.primary["L"] == 7200
    assert panel.session.primary["R"] == right_before
    assert panel.peak_spin.value() == 7200
    assert panel.graph.marker_hz() == pytest.approx(7200, rel=1e-6)
    qtbot.waitUntil(lambda: len(emitted) >= 1, timeout=5000)
    qtbot.wait(400)
    assert len(emitted) == 1
    assert emitted[0].channels["L"].peak_hz == pytest.approx(7200)


def test_dragging_marker_updates_controls_and_is_clamped(qtbot, panel):
    panel.ears.setCurrentIndex(1)
    panel.graph.marker.setValue(math.log10(8400))
    panel.graph.marker.sigPositionChanged.emit(panel.graph.marker)
    assert panel.peak_slider.value() == 8400
    assert panel.session.primary["R"] == 8400
    panel.set_primary_hz(50000)
    assert panel.session.primary["R"] == CONTROL_RANGE[1]


def test_play_tone_routes_to_selected_ear_and_follows_slider(panel):
    engine = panel.audio.engine
    assert engine.state.snapshot().amp == pytest.approx(SAFE_START_VOLUME)
    panel.ears.setCurrentIndex(1)
    panel.tone_button.click()
    snap = engine.state.snapshot()
    assert snap.mode == Mode.SINE and snap.channel_mode == ChannelMode.RIGHT
    assert snap.freq == pytest.approx(panel.session.primary["R"])
    panel.peak_slider.setValue(8000)
    assert engine.state.snapshot().freq == pytest.approx(8000)
    panel.sweep_button.click()
    assert engine.state.snapshot().mode == Mode.SWEEP
    panel.audio.stop_all()
    assert engine.state.snapshot().mode == Mode.IDLE


def test_plain_warning_outside_measured_range(panel):
    panel.set_primary_hz(7500)
    assert not panel.range_warning.isVisibleTo(panel)
    panel.set_primary_hz(6100)
    assert panel.range_warning.isVisibleTo(panel)
    assert "estimate" in panel.range_warning.text().lower()


def test_upper_resonance_auto_follows_the_resonance_manual_stays_put(qtbot, panel):
    """The upper treble row is always there: Auto (as calibrated, moving with the resonance) or Manual (each
    ear's own value, which the resonance never moves). No Reset: Auto is the way back."""
    assert panel.upper.isVisibleTo(panel) and not panel.upper_slider.isEnabled()
    assert panel.upper_auto.isChecked() and not panel.upper_manual.isChecked()
    assert not panel.session.upper_enabled and not hasattr(panel, "reset_upper")
    assert not panel.graph.upper_marker.isVisible()
    panel.ears.setCurrentIndex(0)
    panel._upper_prediction = lambda side, hz: 1.9 * hz  # this test calibration has no upper peak
    shown = []
    for hz in (6500, 8500):  # Auto shows the calibration's upper treble for the resonance: it moves with it
        panel.peak_slider.setValue(hz)
        qtbot.waitUntil(lambda: panel.upper_slider.value() == panel._predicted_upper(), timeout=2000)
        shown.append(panel.upper_slider.value())
    assert shown[0] != shown[1]
    panel.upper_manual.click()
    assert panel.upper_slider.isEnabled() and panel.session.upper_enabled
    pinned = dict(panel.session.upper)  # Manual: every ear keeps the value it showed
    assert set(pinned) == {"L", "R"}
    panel.upper_slider.setValue(15000)
    assert panel.session.upper == {"L": 15000, "R": pinned["R"]}
    for ear in (1, 0):  # the right ear (never touched) and the left: moving the resonance moves neither
        panel.ears.setCurrentIndex(ear)
        before = panel.upper_slider.value()
        panel.peak_slider.setValue(panel.peak_slider.value() + 400)
        qtbot.wait(50)
        assert panel.upper_slider.value() == before
    assert panel.session.upper == {"L": 15000, "R": pinned["R"]}
    with qtbot.waitSignal(panel.eqChanged, timeout=5000):
        panel.upper_auto.setChecked(True)
    assert not panel.session.upper_enabled and not panel.upper_slider.isEnabled()


def test_target_choice_and_import(qtbot, panel, tmp_path):
    names = [panel.target_combo.itemText(i) for i in range(panel.target_combo.count())]
    assert any(n.startswith("Harman IE 2019") for n in names)
    index = next(i for i, n in enumerate(names) if n.startswith("Harman IE 2019"))
    with qtbot.waitSignal(panel.eqChanged, timeout=5000):
        panel.target_combo.setCurrentIndex(index)
    assert panel.session.target_name == "Harman IE 2019"
    f = np.geomspace(20, 20000, 100)
    path = tmp_path / "Mine.txt"
    path.write_text("\n".join(f"{x} {75 - np.log2(x / 1000)}" for x in f))
    with qtbot.waitSignal(panel.eqChanged, timeout=5000):
        panel.import_target(path)
    assert panel.session.target_name == "Mine"
    bad = tmp_path / "Bad.txt"
    bad.write_text("nothing here")
    assert not panel.import_target(bad)
    assert panel.target_problem.text()
    assert panel.session.target_name == "Mine"


def test_choices_are_remembered_per_calibration(qtbot, ctx, tmp_path, panel):
    panel.set_primary_hz(8100)
    panel.remember()
    again = TuningPanel(ctx, AudioService())
    qtbot.addWidget(again)
    with qtbot.waitSignal(again.eqChanged, timeout=10000):
        again.set_calibration(panel.loaded)
    assert again.session.primary[panel.side] == 8100


def test_shared_calibration_has_one_both_ears_tab(qtbot, ctx, tmp_path):
    from eq_helper.calibration.rescal import Calibration
    from eq_helper.dsp.resonance import Fit
    cal = make_calibration()
    cal = Calibration(cal.manifest, [Fit(None, f.name, f.freqs, f.db) for f in cal.fits if f.side == "L"])
    from eq_helper.calibration.rescal import write_rescal
    entry = ctx.library.import_file(write_rescal(tmp_path / "m.rescal", cal))
    p = TuningPanel(ctx, AudioService())
    qtbot.addWidget(p)
    with qtbot.waitSignal(p.eqChanged, timeout=10000):
        p.set_calibration(LoadedCalibration(entry, cal, cal.measurements()))
    assert p.ears.count() == 1 and p.ears.tabText(0) == "Both ears"


def test_centre_plays_in_both_ears_and_moves_both(qtbot, panel):
    engine = panel.audio.engine
    asked = []
    panel.confirm_link = lambda which, values: asked.append(which) or True
    panel.ears.setCurrentIndex(2)
    assert panel.ear == "C" and panel.side == "L"
    panel.peak_slider.setValue(7300)
    assert panel.session.primary == {"L": 7300, "R": 7300}
    assert panel.readout_side.text() == "Both ears resonance"
    panel.tone_button.click()
    snap = engine.state.snapshot()
    assert snap.mode == Mode.SINE and snap.channel_mode == ChannelMode.BOTH
    panel.ears.setCurrentIndex(1)  # a playing tone moves to the ear
    assert engine.state.snapshot().channel_mode == ChannelMode.RIGHT
    panel.peak_slider.setValue(8100)
    assert panel.session.primary == {"L": 7300, "R": 8100}
    panel.ears.setCurrentIndex(2)
    asked.clear()
    panel.peak_slider.setValue(7000)  # the ears differ now: asked first
    assert asked == ["primary"] and panel.session.primary == {"L": 7000, "R": 7000}
    panel.peak_slider.setValue(7050)  # the same again: nothing to ask
    assert asked == ["primary"]
    panel.upper_manual.click()
    panel.upper_slider.setValue(14500)
    assert panel.session.upper == {"L": 14500, "R": 14500}
    panel.audio.stop_all()


def test_centre_keeps_different_ears_when_declined(panel):
    panel.ears.setCurrentIndex(1)
    panel.peak_slider.setValue(8100)
    panel.ears.setCurrentIndex(0)
    panel.peak_slider.setValue(7300)
    panel.confirm_link = lambda which, values: values == {"L": 7300, "R": 8100} and False
    panel.ears.setCurrentIndex(2)
    panel.peak_slider.setValue(7600)
    assert panel.session.primary == {"L": 7300, "R": 8100}  # nothing changed
    assert panel.peak_slider.value() == 7300  # and the slider shows what's there


def test_full_sweep_button(panel):
    panel.ears.setCurrentIndex(1)
    button = panel.sound_buttons["sweep"]
    button.click()
    snap = panel.audio.engine.state.snapshot()
    assert snap.mode == Mode.SWEEP and snap.channel_mode == ChannelMode.RIGHT and panel.audio.playing == "sweep"
    assert button.isChecked() and button.text() == "■ Stop sweep"
    button.click()
    assert panel.audio.playing is None and button.text() == "〰 Full sweep"


def test_power_button_switches_the_eq_preview(panel):
    seen = []
    panel.eqToggled.connect(seen.append)
    assert "EQ off" in panel.power_button.text()
    panel.power_button.click()
    assert seen == [True] and "EQ on" in panel.power_button.text()
    panel.set_eq_on(False)  # follows changes made elsewhere, without echoing them back
    assert seen == [True] and not panel.power_button.isChecked() and "EQ off" in panel.power_button.text()
