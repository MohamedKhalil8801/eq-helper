"""Test audio (the customer's own files) played inside the app, through the customer's EQ.

Files are decoded with ``soundfile`` (libsndfile ≥ 1.1: WAV, FLAC, OGG/Opus,
MP3) on a worker thread, resampled to the device rate and played as stereo
through the engine's per-ear EQ path. The EQ can be switched on/off (A/B)
while the song keeps playing. Formats libsndfile can't read (M4A/AAC/WMA)
are opened in the system's own player instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np

PLAYABLE_SUFFIXES = (".wav", ".flac", ".ogg", ".oga", ".opus", ".mp3", ".aif", ".aiff")


class UnsupportedFile(Exception):
    """The file can't be played here; offer "Open in system player"."""


def can_play(path) -> bool:
    return Path(path).suffix.lower() in PLAYABLE_SUFFIXES


def decode(path, sample_rate: int) -> np.ndarray:
    """Decode to float32 ``(n, 2)`` at ``sample_rate``. Raises UnsupportedFile."""
    path = Path(path)
    if not can_play(path):
        raise UnsupportedFile(f"{path.name} can't be played in the app; open it in your system player.")
    try:
        import soundfile as sf
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 - libsndfile raises various types
        raise UnsupportedFile(f"{path.name} could not be decoded ({exc}); open it in your system player.") from exc
    if data.shape[0] == 0:
        raise UnsupportedFile(f"{path.name} contains no audio.")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    if int(rate) != int(sample_rate):
        from scipy.signal import resample_poly
        g = gcd(int(rate), int(sample_rate))
        data = resample_poly(data, int(sample_rate) // g, int(rate) // g, axis=0).astype(np.float32)
    return np.ascontiguousarray(data, dtype=np.float32)


def _system_open(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607


@dataclass(frozen=True)
class LoadedSong:
    path: Path
    frames: int
    sample_rate: int

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


class SongPlayer:
    """Play/pause/seek one song at a time; EQ on/off without restarting."""

    def __init__(self, engine, opener=_system_open):
        self.engine = engine
        self.opener = opener
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="song-decode")
        self._data: np.ndarray | None = None
        self.song: LoadedSong | None = None
        self.playing = False
        self.paused = False
        self._eq_firs = (None, None)  # (on, off)
        self.eq_enabled = True

    # ── loading ────────────────────────────────────────────────────────────
    def load(self, path) -> "Future[LoadedSong]":
        """Decode in the background; the returned future gives the LoadedSong."""
        path = Path(path)
        rate = int(self.engine.sample_rate)

        def work():
            data = decode(path, rate)
            self.stop()
            self._data = data
            self.song = LoadedSong(path, data.shape[0], rate)
            return self.song
        return self._pool.submit(work)

    def open_externally(self, path) -> None:
        self.opener(Path(path))

    # ── transport ──────────────────────────────────────────────────────────
    def play(self) -> None:
        if self._data is None:
            return
        if self.playing and self.paused:
            self.engine.pause_music(False)
        elif not self.playing or self.finished:
            self.engine.start_music(self._data)
        self.playing, self.paused = True, False

    def pause(self) -> None:
        if self.playing:
            self.engine.pause_music(True)
            self.paused = True

    def toggle(self) -> None:
        self.pause() if self.playing and not self.paused else self.play()

    def stop(self) -> None:
        if self.playing:
            self.engine.stop_music()
        self.playing = self.paused = False

    def seek(self, seconds: float) -> None:
        if self.song is not None:
            self.engine.seek_music(int(round(seconds * self.song.sample_rate)))

    @property
    def position(self) -> float:
        if self.song is None or not self.playing:
            return 0.0
        return self.engine.music_position / self.song.sample_rate

    @property
    def finished(self) -> bool:
        return self.playing and self.engine.music_finished

    # ── EQ A/B ─────────────────────────────────────────────────────────────
    def set_eq_firs(self, on, off) -> None:
        """Per-ear FIRs with EQ on, and the level-matched bypass for EQ off."""
        self._eq_firs = (on, off)
        self._apply_eq()

    def set_eq_enabled(self, enabled: bool) -> None:
        self.eq_enabled = bool(enabled)
        self._apply_eq()

    def _apply_eq(self) -> None:
        on, off = self._eq_firs
        self.engine.set_channel_firs(on if self.eq_enabled else off)
