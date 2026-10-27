"""Per-channel FIR filtering for the in-app EQ preview.

A long correction FIR (8k taps at 48 kHz) is far too heavy for ``lfilter``
in an audio callback, so each channel uses a uniformly partitioned
overlap-save convolver: the filter is cut into ``partition``-sized pieces,
each piece is an FFT, and every block costs one FFT pair plus a
multiply-add per piece.

:class:`StereoFIR` runs one convolver per ear after L/R routing. The input
spectra are shared by every filter loaded on a channel, so changing filters
(or turning the EQ on/off) needs no catch-up work in the audio callback; a
short crossfade keeps switching click-free. Blocks that are a multiple
of the partition add no latency.
"""

from __future__ import annotations

import threading

import numpy as np

from eq_helper.dsp import convolution as conv

DEFAULT_PARTITION = 256
DEFAULT_FADE = 2048


class _Blocker:
    """Turn arbitrary block sizes into fixed ``size`` chunks with a FIFO."""

    def __init__(self, size: int, channels: int):
        self.size = size
        self.channels = channels
        self._in = np.zeros((0, channels))
        self._out = np.zeros((0, channels))
        self.latency = 0

    def process(self, x: np.ndarray, step) -> np.ndarray:
        n = x.shape[0]
        self._in = np.concatenate([self._in, x])
        chunks = []
        while self._in.shape[0] >= self.size:
            chunks.append(step(self._in[:self.size]))
            self._in = self._in[self.size:]
        if chunks:
            self._out = np.concatenate([self._out, *chunks])
        short = n - self._out.shape[0]
        if short > 0:  # first odd-sized block: settle on a small fixed delay
            self._out = np.concatenate([np.zeros((short, self.channels)), self._out])
            self.latency += short
        y, self._out = self._out[:n], self._out[n:]
        return y


class PartitionedConvolver:
    """Uniformly partitioned overlap-save convolution of one channel."""

    def __init__(self, fir, partition: int = DEFAULT_PARTITION):
        h = np.asarray(fir, dtype=float).ravel()
        if h.size == 0 or not np.isfinite(h).all():
            raise ValueError("FIR must be a non-empty finite array.")
        self.partition = p = int(partition)
        self.parts = k = -(-h.size // p)
        padded = np.zeros(k * p)
        padded[:h.size] = h
        self._H = np.fft.rfft(np.pad(padded.reshape(k, p), ((0, 0), (0, p))), axis=1)
        self._fdl = np.zeros_like(self._H)  # newest input spectrum first
        self._prev = np.zeros(p)
        self._blocker = _Blocker(p, 1)

    @property
    def history_needed(self) -> int:
        return self.parts * self.partition

    @property
    def latency(self) -> int:
        return self._blocker.latency

    def step(self, chunk: np.ndarray) -> np.ndarray:
        """Filter exactly one partition of input."""
        chunk = np.asarray(chunk, dtype=float).ravel()
        frame = np.concatenate([self._prev, chunk])
        self._prev = chunk
        self._fdl = np.roll(self._fdl, 1, axis=0)
        self._fdl[0] = np.fft.rfft(frame)
        y = np.fft.irfft(np.einsum("kf,kf->f", self._fdl, self._H), n=2 * self.partition)
        return y[self.partition:]

    def prime(self, history: np.ndarray) -> None:
        """Feed past input (a multiple of the partition) so output is steady at once."""
        history = np.asarray(history, dtype=float).ravel()
        for i in range(0, history.size - self.partition + 1, self.partition):
            self.step(history[i:i + self.partition])

    def process(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=float).reshape(-1, 1)
        return self._blocker.process(x, lambda c: self.step(c[:, 0])[:, None])[:, 0]


class _Spectra:
    """A FIR cut into partitions and transformed once (on the caller's thread)."""

    def __init__(self, fir, partition: int):
        h = np.asarray(fir, dtype=float).ravel()
        if h.size == 0 or not np.isfinite(h).all():
            raise ValueError("FIR must be a non-empty finite array.")
        p = int(partition)
        self.parts = k = -(-h.size // p)
        padded = np.zeros(k * p)
        padded[:h.size] = h
        self.H = np.fft.rfft(np.pad(padded.reshape(k, p), ((0, 0), (0, p))), axis=1)


class StereoFIR:
    """One FIR per ear with click-free switching; ``None`` = bypass.

    The input spectra (the "frequency-domain delay line") belong to the
    channel, not to a filter: they're updated every partition whatever
    filter is loaded. A new filter therefore produces steady output at once,
    with no re-filtering of past input in the audio callback — swapping
    filters costs the same as any other block, so the EQ can follow a slider
    while sound plays without dropouts.
    """

    def __init__(self, partition: int = DEFAULT_PARTITION, fade: int = DEFAULT_FADE, parts: int = 64):
        self.partition = partition
        self.fade = fade
        self._blocker = _Blocker(partition, 2)
        self._lock = threading.Lock()
        self._pending = None  # (filters,) waiting for the audio thread
        self._current: list[_Spectra | None] = [None, None]
        self._next: list[_Spectra | None] | None = None
        self._fade_pos = 0
        bins = partition + 1
        self._fdl = np.zeros((2, parts, bins), dtype=complex)  # ring of input spectra per channel
        self._pos = 0
        self._prev = np.zeros((2, partition))
        self._fresh = True  # spectra hold nothing useful yet (never run, or idle since)

    @property
    def active(self) -> bool:
        return any(c is not None for c in self._current) or self._next is not None

    def set_firs(self, firs) -> None:
        """``{"L": fir, "R": fir}`` or ``None``. Safe to call from any thread; the FFTs happen here."""
        if firs is None:
            built = [None, None]
        else:
            built = [_Spectra(firs[s], self.partition) for s in ("L", "R")]
        with self._lock:
            self._pending = (built,)

    def _take_pending(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return
        (built,) = pending
        need = max((c.parts for c in built if c is not None), default=0)
        if need > self._fdl.shape[1]:  # longer filter (higher sample rate): grow the ring, oldest first
            order = (self._pos - np.arange(self._fdl.shape[1])) % self._fdl.shape[1]
            grown = np.zeros((2, need, self._fdl.shape[2]), dtype=complex)
            grown[:, :self._fdl.shape[1]] = self._fdl[:, order]
            self._fdl, self._pos = grown, 0
        if self._next is not None:  # interrupt a fade: finish it instantly
            self._current = self._next
        self._next = built
        self._fade_pos = 0

    def _filter(self, spectra: list, chunk: np.ndarray, idx: np.ndarray) -> np.ndarray:
        out = np.empty_like(chunk)
        p = self.partition
        for ch in range(2):
            c = spectra[ch]
            if c is None:
                out[:, ch] = chunk[:, ch]
            else:
                rows = self._fdl[ch, idx[:c.parts]]
                out[:, ch] = np.fft.irfft(np.einsum("kf,kf->f", rows, c.H), n=2 * p)[p:]
        return out

    def _step(self, chunk: np.ndarray) -> np.ndarray:
        self._take_pending()
        if self._fresh:
            self._fdl[:] = 0
            self._prev[:] = 0
            self._fresh = False
        k = self._fdl.shape[1]
        self._pos = (self._pos + 1) % k
        for ch in range(2):
            self._fdl[ch, self._pos] = np.fft.rfft(np.concatenate([self._prev[ch], chunk[:, ch]]))
            self._prev[ch] = chunk[:, ch]
        idx = (self._pos - np.arange(k)) % k  # newest input first
        out = self._filter(self._current, chunk, idx)
        if self._next is not None:
            new = self._filter(self._next, chunk, idx)
            n = chunk.shape[0]
            ramp = np.clip((self._fade_pos + np.arange(1, n + 1)) / self.fade, 0, 1)[:, None]
            out = out * (1 - ramp) + new * ramp
            self._fade_pos += n
            if self._fade_pos >= self.fade:
                self._current, self._next = self._next, None
        return out

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter an ``(n, 2)`` block; returns float32 of the same shape."""
        if not self.active and self._pending is None and self._blocker.latency == 0:
            self._fresh = True  # idle: skip the work
            return np.asarray(block, dtype=np.float32)
        return self._blocker.process(np.asarray(block, dtype=float), self._step).astype(np.float32)


# ── designing preview filters ───────────────────────────────────────────────

def _design(freqs, gain_db, sample_rate: int) -> np.ndarray:
    taps = conv.taps_for(sample_rate)
    n_fft = 1 << max(15, int(np.ceil(np.log2(8 * taps))))
    return conv._minimum_phase(conv._curve_magnitude(freqs, gain_db, sample_rate, n_fft), taps)


def gain_fir(gain_db: float) -> np.ndarray:
    return np.array([10.0 ** (gain_db / 20.0)])


def channel_firs(channel_eq, sample_rate: int, enabled: bool = True) -> dict[str, np.ndarray]:
    """Preview FIRs at the device rate: correction + shared preamp per ear.

    ``enabled=False`` gives the level-matched bypass (preamp only), so the A/B
    compares tone, not loudness. A single shared set feeds both ears.
    """
    preamp = channel_eq.preamp_db
    firs = {}
    for side, ch in channel_eq.channels.items():
        firs[side] = (_design(ch.freqs, ch.correction_db + preamp, int(sample_rate))
                      if enabled else gain_fir(preamp))
    if "M" in firs:
        return {"L": firs["M"], "R": firs["M"]}
    return firs
