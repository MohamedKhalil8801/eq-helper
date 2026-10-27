"""The guided test in step ①: listening, answering, blindness, the EQ preview and the full flow."""

import math
import random
import re

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from eq_helper.audio.shared_state import ChannelMode, Mode
from resonancefit.audio import AudioService
from resonancefit.guided_test import LOUDER, SMOOTHNESS
from resonancefit.ui import guided_test_view
from resonancefit.ui.guided_widgets import OptionCard, stage_colors, visualizer_shape
from resonancefit.ui.tuning_panel import TuningPanel

from .test_tuning_panel import ctx, loaded  # noqa: F401 - fixture and helper

F0 = {"L": 7100.0, "R": 8300.0}


@pytest.fixture
def panel(qtbot, ctx, tmp_path):  # noqa: F811
    p = TuningPanel(ctx, AudioService())
    qtbot.addWidget(p)
    with qtbot.waitSignal(p.eqChanged, timeout=10000):
        p.set_calibration(loaded(ctx, tmp_path))
    return p


@pytest.fixture
def clock():
    return [100.0]


@pytest.fixture
def view(panel, clock):
    v = panel.guided
    v._clock = lambda: clock[0]
    panel.start_guided_test(random.Random(5))
    return v


def started(view):
    view.begin()
    assert view.pages.currentWidget() is view.test_page
    return view


def hear_all(view, clock):
    for i in range(len(view._round.options)):
        view.select(i)
        clock[0] += 0.6
        view._account()


def loudness(hz, f0):
    return math.exp(-0.5 * (math.log2(hz / f0) / 0.15) ** 2)


def answer_like_a_listener(qtbot, view, clock):
    """Play if needed, listen to every option, then answer like an ear with resonances ``F0``."""
    if not view.playing:
        qtbot.waitUntil(lambda: view.ready, timeout=15000)
        view.play()
    if view.test.kind == SMOOTHNESS:
        qtbot.waitUntil(lambda: view.ready, timeout=15000)
    hear_all(view, clock)
    f0 = F0[view.test.side]
    options = view._round.options
    if view.test.kind == LOUDER:
        choice = int(np.argmax([loudness(hz, f0) for hz in options]))
    else:
        choice = int(np.argmin([abs(math.log(hz / f0)) for hz in options]))
    view.select(choice)
    view.answer_selected()


def test_find_it_for_me_replaces_step_one(panel, view):
    assert panel.find_button.text().startswith("Find it for me")
    assert panel.guided_active and panel.step1_stack.currentWidget() is view
    assert view.pages.currentWidget() is view.intro_page
    assert not panel.step2.isEnabled() and not panel.step3.isEnabled()
    assert view.intro_other_eq.text() == panel.other_eq.text()  # the same other-EQ warning
    started(view)
    assert view.title.text() == "Left ear · Round 1"
    assert view.stage_label.text() == "Louder test"
    assert [c.text() for c in view.cards[:2]] == ["Play A", "Play B"] and not view.cards[2].isVisibleTo(view)
    view.leave_for_manual()
    assert not panel.guided_active and panel.step2.isEnabled()
    assert panel.manual_box.isVisibleTo(panel)


def test_first_play_selects_a_and_switching_keeps_the_tone_going(view):
    started(view)
    engine = view.audio.engine
    view.play()
    assert view.selected == 0 and view.audio.playing == "tone"
    snap = engine.state.snapshot()
    assert snap.mode == Mode.SINE and snap.channel_mode == ChannelMode.LEFT
    assert snap.freq == pytest.approx(view._round.options[0])
    engine.process_block(480)
    view.select(1)
    assert view.audio.playing == "tone" and view.playing
    assert engine.state.snapshot().freq == pytest.approx(view._round.options[1])
    out = np.concatenate([engine.process_block(480)[:, 0] for _ in range(6)])
    assert np.min([np.abs(out[i:i + 240]).max() for i in range(0, len(out), 240)]) > 0  # no gap


def test_answers_unlock_only_once_every_option_was_heard(view, clock):
    started(view)
    assert not view.answer_button.isEnabled() and not view.same_button.isEnabled()
    assert view.listen_hint.text() == "Listen to each one first"
    view.play()
    clock[0] += 0.3
    view.select(1)                 # A for 0.3 s: not enough
    clock[0] += 0.6
    view.select(0)                 # B heard
    assert view.heard == {1} and not view.answer_button.isEnabled()
    clock[0] += 0.6
    view._account()
    assert view.heard == {0, 1}
    assert view.answer_button.isEnabled() and view.same_button.isEnabled()
    assert view.answer_button.text() == "Louder: A"
    view.select(1)
    assert view.answer_button.text() == "Louder: B"
    assert view.answer_button.accent == view.cards[1].accent


def test_listening_while_paused_does_not_count(view, clock):
    started(view)
    view.select(0)
    clock[0] += 5
    view.select(1)
    clock[0] += 5
    view._account()
    assert view.heard == set()


def test_option_order_is_random_and_no_frequency_is_shown(panel, view, clock):
    started(view)
    firsts = set()
    for seed in range(8):
        panel.start_guided_test(random.Random(seed))
        started(view)
        options = view._round.options
        firsts.add(options[0] < options[1])
        shown = " ".join([view.title.text(), view.stage_label.text(), view.instructions.text(),
                          view.answer_button.text(), view.same_button.text()]
                         + [c.text() for c in view.cards])
        assert "Hz" not in shown and not re.search(r"\d{3}", shown)
    assert firsts == {True, False}


def test_eq_is_off_for_tones_and_put_back_afterwards(panel, view, clock):
    view.audio.set_eq_enabled(True)
    started(view)
    assert not view.audio.eq_enabled
    view.leave_for_manual()
    assert view.audio.eq_enabled
    assert view.audio._eq is panel.channel_eq


def test_smoothness_check_swaps_the_eq_while_the_noise_plays(qtbot, view, clock):
    started(view)
    while view.test.kind == LOUDER:
        answer_like_a_listener(qtbot, view, clock)
    assert view.stage_label.text() == "Smoothness check"
    assert len(view._round.options) == 3 and view.cards[2].isVisibleTo(view)
    assert view.same_button.text() == "Can't tell them apart"
    qtbot.waitUntil(lambda: view.ready, timeout=15000)
    view.play()
    assert view.audio.playing == "pink" and view.audio.eq_enabled
    first = view.audio._eq
    assert first is view._eqs[0][0]
    view.select(2)
    assert view.audio.playing == "pink"
    assert view.audio._eq is view._eqs[2][0] and view.audio._eq is not first
    assert view.audio.engine._stereo_fir._pending is not None  # handed to the crossfading FIR
    eq = view._eqs[2][0]
    assert eq.channels["L"].peak_hz == pytest.approx(view._round.options[2])


def test_card_animation_is_blind_to_the_option(view, clock):
    started(view)
    engine = view.audio.engine
    levels = []
    for index in (0, 1):
        view.audio.switch_tone(view._round.options[index], "L")
        for _ in range(10):
            engine.process_block(480)
        levels.append(view.measure_level(engine.pop_recent_samples(1024)))
    assert levels[0] == pytest.approx(levels[1], abs=0.01)
    assert 0.5 < levels[0] <= 1.0
    a, b = OptionCard("A", "Play A"), OptionCard("B", "Play B")
    a.set_accent(QColor("#7c6cff"))
    b.set_accent(QColor("#f0a020"))
    a.set_level(levels[0], 3.2)
    b.set_level(levels[1], 3.2)
    for x, y in zip(a.shape, b.shape):
        np.testing.assert_allclose(x, y, atol=0.02)
    bars, line = visualizer_shape(0.8, 1.0)
    np.testing.assert_allclose(bars, visualizer_shape(0.8, 1.0)[0])
    assert np.all(bars <= 0.8 * 1.4) and np.all(visualizer_shape(0.0, 1.0)[0] == 0)


def test_selected_card_animates_only_while_playing(qtbot, view, clock):
    started(view)
    view.play()
    view.audio.engine.process_block(1024)
    view._tick()
    card = view.cards[0]
    assert card.selected and card.playing and card.shape is not None
    assert view._frame.isActive()
    view.toggle_play()
    assert not view._frame.isActive() and not card.playing and card.selected
    assert card.shape is not None  # frozen, not cleared


def test_space_and_stop_all_stop_the_test_sound(qtbot, view):
    started(view)
    view.play()
    view.audio.stop_all()
    assert not view.playing and view.transport.text() == "Play"
    view.play()
    view.transport.setFocus()
    qtbot.keyClick(view.transport, Qt.Key_Space)
    assert view.audio.playing is None and not view.playing


def test_keyboard_shortcuts(qtbot, panel, view, clock):
    from PySide6.QtWidgets import QApplication
    panel.show()
    qtbot.waitExposed(panel)
    QApplication.setActiveWindow(panel)  # offscreen windows aren't activated by themselves
    view.start_button.setFocus()
    qtbot.keyClick(view.start_button, Qt.Key_Return)
    assert view.pages.currentWidget() is view.test_page
    focus = view.transport
    focus.setFocus()
    qtbot.keyClick(focus, Qt.Key_P)
    assert view.playing and view.selected == 0
    qtbot.keyClick(focus, Qt.Key_2)
    assert view.selected == 1
    clock[0] += 0.6
    qtbot.keyClick(focus, Qt.Key_1)
    clock[0] += 0.6
    view._account()
    assert view.can_answer
    first = view._round
    qtbot.keyClick(focus, Qt.Key_S)
    assert view._round != first and view.test.round_number == 2
    hear_all(view, clock)
    second = view._round
    qtbot.keyClick(focus, Qt.Key_Return)
    assert view._round != second and view.test.round_number == 3
    qtbot.keyClick(focus, Qt.Key_Backspace)  # Back
    assert view._round == second
    qtbot.keyClick(focus, Qt.Key_P)
    assert not view.playing


def test_every_control_has_a_name(view):
    started(view)
    for widget in (view.transport, *view.cards, view.answer_button, view.same_button, view.progress):
        assert widget.accessibleName()
    for widget in (view.transport, view.cards[0], view.answer_button, view.same_button, view.back_button):
        assert widget.focusPolicy() & Qt.TabFocus


def test_back_undoes_the_last_answer(view, clock):
    started(view)
    assert not view.back_button.isEnabled()
    view.play()
    hear_all(view, clock)
    first = view._round
    view.answer_same()
    assert view.back_button.isEnabled()
    view.back()
    assert view._round == first and view.test.round_number == 1


def test_inconsistent_answers_offer_redo_or_by_hand(qtbot, view, clock):
    started(view)
    search = view.test.stage.search
    while search.searching:
        answer_like_a_listener(qtbot, view, clock)
    check = view._round
    assert check.check
    hear_all(view, clock)
    view.select(0)  # the pair is swapped: the first one is the earlier quieter tone
    view.answer_selected()
    assert view.pages.currentWidget() is view.unsure_page
    assert not view.playing
    view.redo_button.click()
    assert view.pages.currentWidget() is view.test_page and view.test.round_number == 1


def test_full_flow_finds_both_ears(qtbot, panel, view, clock):
    started(view)
    results = []
    view.resultFound.connect(lambda side, hz: results.append((side, hz)))
    for _ in range(60):
        if view.pages.currentWidget() is view.done_page:
            assert "resonance:" in view.result_label.text() and "Hz" in view.result_label.text()
            if view.test.status == "finished":
                break
            view.next_button.click()
            continue
        answer_like_a_listener(qtbot, view, clock)
    assert [side for side, _hz in results] == ["L", "R"]
    for side, hz in results:
        assert abs(hz / F0[side] - 1) < 0.03
        assert panel.session.primary[side] == pytest.approx(round(hz))
    assert view.progress.value == 1.0 or view.done_progress.value == 1.0
    assert panel.peak_slider.value() == round(results[-1][1])  # the right ear is selected now
    assert panel.graph.marker_hz() == pytest.approx(round(results[-1][1]), rel=1e-6)
    view.next_button.click()  # Done
    assert not panel.guided_active
    with qtbot.waitSignal(panel.eqChanged, timeout=10000):
        pass
    assert panel.channel_eq.channels["L"].peak_hz == pytest.approx(round(results[0][1]))
    assert panel.ctx.settings.get("tunings")[panel.loaded.calibration_id]["primary"]["R"] == round(results[1][1])


def test_eq_changes_wait_until_the_test_closes(qtbot, panel, view, clock):
    emitted = []
    panel.eqChanged.connect(emitted.append)
    started(view)
    panel.regenerate()
    assert emitted == []
    view.leave_for_manual()
    assert len(emitted) == 1


def test_stage_follows_light_and_dark(view):
    started(view)
    dark, light = QPalette(), QPalette()
    dark.setColor(QPalette.Window, QColor("#202020"))
    light.setColor(QPalette.Window, QColor("#f4f4f4"))
    assert stage_colors(dark)["bg"].lightness() < 20
    assert stage_colors(light)["bg"].lightness() > 200
    for palette in (dark, light):
        view.setPalette(palette)
        assert not view.grab().isNull()


def test_heard_threshold_is_half_a_second():
    assert guided_test_view.HEARD_SECONDS == pytest.approx(0.5)
