"""The guided test in step ①: listening, answering, blindness, the EQ preview and the full flow."""

import math
import random
import re

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from eq_helper.audio.shared_state import ChannelMode, Mode
from eq_helper.audio.generators import BandNoiseGenerator
from resonancefit.audio import AudioService
from resonancefit.guided_test import FIRST_SMOOTH_DELTA, LOUDER, OVERVIEW, PICK, SMOOTH_DELTA, SMOOTHNESS, UPPER
from resonancefit.ui import guided_test_view
from resonancefit.ui.guided_widgets import OptionCard, stage_colors, visualizer_shape
from resonancefit.ui.tuning_panel import TuningPanel

from .test_tuning_panel import ctx, loaded  # noqa: F401 - fixture and helper

F0 = {"L": 7100.0, "R": 8300.0}
UPPER_OFF = 1.05  # the simulated ear's upper peak: this far above the calibration's prediction


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
    v.tables = None  # the scorecard's own tests below put it back
    panel.start_guided_test(random.Random(5))
    v.start(panel.session.sides, {}, random.Random(5))  # no starting point: the louder test comes first
    return v


def with_tables(qtbot, panel, view, seed=5):
    """Start the test as the app does: the scorecard's tables built on a worker first."""
    view.tables = panel._scorecard_tables
    panel.start_guided_test(random.Random(seed))
    qtbot.waitUntil(lambda: not view._preparing, timeout=30000)
    return started(view)


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
    if view.test.kind in (SMOOTHNESS, PICK):
        qtbot.waitUntil(lambda: view.ready, timeout=15000)
    hear_all(view, clock)
    side = view.test.side
    f0 = view.test.predicted_upper[side] * UPPER_OFF if view.test.target == UPPER else F0[side]
    options = view._round.options
    if view.test.kind == LOUDER:
        choice = int(np.argmax([loudness(hz, f0) for hz in options]))
    else:
        choice = int(np.argmin([abs(math.log(hz / f0)) for hz in options]))
    if view.test.overview:  # cross out the far half
        for i in sorted(range(len(options)), key=lambda i: abs(math.log(options[i] / f0)))[len(options) // 2:]:
            view.toggle_rule_out(i)
    view.select(choice)
    view.answer_selected()


def test_find_it_for_me_replaces_step_one(panel, view):
    assert panel.find_button.text().startswith("Find it for me")
    assert panel.guided_active and panel.step1_stack.currentWidget() is view
    assert view.pages.currentWidget() is view.intro_page
    assert not panel.power_button.isVisibleTo(panel)
    assert view.intro_other_eq.text() == panel.other_eq.text()  # the same other-EQ warning
    started(view)
    assert view.title.text() == "Left ear · Round 1"
    assert view.stage_label.text() == "Louder test"
    assert [c.text() for c in view.cards[:2]] == ["1", "2"] and not view.cards[2].isVisibleTo(view)
    view.leave_for_manual()
    assert not panel.guided_active and panel.power_button.isVisibleTo(panel)
    assert panel.manual_box.isVisibleTo(panel)


def test_each_ear_is_an_overview_of_eight_then_pairs(qtbot, panel, view, clock):
    with_tables(qtbot, panel, view)
    assert view.test.kind == PICK and view.test.overview
    assert view.stage_label.text() == "Smoothness check: overview"
    assert sum(card.isVisibleTo(view) for card in view.cards) == 8 and view.rule_out_button.isVisibleTo(view)
    assert view.cards_grid.getItemPosition(view.cards_grid.indexOf(view.cards[7]))[:2] == (1, 3)  # two rows
    qtbot.waitUntil(lambda: view.ready, timeout=30000)
    hear_all(view, clock)
    options = view._round.options
    order = sorted(range(8), key=lambda i: abs(math.log(options[i] / F0["L"])))
    for i in order[4:]:
        view.toggle_rule_out(i)
    assert view.ruled_out == set(order[4:]) and view.cards[order[7]].ruled_out
    view.select(order[7])
    assert not view.answer_button.isEnabled()  # a crossed-out card can't be the pick
    assert view.rule_out_button.text() == "Bring back"
    view.select(order[0])
    view.answer_selected()
    shown, best, out = view.test.stage.search.answers[0]
    assert best == order[0] and out == frozenset(order[4:])
    assert view.test.kind == PICK and not view.test.overview
    assert sum(card.isVisibleTo(view) for card in view.cards) == 2 and not view.rule_out_button.isVisibleTo(view)
    assert not view.cards[0].ruled_out
    while view.test.side == "L":
        answer_like_a_listener(qtbot, view, clock)
    assert view.pages.currentWidget() is view.done_page
    assert "overview of eight" in view.result_note.text()
    assert abs(view.test.results["L"] / F0["L"] - 1) < 0.02
    view.next_button.click()
    assert view.test.kind == PICK and view.test.overview
    assert view.test.stage.search.start == pytest.approx(view.test.results["L"])  # the right ear starts there


def test_start_waits_for_the_tables(qtbot, panel, view, clock):
    view.tables = panel._scorecard_tables
    panel.start_guided_test(random.Random(5))
    if view._preparing:
        view.begin()
        assert view.start_button.text() == "Getting ready…" and not view.start_button.isEnabled()
        assert view.pages.currentWidget() is view.intro_page
    qtbot.waitUntil(lambda: view.pages.currentWidget() is view.test_page, timeout=30000)
    assert view.test.kind == PICK


def test_with_the_calibration_but_no_tables_each_ear_is_the_smoothness_check_alone(qtbot, panel, view, clock):
    panel.start_guided_test(random.Random(5))
    started(view)
    assert view.test.kind == SMOOTHNESS and view.stage_label.text() == "Smoothness check"
    assert view.test.stage.search.delta == FIRST_SMOOTH_DELTA
    while view.test.side == "L":
        answer_like_a_listener(qtbot, view, clock)
    assert view.pages.currentWidget() is view.done_page
    assert "smoothness check" in view.result_note.text() and "louder" not in view.result_note.text()
    view.next_button.click()
    assert view.test.kind == SMOOTHNESS and view.test.stage.search.delta == SMOOTH_DELTA
    assert view.test.stage.search.center == pytest.approx(view.test.results["L"])


def test_first_play_selects_a_and_switching_keeps_the_tone_going(view):
    started(view)
    engine = view.audio.engine
    view.play()
    assert view.selected == 0 and view.audio.playing == "band"
    snap = engine.state.snapshot()
    assert snap.mode == Mode.SINE and snap.channel_mode == ChannelMode.LEFT
    assert snap.freq == pytest.approx(view._round.options[0])
    engine.process_block(480)
    voice = engine._voices[-1][0]
    assert isinstance(voice, BandNoiseGenerator) and voice.width_oct == pytest.approx(view._round.band)
    assert voice.center_hz == pytest.approx(view._round.options[0])
    engine.process_block(480)
    view.select(1)
    assert view.audio.playing == "band" and view.playing
    assert engine.state.snapshot().freq == pytest.approx(view._round.options[1])
    out = np.concatenate([engine.process_block(480)[:, 0] for _ in range(6)])
    assert np.min([np.abs(out[i:i + 240]).max() for i in range(0, len(out), 240)]) > 0  # no gap


def test_answers_are_open_at_once_and_heard_cards_get_a_tick(view, clock):
    started(view)
    assert not view.answer_button.isEnabled() and view.same_button.isEnabled()  # nothing selected yet
    view.play()
    assert view.answer_button.isEnabled()  # no need to hear every card first
    clock[0] += 0.3
    view.select(1)                 # 1 for 0.3 s: not a tick yet
    clock[0] += 0.6
    view.select(0)                 # 2 heard
    assert view.heard == {1} and view.cards[1].heard and not view.cards[0].heard
    clock[0] += 0.6
    view._account()
    assert view.heard == {0, 1}
    assert view.answer_button.text() == "Louder: 1"
    view.select(1)
    assert view.answer_button.text() == "Louder: 2"
    assert view.answer_button.accent == view.cards[1].accent


def test_clicking_a_card_starts_playing_it(view, clock):
    started(view)
    assert not view.playing
    view.cards[1].click()
    assert view.playing and view.selected == 1 and view.audio.playing == "band"
    assert view.audio.engine.state.snapshot().freq == pytest.approx(view._round.options[1])


def test_listening_while_paused_does_not_count(view, clock):
    started(view)
    view.select(0)
    view.toggle_play()  # pause
    clock[0] += 5
    view._account()
    assert view.heard == set() and not view.playing


def test_clicking_a_card_in_the_smoothness_check_plays_once_the_eqs_are_ready(qtbot, view, clock):
    started(view)
    while view.test.kind == LOUDER:
        answer_like_a_listener(qtbot, view, clock)
    assert not view.playing
    view.cards[2].click()
    qtbot.waitUntil(lambda: view.playing, timeout=15000)
    assert view.audio.playing == "pink" and view.audio._eq is view._eqs[2][0]


def test_louder_pairs_are_shuffled_smoothness_cards_in_order_and_no_frequency_is_shown(panel, view, clock):
    started(view)
    firsts = set()
    for seed in range(8):
        view.start(panel.session.sides, {}, random.Random(seed))  # the louder test
        started(view)
        options = view._round.options
        firsts.add(options[0] < options[1])
        shown = " ".join([view.title.text(), view.stage_label.text(), view.instructions.text(),
                          view.answer_button.text(), view.same_button.text()]
                         + [c.text() for c in view.cards])
        assert "Hz" not in shown and not re.search(r"\d{3}", shown)
    assert firsts == {True, False}
    panel.start_guided_test(random.Random(1))  # the smoothness check: low to high
    started(view)
    assert list(view._round.options) == sorted(view._round.options)


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
        view.audio.switch_band(view._round.options[index], view._round.band, "L")
        for _ in range(40):
            engine.process_block(480)
        levels.append(view.measure_level(engine.pop_recent_samples(16384)))
    assert levels[0] == pytest.approx(levels[1], abs=0.05)  # equal-RMS bands (noise wobbles a little)
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


def reach_unsure(panel, view, clock):
    """Answer like an ear whose peak jumps between two places until the louder test is unsure."""
    for seed in range(100):
        view.start(panel.session.sides, {}, random.Random(seed))
        started(view)
        rng = random.Random(seed)
        while view.pages.currentWidget() is view.test_page and view.test.kind == LOUDER:
            view.play() if not view.playing else None
            hear_all(view, clock)
            f0 = 6400 if rng.random() < 0.5 else 8600
            view.select(int(np.argmax([loudness(hz, f0) for hz in view._round.options])))
            view.answer_selected()
        if view.pages.currentWidget() is view.unsure_page:
            return
    raise AssertionError("never unsure")


def test_unsure_answers_offer_redo_use_anyway_or_by_hand(qtbot, panel, view, clock):
    reach_unsure(panel, view, clock)
    assert not view.playing
    assert view.use_anyway_button.isVisibleTo(view) and view.unsure_stop.isVisibleTo(view)
    view.redo_button.click()
    assert view.pages.currentWidget() is view.test_page and view.test.round_number == 1
    reach_unsure(panel, view, clock)
    estimate = view.test.stage.search.estimate()
    view.use_anyway_button.click()
    assert view.test.kind == SMOOTHNESS and view.test.stage.search.center == pytest.approx(estimate)
    assert view.stage_label.text() == "Smoothness check"


def test_leaving_from_the_unsure_page_keeps_the_estimate(panel, view, clock):
    reach_unsure(panel, view, clock)
    estimate = view.test.stage.search.estimate()
    view.unsure_stop.click()
    assert not panel.guided_active
    if view.test.stage.search.spread <= 1.15:
        assert panel.session.primary["L"] == pytest.approx(round(estimate))


def test_leaving_during_the_louder_test_keeps_its_estimate(qtbot, panel, view, clock):
    started(view)
    for _ in range(4):
        answer_like_a_listener(qtbot, view, clock)
    assert view.test.kind == LOUDER
    view.stop_button.click()
    assert abs(panel.session.primary["L"] / F0["L"] - 1) < 0.05


def test_full_flow_finds_both_ears(qtbot, panel, view, clock):
    started(view)
    results = []
    view.resultFound.connect(lambda side, hz: results.append((side, hz)))
    for _ in range(60):
        if view.pages.currentWidget() is view.done_page:
            assert "resonance:" in view.result_label.text() and "Hz" in view.result_label.text()
            if view.test.status == "upper_offer":
                break
            view.next_button.click()
            continue
        answer_like_a_listener(qtbot, view, clock)
    assert view.next_button.text() == "Next"
    view.next_button.click()
    assert view.pages.currentWidget() is view.upper_page
    assert "Left resonance" in view.upper_summary.text()
    assert [side for side, _hz in results] == ["L", "R"]
    for side, hz in results:
        assert abs(hz / F0[side] - 1) < 0.03
        assert panel.session.primary[side] == pytest.approx(round(hz))
    assert view.progress.value == 1.0 or view.done_progress.value == 1.0
    assert panel.peak_slider.value() == round(results[-1][1])  # the right ear is selected now
    assert panel.graph.marker_hz() == pytest.approx(round(results[-1][1]), rel=1e-6)
    view.skip_upper_button.click()  # no balance check: the test closes
    assert not panel.guided_active
    assert not panel.session.upper_enabled
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


def finish_primary(qtbot, view, clock, predict=True):
    if predict:  # the test calibration has no upper peak: predict one at twice the resonance
        view.upper_prediction = lambda _side, hz: 2.0 * hz
    started(view)
    for _ in range(60):
        if view.pages.currentWidget() is view.done_page:
            view.next_button.click()
            if view.pages.currentWidget() is view.upper_page:
                return
            continue
        answer_like_a_listener(qtbot, view, clock)
    raise AssertionError("didn't reach the upper-treble offer")


def test_optional_upper_treble_after_both_ears(qtbot, panel, view, clock):
    finish_primary(qtbot, view, clock)
    upper = []
    view.upperFound.connect(lambda side, hz: upper.append((side, hz)))
    view.upper_button.click()
    assert view.pages.currentWidget() is view.test_page
    assert view.test.kind == OVERVIEW  # no louder test or audibility question up there: eight EQs
    assert view.stage_label.text() == "Upper treble: overview" and view.rule_out_button.isVisibleTo(view)
    assert view.noise_button.isChecked()  # the sound the listener was using: nothing switches by itself
    predicted = dict(view.test.predicted_upper)
    for side in "LR":
        assert predicted[side] == pytest.approx(2.0 * view.test.results[side])
    assert sum(c.isVisibleTo(view) for c in view.cards) == 8
    assert list(view._round.options) == sorted(view._round.options)
    for _ in range(60):
        if view.pages.currentWidget() is view.done_page:
            assert "upper treble:" in view.result_label.text()
            if view.test.status == "finished":
                break
            view.next_button.click()
            continue
        if view.test.kind == SMOOTHNESS:
            assert view.stage_label.text() == "Upper treble: smoothness check"
            assert view.noise_button.isChecked()
        answer_like_a_listener(qtbot, view, clock)
    assert [side for side, _hz in upper] == ["L", "R"]
    for side, hz in upper:
        assert abs(hz / (predicted[side] * UPPER_OFF) - 1) < 0.03
        assert panel.session.upper[side] == pytest.approx(round(hz))
    assert panel.session.upper_enabled and panel.upper_manual.isChecked()
    assert view.next_button.text() == "Done"
    view.next_button.click()
    assert not panel.guided_active
    with qtbot.waitSignal(panel.eqChanged, timeout=10000):
        pass
    assert panel.channel_eq.personal_treble_enabled


def test_smoothness_options_for_the_upper_treble_move_only_the_upper_resonance(qtbot, panel, view, clock):
    finish_primary(qtbot, view, clock)
    view.upper_button.click()
    assert view.test.kind == OVERVIEW
    qtbot.waitUntil(lambda: view.ready, timeout=15000)
    primary = dict(panel.session.primary)
    lo, hi = view.test.upper_window("L")
    for index, hz in enumerate(view._round.options):
        assert lo <= hz <= hi
        eq = view._eqs[index][0]
        assert eq.personal_treble_enabled
        assert eq.channels["L"].peak_hz == pytest.approx(primary["L"])


def test_cant_tell_the_upper_overview_apart_keeps_it_as_calibrated(qtbot, panel, view, clock):
    finish_primary(qtbot, view, clock)
    view.upper_button.click()
    qtbot.waitUntil(lambda: view.ready, timeout=15000)
    view.play()
    hear_all(view, clock)
    assert view.same_button.text() == "Can't tell them apart"
    view.answer_same()
    assert view.pages.currentWidget() is view.done_page
    assert "as calibrated" in view.result_label.text()
    assert not panel.session.upper_enabled and panel.session.upper == {}
    view.next_button.click()
    assert view.test.kind == OVERVIEW and view.test.side == "R"


def test_no_predicted_upper_peak_goes_straight_on(qtbot, panel, view, clock):
    """A calibration without an upper peak: nothing to fine-tune up there."""
    assert panel._upper_prediction("L", 7000.0) is None  # the test calibration has none
    finish_primary(qtbot, view, clock, predict=False)
    view.upper_button.click()
    assert view.test.status == "finished" and view.test.upper_results == {"L": None, "R": None}
    assert not panel.guided_active


def reach_smoothness(qtbot, view, clock):
    started(view)
    while view.test.kind == LOUDER:
        answer_like_a_listener(qtbot, view, clock)
    qtbot.waitUntil(lambda: view.ready, timeout=15000)


def test_smoothness_check_plays_noise_or_a_sweep_through_the_same_eq(qtbot, view, clock):
    reach_smoothness(qtbot, view, clock)
    assert view.sound_row.isVisibleTo(view) and view.noise_button.isChecked()
    view.cards[1].click()
    assert view.audio.playing == "pink"
    eq = view.audio._eq
    view.sweep_button.click()
    assert view.playing and view.audio.playing == "focus" and view.audio._eq is eq
    snap = view.audio.engine.state.snapshot()
    assert snap.mode == Mode.SWEEP and snap.channel_mode == ChannelMode.LEFT
    low, mid, high = sorted(view._round.options)
    assert snap.sweep_f_start < low and snap.sweep_f_end > high  # the sweep spans all three options
    sweep = view.audio.engine._sweep
    view.cards[2].click()  # another EQ: the sweep keeps going, only the EQ changes
    assert view.audio.playing == "focus" and view.audio.engine._sweep is sweep
    assert view.audio._eq is view._eqs[2][0]
    view.noise_button.click()
    assert view.audio.playing == "pink" and view.audio._eq is view._eqs[2][0]
    view.grey_button.click()  # grey noise: same EQ, same ear
    assert view.audio.playing == "grey" and view.audio._eq is view._eqs[2][0]
    snap = view.audio.engine.state.snapshot()
    assert snap.mode == Mode.GREY and snap.channel_mode == ChannelMode.LEFT
    assert view.grey_button.isChecked() and not view.noise_button.isChecked()
    view.cards[0].click()
    assert view.audio.playing == "grey" and view.audio._eq is view._eqs[0][0]
    view.set_stage2_sound("pink")  # the N key
    assert view.audio.playing == "pink"


def test_focused_noise_is_grey_noise_around_the_round_and_follows_it(qtbot, view, clock):
    reach_smoothness(qtbot, view, clock)
    view.cards[1].click()
    view.region_button.click()
    assert view.audio.playing == "region" and view.region_button.isChecked()
    assert view.audio._eq is view._eqs[1][0]
    snap = view.audio.engine.state.snapshot()
    assert snap.mode == Mode.GREY and snap.channel_mode == ChannelMode.LEFT
    focused = view.audio.engine._grey_buf
    assert focused is not view.audio.engine._grey_full
    view.cards[2].click()  # another EQ: the same noise keeps going
    assert view.audio.engine._grey_buf is focused
    hear_all(view, clock)
    view.select(0)
    view.answer_selected()  # the next round: centred on its options
    if view.test.kind == SMOOTHNESS and view.test.side == "L":
        qtbot.waitUntil(lambda: view.ready, timeout=15000)
        assert view.audio.playing == "region" and view.audio.engine._grey_buf is not focused


def test_listening_with_either_sound_counts_as_heard(qtbot, view, clock):
    reach_smoothness(qtbot, view, clock)
    view.select(0)
    clock[0] += 0.6
    view.set_stage2_sound("focus")
    view.select(1)
    clock[0] += 0.6
    view.select(2)
    clock[0] += 0.6
    view._account()
    assert view.heard == {0, 1, 2} and view.can_answer


def test_the_sweep_choice_stays_for_later_rounds_and_follows_them(qtbot, view, clock):
    reach_smoothness(qtbot, view, clock)
    view.select(0)
    view.set_stage2_sound("focus")
    hear_all(view, clock)
    view.answer_same()  # "can't tell": the left ear is done
    while view.pages.currentWidget() is view.done_page:
        view.next_button.click()
    while view.test.kind == LOUDER:
        assert not view.sound_row.isVisibleTo(view)
        answer_like_a_listener(qtbot, view, clock)
    assert view.sweep_button.isChecked()
    qtbot.waitUntil(lambda: view.ready, timeout=15000)
    view.cards[0].click()
    assert view.audio.playing == "focus"
    low, _mid, high = sorted(view._round.options)
    snap = view.audio.engine.state.snapshot()
    assert snap.sweep_f_start < low and snap.sweep_f_end > high and snap.channel_mode == ChannelMode.RIGHT


def test_sweep_keys(qtbot, panel, view, clock):
    from PySide6.QtWidgets import QApplication
    panel.show()
    qtbot.waitExposed(panel)
    QApplication.setActiveWindow(panel)
    reach_smoothness(qtbot, view, clock)
    view.transport.setFocus()
    qtbot.keyClick(view.transport, Qt.Key_1)
    qtbot.keyClick(view.transport, Qt.Key_W)
    assert view.audio.playing == "focus"
    qtbot.keyClick(view.transport, Qt.Key_N)
    assert view.audio.playing == "pink"


def test_sweep_animation_is_blind_too(qtbot, view, clock):
    reach_smoothness(qtbot, view, clock)
    view.select(0)
    view.set_stage2_sound("focus")
    engine = view.audio.engine
    levels = []
    for index in range(3):
        view.select(index)
        for _ in range(12):
            engine.process_block(480)
        levels.append(view.measure_level(engine.pop_recent_samples(1024)))
    assert max(levels) - min(levels) < 0.02  # the level fed to the cards ignores the EQ


def test_cards_are_level_matched(qtbot, panel, view, clock):
    """A gain after each card's EQ makes it as loud as the quietest card, for the sound playing."""
    with_tables(qtbot, panel, view)
    assert view.test.kind == PICK
    qtbot.waitUntil(lambda: view.ready, timeout=15000)
    eqs = [view._eqs[i][0] for i in range(len(view._round.options))]
    offsets = [view._offsets[i] for i in range(len(eqs))]
    assert max(offsets) == pytest.approx(0.0) and min(offsets) < -0.01 and all(o <= 0 for o in offsets)
    engine = view.audio.engine
    for i in (0, len(eqs) - 1):
        view.select(i)
        assert engine._lr_target == pytest.approx((10 ** (offsets[i] / 20),) * 2)
    view.set_stage2_sound("region")  # another sound: matched again for what it plays
    assert view._offsets != dict(enumerate(offsets))
    assert engine._lr_target == pytest.approx((10 ** (view._offsets[len(eqs) - 1] / 20),) * 2)
    view.stop()
    assert engine._lr_target == (1.0, 1.0)
