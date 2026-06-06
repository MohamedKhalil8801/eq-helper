"""Pure decision logic for the unified spectrum interaction model.

Kept free of Qt imports so the rules can be unit-tested in isolation. The
spectrum view wires these to real mouse/keyboard events.

Click model:
  * plain left-click  -> always play a sine wave at the cursor frequency
  * Ctrl + left-click -> grab the nearest existing peak filter, or create one

Ctrl-hover affordance: while Ctrl is held the cursor shows what a click would
do -- a translucent "ghost" dot where a new filter would land, or a highlight
on the existing dot that a click would grab.
"""

from __future__ import annotations


def classify_press(
    *, ctrl_held: bool, shift_held: bool, near_dot: bool, on_interactive: bool
) -> str:
    """Decide what a left-button press should do.

    Returns one of:
      * ``"sine"``        -- play a sine wave (plain click on the plot)
      * ``"create"``      -- create a new peak filter at the cursor (Ctrl, empty)
      * ``"grab"``        -- drag the nearest existing filter (Ctrl, near a dot)
      * ``"focus_sweep"`` -- start a focused sweep around the cursor (Shift)
      * ``"pass"``        -- defer to pyqtgraph's default handler (legend/band line)

    Precedence: Ctrl (filter ops) > Shift (focus sweep) > interactive overlay >
    plain sine. Ctrl and Shift are deliberate gestures and override overlays.
    """
    if ctrl_held:
        return "grab" if near_dot else "create"
    if shift_held:
        return "focus_sweep"
    if on_interactive:
        return "pass"
    return "sine"


def compute_ctrl_hint(
    *, ctrl_held: bool, cursor_in_plot: bool, near_dot_index: int
) -> tuple[bool, int]:
    """Decide the Ctrl-hover hint state.

    Returns ``(show_ghost, highlight_index)``:
      * ``(False, -1)`` -- no hint (Ctrl not held, or cursor outside the plot)
      * ``(False, i)``  -- highlight existing dot ``i`` ("click moves this")
      * ``(True, -1)``  -- show the ghost dot ("click creates a filter here")
    """
    if not (ctrl_held and cursor_in_plot):
        return (False, -1)
    if near_dot_index >= 0:
        return (False, near_dot_index)
    return (True, -1)
