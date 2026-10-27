"""Sound for the customer app: tones, sweeps, songs and the in-app EQ preview.

Wraps :class:`~eq_helper.audio.engine.AudioEngine` with the listening-safety
rules from the spec: volume starts low at every launch, one call stops all
sound, and the output device is explicit. ``sounddevice`` is imported off
the GUI thread (``ensure_audio``), exactly as EQ Helper does, because its
import can hang on a wedged Windows WMI service.
"""

from __future__ import annotations

import math
import threading
from typing import Callable, Optional

from eq_helper.audio import engine as audio_engine
from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import ChannelMode
from eq_helper.audio.stereo_fir import channel_firs

SAFE_START_VOLUME = 0.05          # about −26 dBFS; every launch starts here
FOCUS_WIDTH_OCTAVES = 1.0
FOCUS_SECONDS_PER_OCTAVE = 3.0
FULL_SWEEP = (20.0, 20000.0, 20.0)  # start Hz, end Hz, seconds

_ROUTES = {"L": ChannelMode.LEFT, "R": ChannelMode.RIGHT, "M": ChannelMode.BOTH,
           "both": ChannelMode.BOTH, None: ChannelMode.BOTH}


def amp_to_db(amp: float) -> float:
    return 20 * math.log10(max(float(amp), 1e-6))


class AudioService:
    def __init__(self, engine: Optional[AudioEngine] = None):
        self.engine = engine or AudioEngine()
        self.ready = False
        self.error: Optional[str] = None
        self.playing: Optional[str] = None  # "tone" | "focus" | "sweep" | "pink" | None
        self.side: Optional[str] = "both"
        self._eq = None
        self._eq_enabled = False
        self._fir_rate: Optional[int] = None
        self._firs = (None, None)
        self._listeners: list[Callable[[], None]] = []
        self.volume = SAFE_START_VOLUME
        self.engine.set_volume(self.volume)
        self.engine.stop()

    # ── start-up ───────────────────────────────────────────────────────────
    def start(self, on_ready: Callable[[bool], None] | None = None) -> threading.Thread:
        """Initialise audio on a worker thread; ``on_ready(ok)`` runs on that thread."""
        def work():
            ok = audio_engine.ensure_audio()
            self.ready = ok
            self.error = None if ok else audio_engine.audio_init_error()
            if on_ready is not None:
                on_ready(ok)
        thread = threading.Thread(target=work, name="audio-init", daemon=True)
        thread.start()
        return thread

    def on_change(self, callback: Callable[[], None]) -> None:
        """Called after anything that changes what is playing."""
        self._listeners.append(callback)

    def _changed(self) -> None:
        for callback in list(self._listeners):
            callback()

    # ── devices ────────────────────────────────────────────────────────────
    @staticmethod
    def output_devices() -> list[tuple[int, str]]:
        return audio_engine.list_output_devices()

    def set_output_device(self, device_id: Optional[int]) -> None:
        self.engine.set_device_a(device_id)

    @property
    def device_rate(self) -> int:
        """The output device's sample rate (what exports should be pre-filled with)."""
        sd = audio_engine.sd
        if sd is not None:
            try:
                info = sd.query_devices(self.engine._device_a, "output")
                return int(round(float(info["default_samplerate"])))
            except Exception:  # noqa: BLE001 - no device, unknown rate
                pass
        return int(self.engine.sample_rate)

    # ── volume and routing ─────────────────────────────────────────────────
    def set_volume(self, amp: float) -> None:
        self.volume = max(0.0, min(1.0, float(amp)))
        self.engine.set_volume(self.volume)

    def route(self, side) -> None:
        """Play on one ear ("L"/"R") or both ("M", "both", None)."""
        self.side = side
        self.engine.set_channel_mode(_ROUTES.get(side, ChannelMode.BOTH))

    # ── sources ────────────────────────────────────────────────────────────
    def play_tone(self, hz: float, side=None) -> None:
        if side is not None:
            self.route(side)
        self.engine.start_sine(float(hz))
        self._started("tone")

    def set_tone(self, hz: float) -> None:
        self.engine.set_freq(float(hz))

    def play_focus_sweep(self, hz: float, side=None) -> None:
        if side is not None:
            self.route(side)
        self.engine.start_focused_sweep(float(hz), FOCUS_WIDTH_OCTAVES, FOCUS_SECONDS_PER_OCTAVE)
        self._started("focus")

    def play_full_sweep(self, side=None) -> None:
        if side is not None:
            self.route(side)
        lo, hi, seconds = FULL_SWEEP
        self.engine.start_sweep(lo, hi, seconds, loop=True)
        self._started("sweep")

    def play_pink(self, side=None) -> None:
        if side is not None:
            self.route(side)
        self.engine.start_pink()
        self._started("pink")

    def _started(self, kind: str) -> None:
        self.playing = kind
        self._refresh_rate()
        self._changed()

    def stop_all(self) -> None:
        """Silence everything: tones, sweeps, noise and songs."""
        self.engine.stop()
        try:
            self.engine.stop_music()
        except Exception:  # noqa: BLE001
            pass
        self.playing = None
        self._changed()

    # ── in-app EQ preview ──────────────────────────────────────────────────
    def set_eq(self, channel_eq) -> None:
        """The EQ to preview (``None`` = no EQ yet). Recomputes the per-ear FIRs."""
        self._eq = channel_eq
        self._fir_rate = None
        self._refresh_rate()

    def set_eq_enabled(self, enabled: bool) -> None:
        self._eq_enabled = bool(enabled)
        self._apply()

    @property
    def eq_enabled(self) -> bool:
        return self._eq_enabled

    def preview_firs(self):
        """(on, off) FIR dicts for the song player's A/B."""
        return self._firs

    def _refresh_rate(self) -> None:
        rate = int(self.engine.sample_rate)
        if self._eq is not None and self._fir_rate != rate:
            self._firs = (channel_firs(self._eq, rate, True), channel_firs(self._eq, rate, False))
            self._fir_rate = rate
        elif self._eq is None:
            self._firs = (None, None)
            self._fir_rate = None
        self._apply()

    def _apply(self) -> None:
        on, off = self._firs
        if on is None:
            self.engine.set_channel_firs(None)
        else:
            self.engine.set_channel_firs(on if self._eq_enabled else off)
