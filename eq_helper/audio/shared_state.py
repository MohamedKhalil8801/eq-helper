"""Thread-safe shared state between UI thread and PortAudio callback.

The audio callback runs on PortAudio's realtime thread and must never allocate
or block for long. The UI thread writes user intent here; the callback snapshots
it once per block. All mutable fields are plain primitives (so the snapshot is a
cheap tuple build), except ``sos`` which is a numpy array swapped atomically via
CPython pointer rebinding under the GIL.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


class Mode(enum.IntEnum):
    """Which source generator is active."""

    IDLE = 0
    SINE = 1
    PINK = 2
    SWEEP = 3
    PLAYBACK = 4
    GREY = 5


class ChannelMode(enum.IntEnum):
    """Which hardware channels receive the signal."""

    LEFT = 0
    BOTH = 1
    RIGHT = 2


class DualRouting(enum.IntEnum):
    """How audio is distributed across two output devices."""

    MIRROR = 0   # identical L+R on both devices
    SPLIT_LR = 1  # L-only on device 1, R-only on device 2


@dataclass
class StateSnapshot:
    """Plain-data snapshot taken by the audio callback each block.

    All fields are immutable primitives or numpy arrays that are treated as
    read-only. No Python-level locking is required once the snapshot is taken.
    """

    mode: Mode
    freq: float
    amp: float
    channel_mode: ChannelMode
    force_mono: bool
    band_enabled: bool
    sos: Optional[np.ndarray]
    sweep_f_start: float
    sweep_f_end: float
    sweep_duration_s: float
    sweep_loop: bool
    mic_monitoring: bool
    dual_output: bool
    dual_routing: DualRouting
    eq_chain_sos: Optional[np.ndarray]
    eq_chain_enabled: bool
    graphic_eq_fir: Optional[np.ndarray]


class SharedState:
    """Mutable container guarded by a single short-lived lock.

    The lock is held only long enough to copy primitives out into a
    ``StateSnapshot``; this keeps the audio callback's contention-free in
    practice (GIL + tiny critical section).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mode: Mode = Mode.IDLE
        self.freq: float = 1000.0
        self.amp: float = 0.08  # 8% default per spec
        self.channel_mode: ChannelMode = ChannelMode.BOTH
        self.force_mono: bool = True
        self.band_enabled: bool = False
        self.sos: Optional[np.ndarray] = None
        self.sweep_f_start: float = 20.0
        self.sweep_f_end: float = 20000.0
        self.sweep_duration_s: float = 10.0
        self.sweep_loop: bool = True
        self.mic_monitoring: bool = False
        self.dual_output: bool = False
        self.dual_routing: DualRouting = DualRouting.MIRROR
        self.eq_chain_sos: Optional[np.ndarray] = None
        self.eq_chain_enabled: bool = False
        self.graphic_eq_fir: Optional[np.ndarray] = None

    def snapshot(self) -> StateSnapshot:
        """Return an immutable snapshot of the current state."""
        with self._lock:
            return StateSnapshot(
                mode=self.mode,
                freq=self.freq,
                amp=self.amp,
                channel_mode=self.channel_mode,
                force_mono=self.force_mono,
                band_enabled=self.band_enabled,
                sos=self.sos,
                sweep_f_start=self.sweep_f_start,
                sweep_f_end=self.sweep_f_end,
                sweep_duration_s=self.sweep_duration_s,
                sweep_loop=self.sweep_loop,
                mic_monitoring=self.mic_monitoring,
                dual_output=self.dual_output,
                dual_routing=self.dual_routing,
                eq_chain_sos=self.eq_chain_sos,
                eq_chain_enabled=self.eq_chain_enabled,
                graphic_eq_fir=self.graphic_eq_fir,
            )

    def update(self, **kwargs: object) -> None:
        """Bulk-update fields under the lock. Unknown keys raise AttributeError."""
        with self._lock:
            for key, value in kwargs.items():
                if not hasattr(self, key):
                    raise AttributeError(f"SharedState has no field {key!r}")
                setattr(self, key, value)
