"""The guided test's loudness match page: the pair of bands, the slider, and the flow into the smoothness check."""

import random

import numpy as np
import pytest

from eq_helper.audio.generators import AlternatingBandsGenerator
from resonancefit.guided_test import SHAPE, SMOOTHNESS
from resonancefit.shape_match import band_level

from .test_guided_test_view import F0, answer_like_a_listener, clock, panel  # noqa: F401 - fixtures and helper
from .test_tuning_panel import ctx  # noqa: F401 - fixture


@pytest.fixture
def view(qtbot, panel, clock):  # noqa: F811
    v = panel.guided
    v._clock = lambda: clock[0]
    panel.start_guided_test(random.Random(5))
    qtbot.waitUntil(lambda: not v._preparing, timeout=30000)
    return v


def heard_db(panel, side, hz):
    """What the simulated ear (resonance at ``F0[side]``) hears in a band: the model's own prediction."""
    model = panel.session.measurements.models[side]
    return band_level(model.freqs, model.predict(F0[side]), hz)


def match_like_a_listener(panel, view, clock):
    view.play_match()
    clock[0] += 2.5
    view._tick()
    a, b = view.match_bands
    side = view.test.side
    view.match_slider.setValue(int(round((heard_db(panel, side, a) - heard_db(panel, side, b)) * 4)))
    view.answer_match()


def test_the_test_starts_with_the_loudness_match_once_the_model_is_ready(view):
    assert view.test.kind == SHAPE
    assert "match how loud" in view.intro_steps.text()
    view.begin()
    assert view.pages.currentWidget() is view.match_page
    assert view.match_title.text() == "Left ear · Match 1"
    assert not view.match_button.isEnabled() and "Play them first" in view.match_hint.text()


def test_the_pair_plays_in_turn_and_the_slider_moves_band_b_live(view, clock):
    view.begin()
    view.play_match()
    assert view.audio.playing == "pair" and view.playing
    engine = view.audio.engine
    engine.process_block(480)
    pair = engine._voices[-1][0]
    assert isinstance(pair, AlternatingBandsGenerator) and pair is view._pair
    assert {pair.a_hz, pair.b_hz} == {view._match.reference, view._match.adjust}
    view.match_slider.setValue(view.match_slider.value() + 8)
    assert pair.gain_db == pytest.approx(view.match_gain_db)
    view.set_match_mode("b")
    assert pair.mode == "b" and view.match_mode_buttons["b"].isChecked()
    clock[0] += 2.5
    view._tick()
    assert view.match_button.isEnabled()
    view.toggle_match_play()
    assert not view.playing and view.audio.playing is None


def test_the_answer_is_the_level_difference_whichever_band_the_slider_moves(view, clock):
    view.begin()
    for swap, expected in ((False, -3.0), (True, 3.0)):
        view._match_swap = swap
        view.play_match()
        clock[0] += 2.5
        view._tick()
        view.match_slider.setValue(12)  # B up 3 dB
        answers = []
        view.test.answer = answers.append  # what the search would be told
        view.test.status = "testing"
        try:
            view._answer = lambda v: answers.append(v)
            view.answer_match()
        finally:
            del view._answer
            del view.test.answer
        assert answers == [pytest.approx(expected)]


def test_matches_then_fine_smoothness_find_both_ears(qtbot, panel, view, clock):
    view.begin()
    found = {}
    panel.guided.resultFound.connect(lambda side, hz: found.__setitem__(side, hz))
    for _ in range(80):
        if view.pages.currentWidget() is view.done_page:
            if view.test.status != "testing":
                break
            view.next_ear()
            continue
        if view.test.kind == SHAPE:
            match_like_a_listener(panel, view, clock)
        else:
            assert view.test.kind == SMOOTHNESS and view.pages.currentWidget() is view.test_page
            answer_like_a_listener(qtbot, view, clock)
    assert set(found) == {"L", "R"}
    for side, hz in found.items():
        assert abs(np.log2(hz / F0[side])) < 0.03
    shape = [s for s in view.test.stages if s.kind == SHAPE]
    assert len(shape[1].search.answers) <= len(shape[0].search.answers) + 2  # the right ear starts from the left
    assert "loudness matches" in view.result_note.text()


def test_back_from_the_first_smoothness_round_returns_to_the_match_page(qtbot, panel, view, clock):
    view.begin()
    while view.test.kind == SHAPE:
        match_like_a_listener(panel, view, clock)
    assert view.pages.currentWidget() is view.test_page
    view.back()
    assert view.test.kind == SHAPE and view.pages.currentWidget() is view.match_page
