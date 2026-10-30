import numpy as np
import pytest

from resonancefit.loudness import GRID, a_weighting_db, heard_weights, level_offsets


def notch(centre, depth=8.0, freqs=GRID):
    return freqs, -depth * np.exp(-0.5 * (np.log2(freqs / centre) / 0.1) ** 2)


def test_a_weighting_matches_the_standard():
    np.testing.assert_allclose(a_weighting_db([100, 1000, 10000]), [-19.1, 0.0, -2.5], atol=0.1)


@pytest.mark.parametrize("sound", ["pink", "grey", "region", "focus"])
def test_same_curves_need_nothing(sound):
    assert level_offsets([notch(7000), notch(7000)], sound, 7000) == pytest.approx([0.0, 0.0])


@pytest.mark.parametrize("sound", ["pink", "grey", "region", "focus"])
def test_a_louder_card_is_turned_down_by_exactly_its_extra_level(sound):
    f, g = notch(7000)
    assert level_offsets([(f, g), (f, g + 4.0)], sound, 7000) == pytest.approx([0.0, -4.0])


@pytest.mark.parametrize("sound", ["pink", "grey", "region", "focus"])
def test_matched_cards_come_out_equally_loud_and_never_boosted(sound):
    curves = [notch(c, depth=d) for c, d in ((5500, 10), (7000, 4), (8500, 12), (10000, 6))]
    curves[1] = (curves[1][0], curves[1][1] - 1.5)  # a lower preamp, too
    offsets = level_offsets(curves, sound, 7500)
    assert max(offsets) == pytest.approx(0.0) and all(o <= 1e-9 for o in offsets)
    again = level_offsets([(f, g + o) for (f, g), o in zip(curves, offsets)], sound, 7500)
    assert again == pytest.approx([0.0] * 4, abs=1e-6)


def test_focused_sounds_only_count_what_they_play():
    assert heard_weights("region", 7000)[GRID < 2000].max() < 1e-12
    assert heard_weights("focus", 7000)[(GRID < 4500) | (GRID > 11000)].max() == 0
    # a notch far outside the focused band changes nothing there
    flat = (GRID, np.zeros_like(GRID))
    assert level_offsets([flat, notch(1000, depth=20)], "region", 7000) == pytest.approx([0.0, 0.0], abs=1e-6)
    assert level_offsets([flat, notch(1000, depth=20)], "pink", 7000)[0] < -0.1  # pink does hear it
