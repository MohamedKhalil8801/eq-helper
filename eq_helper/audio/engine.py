"""Audio engine: owns the PortAudio streams and the realtime callbacks.

Supports single-device and dual-device output. In dual mode, two
``sd.OutputStream`` instances run on different devices. The source audio is
generated once by the primary callback and shared with the secondary via a
lock-free double buffer.
"""

from __future__ import annotations

import os
import sys
import threading
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

# sounddevice is imported LAZILY via ensure_audio() — never at module import.
# Its import runs platform.system(), which on Python 3.12+ issues a Windows WMI
# query that can block FOREVER when WMI is wedged (a state some machines enter
# after changing audio devices; CPython gh-112278). With the import on the
# module level it sat on the GUI-critical path, so the app process started but
# no window ever appeared. All call sites already treat ``sd is None`` as
# "no audio" and keep working.
sd = None  # type: ignore  # set by ensure_audio()
_sd_lock = threading.Lock()
_sd_error: Optional[str] = None


def ensure_audio() -> bool:
    """Import sounddevice and enable audio. Idempotent and thread-safe.

    MUST be called off the GUI thread: the import can block indefinitely on a
    wedged Windows WMI/audio state. Returns ``True`` when audio is available.
    Honors ``EQ_HELPER_NO_AUDIO`` (used by tests) by staying in no-audio mode.
    """
    global sd, _sd_error
    if sd is not None:
        return True
    if os.environ.get("EQ_HELPER_NO_AUDIO"):
        return False
    with _sd_lock:
        if sd is not None:
            return True
        try:
            import sounddevice as _sd_mod  # noqa: PLC0415 — deliberate lazy import
        except Exception as exc:  # OSError: PortAudio missing; else broken env
            _sd_error = str(exc)
            return False
        sd = _sd_mod
        return True


def audio_available() -> bool:
    return sd is not None


def audio_init_error() -> Optional[str]:
    return _sd_error

from eq_helper.audio.filters import BandPassFilter
from eq_helper.audio.generators import (
    LogSweepGenerator,
    PingPongSweepGenerator,
    PinkNoiseGenerator,
    SineGenerator,
)
from eq_helper.audio.router import route
from eq_helper.audio.sweep_math import focused_sweep_params
from eq_helper.audio.shared_state import (
    ChannelMode,
    DualRouting,
    Mode,
    SharedState,
)

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_BLOCK_SIZE = 480  # 10 ms @ 48 kHz
TONE_SWITCH_FADE_S = 0.02  # crossfade length of switch_sine()


# ----------------------------------------------------------------- helpers


def _assets_dir() -> Path:
    """Return the assets directory, works both in dev and PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.parent / "assets"


def _load_wav_mono(path: Path) -> Optional[np.ndarray]:
    """Load a WAV file as a mono float32 array normalized to [-1, 1]."""
    try:
        with wave.open(str(path), "rb") as wf:
            n = wf.getnframes()
            raw = wf.readframes(n)
            sw = wf.getsampwidth()
            ch = wf.getnchannels()
        if sw == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            return None
        if ch > 1:
            data = data.reshape(-1, ch)[:, 0]
        return data
    except Exception:  # noqa: BLE001
        return None


def list_output_devices() -> List[Tuple[int, str]]:
    """Return ``[(device_index, device_name), ...]`` for all output devices."""
    if sd is None:
        return []
    result = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] >= 2:  # type: ignore[index]
            result.append((i, dev["name"]))  # type: ignore[index]
    return result


# --------------------------------------------------------------- ring buffer


class RingBuffer:
    """Thread-safe mono float32 ring buffer."""

    def __init__(self, capacity: int = 16384) -> None:
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._cap = capacity
        self._write = 0
        self._filled = 0
        self._lock = threading.Lock()

    def push(self, block: np.ndarray) -> None:
        with self._lock:
            n = block.shape[0]
            if n >= self._cap:
                self._buf[:] = block[-self._cap :]
                self._write = 0
                self._filled = self._cap
                return
            end = self._write + n
            if end <= self._cap:
                self._buf[self._write : end] = block
            else:
                first = self._cap - self._write
                self._buf[self._write :] = block[:first]
                self._buf[: n - first] = block[first:]
            self._write = (self._write + n) % self._cap
            self._filled = min(self._cap, self._filled + n)

    def read_latest(self, n: int) -> Optional[np.ndarray]:
        """Return the most recent ``n`` samples (non-destructive)."""
        with self._lock:
            if self._filled < n:
                return None
            start = (self._write - n) % self._cap
            if start + n <= self._cap:
                return self._buf[start : start + n].copy()
            first = self._cap - start
            out = np.empty(n, dtype=np.float32)
            out[:first] = self._buf[start:]
            out[first:] = self._buf[: n - first]
            return out

    def pop(self, n: int) -> np.ndarray:
        """Read and remove up to ``n`` samples (destructive)."""
        with self._lock:
            avail = min(n, self._filled)
            if avail == 0:
                return np.zeros(0, dtype=np.float32)
            start = (self._write - self._filled) % self._cap
            self._filled -= avail
            if start + avail <= self._cap:
                return self._buf[start : start + avail].copy()
            first = self._cap - start
            out = np.empty(avail, dtype=np.float32)
            out[:first] = self._buf[start:]
            out[first:] = self._buf[: avail - first]
            return out


# -------------------------------------------------------------------- engine


class AudioEngine:
    """Owns the sounddevice streams and coordinates all audio work."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        block_size: int = DEFAULT_BLOCK_SIZE,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        self.state = SharedState()
        self.ring = RingBuffer(capacity=max(16384, self.sample_rate // 2))

        # Generators
        self._sine = SineGenerator(self.sample_rate)
        self._pink = PinkNoiseGenerator(self.sample_rate)
        self._pink_right = PinkNoiseGenerator(self.sample_rate, seed=0xC0FFEE)
        self._sweep: Optional[LogSweepGenerator | PingPongSweepGenerator] = None
        self._focus_center_hz: float = 1000.0  # centre of the current focused sweep

        # Crossfaded tones (switch_sine): each voice is [generator, freq, gain,
        # target gain, gain step per sample]. Empty = the plain sine above,
        # exactly as before.
        self._voices: list[list] = []
        self._voice_cmds: list[tuple] = []
        self._voice_lock = threading.Lock()

        # Grey noise (loaded from WAV, looped)
        self._grey_buf: Optional[np.ndarray] = _load_wav_mono(
            _assets_dir() / "grey_noise.wav"
        )
        self._grey_pos: int = 0
        self._filter = BandPassFilter(self.sample_rate)

        # EQ chain (parametric EQ filters from EQ mode)
        self._eq_chain_sos: Optional[np.ndarray] = None
        self._eq_chain_zi: Optional[np.ndarray] = None

        # Graphic EQ (FIR filter from graphic EQ mode)
        self._geq_fir: Optional[np.ndarray] = None
        self._geq_zi: Optional[np.ndarray] = None

        # Per-ear FIRs after routing (customer-app EQ preview). Separate from
        # the mono FIR above, which EQ Helper keeps using unchanged.
        from eq_helper.audio.stereo_fir import StereoFIR
        self._stereo_fir = StereoFIR()

        # Output streams (A = primary, B = secondary for dual mode)
        self._stream_a: Optional["sd.OutputStream"] = None
        self._stream_b: Optional["sd.OutputStream"] = None
        self._stream_lock = threading.Lock()
        self._device_a: Optional[int] = None  # None = system default
        self._device_b: Optional[int] = None

        # Ring buffer for passing stereo audio from callback A → callback B.
        # Stores interleaved [L0,R0,L1,R1,...] so each block is consumed
        # exactly once (destructive pop), eliminating the buzzing caused by
        # the secondary re-reading stale data from a plain shared array.
        self._dual_ring = RingBuffer(capacity=max(16384, self.block_size * 16))

        # Microphone / input
        self.mic_ring = RingBuffer(capacity=max(16384, self.sample_rate // 2))
        # Monitor buffer: larger capacity + pre-fill threshold so the output
        # callback always has a full block ready. Without this, timing jitter
        # between the independent input/output callbacks causes partial reads
        # → choppy audio with periodic silence gaps.
        self._monitor_buf = RingBuffer(capacity=max(16384, self.block_size * 32))
        self._monitor_prefill = self.block_size * 3  # ~30 ms lead time
        self._monitor_primed = False
        self._input_stream: Optional["sd.InputStream"] = None
        self._input_lock = threading.Lock()

        # Recording
        self._recording = False
        self._rec_chunks: list[np.ndarray] = []

        # Playback
        self._playback_buf: Optional[np.ndarray] = None
        self._playback_pos: int = 0

        # Stereo music (test songs): (n, 2) at the device rate.
        self._music_buf: Optional[np.ndarray] = None
        self._music_pos: int = 0
        self._music_paused: bool = False
        self._music_loop: bool = False

    # -------------------------------------------------------------- output streams

    def _open_stream(self, device: Optional[int], callback) -> Optional["sd.OutputStream"]:
        """Open one OutputStream on the given device. Returns None on failure."""
        if sd is None or os.environ.get("EQ_HELPER_NO_AUDIO"):
            return None
        try:
            stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=device,
                channels=2,
                dtype="float32",
                latency="low",
                callback=callback,
            )
            stream.start()
            actual_rate = getattr(stream, "samplerate", None)
            if actual_rate is not None:
                try:
                    self.sample_rate = int(actual_rate)
                except (TypeError, ValueError):
                    pass
            return stream
        except Exception:  # noqa: BLE001
            return None

    def _close_stream(self, stream: Optional["sd.OutputStream"]) -> None:
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001
            pass

    def start_stream(self) -> None:
        """Open output stream(s). Idempotent. Respects dual-output state."""
        if sd is None or os.environ.get("EQ_HELPER_NO_AUDIO"):
            return
        with self._stream_lock:
            # Primary stream
            if self._stream_a is None:
                self._stream_a = self._open_stream(
                    self._device_a, self._callback_a
                )
            # Secondary stream (only in dual mode)
            st = self.state.snapshot()
            if st.dual_output and self._stream_b is None and self._device_b is not None:
                self._stream_b = self._open_stream(
                    self._device_b, self._callback_b
                )

    def stop_stream(self) -> None:
        with self._stream_lock:
            self._close_stream(self._stream_a)
            self._stream_a = None
            self._close_stream(self._stream_b)
            self._stream_b = None

    def set_device_a(self, device_id: Optional[int]) -> None:
        """Set the primary output device. Restarts the stream if running."""
        if device_id == self._device_a:
            return
        self._device_a = device_id
        self._restart_streams()

    def set_device_b(self, device_id: Optional[int]) -> None:
        """Set the secondary output device. Restarts the stream if running."""
        if device_id == self._device_b:
            return
        self._device_b = device_id
        self._restart_streams()

    def set_dual_output(self, enabled: bool) -> None:
        self.state.update(dual_output=bool(enabled))
        self._restart_streams()

    def set_dual_routing(self, routing: DualRouting) -> None:
        self.state.update(dual_routing=routing)

    def _restart_streams(self) -> None:
        """Close and reopen streams to apply new device/dual settings."""
        was_running = self._stream_a is not None
        self.stop_stream()
        if was_running:
            self.start_stream()

    # -------------------------------------------------------------- input stream

    def start_input_stream(self) -> None:
        if sd is None or os.environ.get("EQ_HELPER_NO_AUDIO"):
            return
        with self._input_lock:
            if self._input_stream is not None:
                return
            try:
                self._input_stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    channels=1,
                    dtype="float32",
                    callback=self._input_callback,
                )
                self._input_stream.start()
            except Exception:  # noqa: BLE001
                self._input_stream = None

    def stop_input_stream(self) -> None:
        with self._input_lock:
            if self._input_stream is None:
                return
            try:
                self._input_stream.stop()
                self._input_stream.close()
            finally:
                self._input_stream = None

    # ---------------------------------------------------------------- controls

    def start_sine(self, freq: Optional[float] = None) -> None:
        if self._voices or self._voice_cmds:  # a plain tone replaces crossfaded ones
            with self._voice_lock:
                self._voice_cmds = [("clear",)]
        if freq is not None:
            self.state.update(freq=float(freq))
        self.state.update(mode=Mode.SINE)
        self.start_stream()

    def start_pink(self) -> None:
        self.state.update(mode=Mode.PINK)
        self.start_stream()

    def start_sweep(
        self,
        f_start: Optional[float] = None,
        f_end: Optional[float] = None,
        duration_s: Optional[float] = None,
        loop: Optional[bool] = None,
    ) -> None:
        if f_start is not None:
            self.state.update(sweep_f_start=float(f_start))
        if f_end is not None:
            self.state.update(sweep_f_end=float(f_end))
        if duration_s is not None:
            self.state.update(sweep_duration_s=float(duration_s))
        if loop is not None:
            self.state.update(sweep_loop=bool(loop))
        snap = self.state.snapshot()
        self._sweep = LogSweepGenerator(
            f_start=snap.sweep_f_start,
            f_end=snap.sweep_f_end,
            duration_s=snap.sweep_duration_s,
            sample_rate=self.sample_rate,
            loop=snap.sweep_loop,
        )
        self.state.update(mode=Mode.SWEEP)
        self.start_stream()

    def start_focused_sweep(
        self,
        center_hz: float,
        width_octaves: float,
        seconds_per_octave: float,
    ) -> None:
        """Start a looping bidirectional ("ping-pong") sweep over a narrow band
        centred on ``center_hz`` — for judging the smoothness of the response
        around a target tone. Reuses ``Mode.SWEEP``."""
        self._focus_center_hz = float(center_hz)
        f_start, f_end, duration_s = focused_sweep_params(
            center_hz, width_octaves, seconds_per_octave
        )
        self.state.update(
            sweep_f_start=f_start,
            sweep_f_end=f_end,
            sweep_duration_s=duration_s,
            sweep_loop=True,
        )
        self._sweep = PingPongSweepGenerator(
            f_start=f_start,
            f_end=f_end,
            duration_s=duration_s,
            sample_rate=self.sample_rate,
        )
        self.state.update(mode=Mode.SWEEP)
        self.start_stream()

    def update_focused_sweep(
        self, width_octaves: float, seconds_per_octave: float
    ) -> None:
        """Re-band/re-time a running focused sweep in place (no restart).

        The centre is kept fixed (the stored one from ``start_focused_sweep``),
        so repeated width tweaks don't drift from edge-clamping. A no-op unless a
        focused (ping-pong) sweep is currently active."""
        if not isinstance(self._sweep, PingPongSweepGenerator):
            return
        f_start, f_end, duration_s = focused_sweep_params(
            self._focus_center_hz, width_octaves, seconds_per_octave
        )
        self._sweep.set_params(f_start=f_start, f_end=f_end, duration_s=duration_s)
        self.state.update(
            sweep_f_start=f_start,
            sweep_f_end=f_end,
            sweep_duration_s=duration_s,
        )

    def start_grey(self) -> None:
        self._grey_pos = 0
        self.state.update(mode=Mode.GREY)
        self.start_stream()

    def switch_sine(self, freq: float, fade_s: float = TONE_SWITCH_FADE_S) -> None:
        """Play a sine at ``freq``, crossfading from whatever tone is playing (or
        fading in from silence) over ``fade_s`` seconds: no click, no gap."""
        with self._voice_lock:
            self._voice_cmds.append(("switch", float(freq), max(1, int(fade_s * self.sample_rate))))
        self.state.update(freq=float(freq), mode=Mode.SINE)
        self.start_stream()

    def stop(self) -> None:
        self.state.update(mode=Mode.IDLE)
        if self._voices or self._voice_cmds:
            with self._voice_lock:
                self._voice_cmds = [("clear",)]

    def set_freq(self, freq: float) -> None:
        self.state.update(freq=float(freq))
        if self._voices or self._voice_cmds:
            with self._voice_lock:
                self._voice_cmds.append(("retune", float(freq)))

    def set_volume(self, amp: float) -> None:
        self.state.update(amp=float(amp))

    def set_channel_mode(self, mode: ChannelMode) -> None:
        self.state.update(channel_mode=mode)

    def set_mono(self, force_mono: bool) -> None:
        self.state.update(force_mono=bool(force_mono))

    def set_sweep_duration(self, duration_s: float) -> None:
        self.state.update(sweep_duration_s=float(duration_s))
        # Only the full-range sweep uses this control; a focused (ping-pong)
        # sweep owns its own duration and must not be disturbed.
        if isinstance(self._sweep, LogSweepGenerator):
            self._sweep.set_duration(duration_s)

    def set_sweep_loop(self, loop: bool) -> None:
        self.state.update(sweep_loop=bool(loop))
        if isinstance(self._sweep, LogSweepGenerator):
            self._sweep._loop = bool(loop)  # type: ignore[attr-defined]

    def set_band_filter(self, fc: float, bw: float, enabled: bool) -> None:
        self._filter.set_params(fc, bw)
        self.state.update(band_enabled=bool(enabled), sos=self._filter.sos_copy())

    def set_band_enabled(self, enabled: bool) -> None:
        self.state.update(band_enabled=bool(enabled))

    def set_eq_chain(self, sos: Optional[np.ndarray], enabled: bool) -> None:
        """Swap the EQ chain SOS atomically. Resets filter state."""
        from scipy.signal import sosfilt_zi
        self._eq_chain_sos = sos
        if sos is not None:
            self._eq_chain_zi = sosfilt_zi(sos) * 0.0
        else:
            self._eq_chain_zi = None
        self.state.update(eq_chain_sos=sos, eq_chain_enabled=bool(enabled))

    def set_graphic_eq(self, fir: Optional[np.ndarray]) -> None:
        """Swap the graphic EQ FIR atomically. Resets filter state."""
        self._geq_fir = fir
        if fir is not None:
            self._geq_zi = np.zeros(len(fir) - 1, dtype=np.float64)
        else:
            self._geq_zi = None
        self.state.update(graphic_eq_fir=fir)

    # --------------------------------------------------------- mic controls

    def set_channel_firs(self, firs: Optional[dict]) -> None:
        """Per-ear FIRs ``{"L": fir, "R": fir}`` applied after routing; ``None`` = bypass.

        Switching is crossfaded, so it can happen while sound is playing.
        """
        self._stereo_fir.set_firs(firs)

    def set_mic_monitoring(self, enabled: bool) -> None:
        self.state.update(mic_monitoring=bool(enabled))
        if enabled:
            self._monitor_primed = False
            self.start_input_stream()
            self.start_stream()
        else:
            self._monitor_primed = False

    def start_recording(self) -> None:
        self._rec_chunks = []
        self._recording = True
        self.start_input_stream()

    def stop_recording(self) -> Optional[np.ndarray]:
        self._recording = False
        if not self._rec_chunks:
            return None
        buf = np.concatenate(self._rec_chunks).astype(np.float32)
        self._rec_chunks = []
        return buf

    def start_playback(self, buf: np.ndarray, loop: bool = True) -> None:
        self._playback_buf = buf.astype(np.float32, copy=False)
        self._playback_pos = 0
        self._playback_loop = loop
        self.state.update(mode=Mode.PLAYBACK)
        self.start_stream()

    def stop_playback(self) -> None:
        self.state.update(mode=Mode.IDLE)
        self._playback_buf = None
        self._playback_pos = 0

    def start_music(self, buf: np.ndarray, loop: bool = False) -> None:
        """Play an ``(n, 2)`` buffer already at :attr:`sample_rate`, from the start."""
        data = np.asarray(buf, dtype=np.float32)
        if data.ndim != 2 or data.shape[1] != 2:
            raise ValueError("Music must be an (n, 2) stereo buffer.")
        self._music_buf = data
        self._music_pos = 0
        self._music_paused = False
        self._music_loop = loop
        self.state.update(mode=Mode.MUSIC)
        self.start_stream()

    def pause_music(self, paused: bool) -> None:
        self._music_paused = bool(paused)

    def seek_music(self, frame: int) -> None:
        if self._music_buf is not None:
            self._music_pos = int(np.clip(frame, 0, self._music_buf.shape[0]))

    @property
    def music_position(self) -> int:
        return self._music_pos

    @property
    def music_finished(self) -> bool:
        buf = self._music_buf
        return buf is not None and not self._music_loop and self._music_pos >= buf.shape[0]

    def stop_music(self) -> None:
        if self.state.snapshot().mode == Mode.MUSIC:
            self.state.update(mode=Mode.IDLE)
        self._music_buf = None
        self._music_pos = 0

    def _music_block(self, st, frames: int):
        buf = self._music_buf
        out = np.zeros((frames, 2), dtype=np.float32)
        if buf is None or self._music_paused or buf.shape[0] == 0:
            return out[:, 0], out[:, 1]
        pos, written = self._music_pos, 0
        while written < frames:
            if pos >= buf.shape[0]:
                if not self._music_loop:
                    break
                pos = 0
            take = min(frames - written, buf.shape[0] - pos)
            out[written:written + take] = buf[pos:pos + take]
            pos += take
            written += take
        self._music_pos = pos
        out *= np.float32(st.amp)
        # Songs are always stereo; "force mono" is a test-signal setting.
        return out[:, 0].copy(), out[:, 1].copy()

    # --------------------------------------------------------- input callback

    def _input_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        mono = indata[:, 0].copy()
        self.mic_ring.push(mono)
        if self.state.snapshot().mic_monitoring:
            self._monitor_buf.push(mono)
            if not self._monitor_primed:
                with self._monitor_buf._lock:
                    if self._monitor_buf._filled >= self._monitor_prefill:
                        self._monitor_primed = True
        if self._recording:
            self._rec_chunks.append(mono.copy())

    # --------------------------------------------------- source generation

    def _generate_source(self, st, frames: int) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Generate the mono (and optional right) source block.

        Returns ``(mono, right)`` or ``(None, None)`` for IDLE.
        """
        if self._voice_cmds:
            self._take_voice_cmds()
        if self._voices and st.mode != Mode.SINE:
            self._voices = []  # another sound took over: nothing left to fade
        if st.mode == Mode.SINE:
            if self._voices:
                return self._voices_block(frames) * np.float32(st.amp), None
            mono = self._sine.next_block(frames, st.freq) * np.float32(st.amp)
            return mono, None
        elif st.mode == Mode.PINK:
            mono = self._pink.next_block(frames) * np.float32(st.amp)
            right = None
            if st.channel_mode == ChannelMode.BOTH and not st.force_mono:
                right = self._pink_right.next_block(frames) * np.float32(st.amp)
            return mono, right
        elif st.mode == Mode.SWEEP:
            sweep = self._sweep
            if sweep is None:
                return None, None
            mono = sweep.next_block(frames) * np.float32(st.amp)
            return mono, None
        elif st.mode == Mode.GREY:
            gb = self._grey_buf
            if gb is None or gb.shape[0] == 0:
                return None, None
            mono = np.empty(frames, dtype=np.float32)
            pos = self._grey_pos
            remaining = frames
            written = 0
            while remaining > 0:
                avail = min(remaining, gb.shape[0] - pos)
                mono[written : written + avail] = gb[pos : pos + avail] * np.float32(st.amp)
                pos += avail
                written += avail
                remaining -= avail
                if pos >= gb.shape[0]:
                    pos = 0  # always loop
            self._grey_pos = pos
            return mono, None
        elif st.mode == Mode.MUSIC:
            return self._music_block(st, frames)
        elif st.mode == Mode.PLAYBACK:
            pb = self._playback_buf
            if pb is None or pb.shape[0] == 0:
                return None, None
            mono = np.empty(frames, dtype=np.float32)
            pos = self._playback_pos
            remaining = frames
            written = 0
            while remaining > 0:
                avail = min(remaining, pb.shape[0] - pos)
                mono[written : written + avail] = pb[pos : pos + avail] * np.float32(st.amp)
                pos += avail
                written += avail
                remaining -= avail
                if pos >= pb.shape[0]:
                    if getattr(self, "_playback_loop", True):
                        pos = 0
                    else:
                        mono[written:] = 0.0
                        break
            self._playback_pos = pos
            return mono, None
        else:  # IDLE
            return None, None

    def _take_voice_cmds(self) -> None:
        with self._voice_lock:
            cmds, self._voice_cmds = self._voice_cmds, []
        for cmd in cmds:
            if cmd[0] == "clear":
                self._voices = []
            elif cmd[0] == "retune" and self._voices:
                self._voices = [self._voices[-1]]
                self._voices[0][1], self._voices[0][2], self._voices[0][3] = cmd[1], 1.0, 1.0
            elif cmd[0] == "switch":
                _kind, freq, fade = cmd
                for voice in self._voices:
                    voice[3] = 0.0
                self._voices.append([SineGenerator(self.sample_rate), freq, 0.0, 1.0, 1.0 / fade])

    def _voices_block(self, frames: int) -> np.ndarray:
        """Sum of the crossfading tones from switch_sine (equal-power fades)."""
        out = np.zeros(frames, dtype=np.float64)
        steps = np.arange(1, frames + 1)
        for voice in self._voices:
            gen, freq, gain, target, rate = voice
            if gain == target:
                ramp = np.full(frames, gain)
            else:
                direction = 1.0 if target > gain else -1.0
                ramp = np.clip(gain + direction * rate * steps, 0.0, 1.0)
                if direction < 0:
                    ramp = np.maximum(ramp, target)
                else:
                    ramp = np.minimum(ramp, target)
                voice[2] = float(ramp[-1])
            out += gen.next_block(frames, freq) * np.sin(0.5 * np.pi * ramp)
        self._voices = [v for v in self._voices if v[2] > 0.0 or v[3] > 0.0]
        return out.astype(np.float32)

    def _apply_processing(
        self, st, frames: int, mono: np.ndarray, right: Optional[np.ndarray]
    ) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """Apply mic monitoring mix + band filter to the source."""
        if st.mic_monitoring and self._monitor_primed:
            monitor = self._monitor_buf.pop(frames)
            if monitor.shape[0] > 0:
                mono[: monitor.shape[0]] += monitor

        if st.band_enabled and st.sos is not None:
            mono = self._filter.process(mono)
            if right is not None:
                right = self._filter.process(right)

        # EQ chain from EQ mode (parametric EQ filters).
        if st.eq_chain_enabled and self._eq_chain_sos is not None and self._eq_chain_zi is not None:
            from scipy.signal import sosfilt
            mono, self._eq_chain_zi = sosfilt(
                self._eq_chain_sos, mono, zi=self._eq_chain_zi
            )
            mono = mono.astype(np.float32, copy=False)

        # Graphic EQ (FIR filter).
        if self._geq_fir is not None and self._geq_zi is not None:
            from scipy.signal import lfilter
            mono_f64 = mono.astype(np.float64)
            mono_f64, self._geq_zi = lfilter(
                self._geq_fir, [1.0], mono_f64, zi=self._geq_zi
            )
            mono = mono_f64.astype(np.float32)

        return mono, right

    # -------------------------------------------------------- output callbacks

    def _callback_a(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        """Primary output callback. Generates audio and shares it with B."""
        st = self.state.snapshot()
        frames = int(frames)

        mono, right = self._generate_source(st, frames)

        if mono is None:
            # IDLE — still handle mic monitoring
            if st.mic_monitoring and self._monitor_primed:
                monitor = self._monitor_buf.pop(frames)
                if monitor.shape[0] >= frames:
                    mono = monitor[:frames]
                    self.ring.push(mono)
                    self._route_dual(outdata, mono, st, frames)
                    return
            if self._stereo_fir.active and not st.dual_output:
                outdata[:] = self._stereo_fir.process(np.zeros((frames, 2), dtype=np.float32))
            else:
                outdata.fill(0.0)
            self._dual_ring.push(np.zeros(frames * 2, dtype=np.float32))
            return

        mono, right = self._apply_processing(st, frames, mono, right)
        self.ring.push(mono)
        # Only music carries a real right channel to the output; other
        # sources keep their existing mono routing.
        self._route_dual(outdata, mono, st, frames, right if st.mode == Mode.MUSIC else None)

    def _route_dual(
        self, outdata: np.ndarray, mono: np.ndarray, st, frames: int,
        right: Optional[np.ndarray] = None,
    ) -> None:
        """Route the mono source to device A and push data for device B.

        **Mirror:** Both devices get the exact same signal. The Channel
        switch (L/Both/R) controls which physical ears play on BOTH devices
        — identical behaviour to single-device mode.

        **Split L/R:** Device A plays the mono source in the LEFT ear only
        (right ear silent). Device B plays it in the RIGHT ear only (left
        ear silent). The Channel switch is ignored.
        """
        if not st.dual_output:
            stereo = self._stereo_fir.process(route(mono, st.channel_mode, st.force_mono and right is None, right))
            outdata[:] = stereo
            self._dual_ring.push(stereo.flatten())
            return

        if st.dual_routing == DualRouting.SPLIT_LR:
            # Device A: left ear only.  Device B: right ear only.
            # Channel switch selects which device(s) play:
            #   L    → device A only
            #   Both → both devices
            #   R    → device B only
            play_a = st.channel_mode in (ChannelMode.LEFT, ChannelMode.BOTH)
            play_b = st.channel_mode in (ChannelMode.RIGHT, ChannelMode.BOTH)

            out_a = np.zeros((frames, 2), dtype=np.float32)
            if play_a:
                out_a[:, 0] = mono
            outdata[:] = out_a

            out_b = np.zeros((frames, 2), dtype=np.float32)
            if play_b:
                out_b[:, 1] = mono
            self._dual_ring.push(out_b.flatten())
        else:
            # Mirror: identical output on both devices, channel switch
            # controls L/R ears the same way as single-device mode.
            stereo = self._stereo_fir.process(route(mono, st.channel_mode, st.force_mono and right is None, right))
            outdata[:] = stereo
            self._dual_ring.push(stereo.flatten())

    def _callback_b(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        """Secondary output callback. Pops pre-routed audio from the ring."""
        frames = int(frames)
        needed = frames * 2
        data = self._dual_ring.pop(needed)
        if data.shape[0] < needed:
            padded = np.zeros(needed, dtype=np.float32)
            padded[: data.shape[0]] = data
            data = padded
        outdata[:] = data.reshape(frames, 2)

    # Legacy single-callback name for backwards compat with process_block.
    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        self._callback_a(outdata, frames, time_info, status)

    def process_block(self, frames: int) -> np.ndarray:
        """Drive the callback manually (used by tests to avoid PortAudio)."""
        out = np.zeros((frames, 2), dtype=np.float32)
        self._callback(out, frames, None, None)
        return out

    def process_block_dual(self, frames: int) -> Tuple[np.ndarray, np.ndarray]:
        """Drive both callbacks manually (used by tests)."""
        out_a = np.zeros((frames, 2), dtype=np.float32)
        out_b = np.zeros((frames, 2), dtype=np.float32)
        self._callback_a(out_a, frames, None, None)
        self._callback_b(out_b, frames, None, None)
        return out_a, out_b

    def simulate_mic_input(self, block: np.ndarray) -> None:
        """Drive the input callback manually (used by tests)."""
        indata = block.reshape(-1, 1)
        self._input_callback(indata, block.shape[0], None, None)

    def pop_recent_samples(self, n: int) -> Optional[np.ndarray]:
        return self.ring.read_latest(n)
