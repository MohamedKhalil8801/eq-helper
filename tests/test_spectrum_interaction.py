"""Unit tests for the pure spectrum-interaction decision logic.

These functions encode the unified click model:
  * plain left-click  -> always play a sine wave
  * Ctrl + left-click -> create a peak filter, or grab the nearest existing one

and the Ctrl-hover affordance (ghost dot vs. highlighting an existing dot).
They are intentionally Qt-free so the rules can be tested in isolation.
"""

from __future__ import annotations

from eq_helper.ui.spectrum_interaction import classify_press, compute_ctrl_hint


class TestClassifyPress:
    def test_plain_click_empty_area_plays_sine(self) -> None:
        assert classify_press(ctrl_held=False, shift_held=False, near_dot=False, on_interactive=False) == "sine"

    def test_plain_click_over_a_dot_still_plays_sine(self) -> None:
        # The whole point of the redesign: a plain click never moves a filter.
        assert classify_press(ctrl_held=False, shift_held=False, near_dot=True, on_interactive=False) == "sine"

    def test_plain_click_on_interactive_overlay_passes_through(self) -> None:
        # Legend / band centre-line keep their own click handling.
        assert classify_press(ctrl_held=False, shift_held=False, near_dot=False, on_interactive=True) == "pass"

    def test_ctrl_click_empty_area_creates_filter(self) -> None:
        assert classify_press(ctrl_held=True, shift_held=False, near_dot=False, on_interactive=False) == "create"

    def test_ctrl_click_near_dot_grabs_it(self) -> None:
        assert classify_press(ctrl_held=True, shift_held=False, near_dot=True, on_interactive=False) == "grab"

    def test_ctrl_overrides_interactive_overlays(self) -> None:
        # Ctrl is a deliberate EQ gesture; it wins over legend/band hits.
        assert classify_press(ctrl_held=True, shift_held=False, near_dot=True, on_interactive=True) == "grab"
        assert classify_press(ctrl_held=True, shift_held=False, near_dot=False, on_interactive=True) == "create"

    def test_shift_click_starts_focus_sweep(self) -> None:
        assert classify_press(ctrl_held=False, shift_held=True, near_dot=False, on_interactive=False) == "focus_sweep"

    def test_shift_focus_sweep_ignores_dots_and_overlays(self) -> None:
        # Focus sweep centres on the clicked frequency regardless of what's there.
        assert classify_press(ctrl_held=False, shift_held=True, near_dot=True, on_interactive=False) == "focus_sweep"
        assert classify_press(ctrl_held=False, shift_held=True, near_dot=False, on_interactive=True) == "focus_sweep"

    def test_ctrl_takes_precedence_over_shift(self) -> None:
        # If both are held, the filter gesture (Ctrl) wins.
        assert classify_press(ctrl_held=True, shift_held=True, near_dot=False, on_interactive=False) == "create"
        assert classify_press(ctrl_held=True, shift_held=True, near_dot=True, on_interactive=False) == "grab"


class TestComputeCtrlHint:
    def test_no_hint_without_ctrl(self) -> None:
        assert compute_ctrl_hint(ctrl_held=False, cursor_in_plot=True, near_dot_index=-1) == (False, -1)
        assert compute_ctrl_hint(ctrl_held=False, cursor_in_plot=True, near_dot_index=3) == (False, -1)

    def test_no_hint_when_cursor_outside_plot(self) -> None:
        assert compute_ctrl_hint(ctrl_held=True, cursor_in_plot=False, near_dot_index=-1) == (False, -1)

    def test_ghost_shown_in_empty_area(self) -> None:
        assert compute_ctrl_hint(ctrl_held=True, cursor_in_plot=True, near_dot_index=-1) == (True, -1)

    def test_existing_dot_highlighted(self) -> None:
        assert compute_ctrl_hint(ctrl_held=True, cursor_in_plot=True, near_dot_index=2) == (False, 2)
