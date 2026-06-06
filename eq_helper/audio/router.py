"""Mono-to-stereo channel routing.

Implements the routing table from the plan:

    channel_mode | force_mono | L out    | R out
    LEFT         | any        | block    | 0
    RIGHT        | any        | 0        | block
    BOTH         | True       | block    | block
    BOTH         | False      | block_L  | block_R   (if decorrelated input)
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from eq_helper.audio.shared_state import ChannelMode


def route(
    mono_block: np.ndarray,
    channel_mode: ChannelMode,
    force_mono: bool,
    right_block: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Return an ``(N, 2)`` float32 stereo block for the audio output.

    When ``channel_mode`` is ``BOTH`` and ``force_mono`` is False, the caller
    may pass a ``right_block`` with decorrelated content (used by pink noise);
    otherwise the mono block is duplicated on both channels.
    """
    n = mono_block.shape[0]
    out = np.zeros((n, 2), dtype=np.float32)
    if channel_mode == ChannelMode.LEFT:
        out[:, 0] = mono_block
    elif channel_mode == ChannelMode.RIGHT:
        out[:, 1] = mono_block
    else:  # BOTH
        if force_mono or right_block is None:
            out[:, 0] = mono_block
            out[:, 1] = mono_block
        else:
            out[:, 0] = mono_block
            out[:, 1] = right_block
    return out
