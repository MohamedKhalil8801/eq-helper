"""Stateful band-pass biquad filter used by the frequency-isolation feature."""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np
from scipy.signal import iirfilter, sosfilt, sosfilt_zi


def make_bandpass_sos(
    fc: float,
    bw: float,
    sample_rate: int,
    order: int = 2,
) -> np.ndarray:
    """Build a Butterworth band-pass as a second-order-section matrix.

    The resulting SOS is safe to feed into ``scipy.signal.sosfilt`` with a
    persistent ``zi`` vector. Frequencies are clamped to ``[20, fs/2 - 1]``.
    """
    nyquist = sample_rate / 2.0
    lo = max(20.0, fc - bw / 2.0)
    hi = min(nyquist - 1.0, fc + bw / 2.0)
    if hi <= lo:
        # Degenerate: make the narrowest valid band we can.
        lo = max(20.0, fc * 0.95)
        hi = min(nyquist - 1.0, fc * 1.05)
        if hi <= lo:
            hi = lo + 1.0
    return iirfilter(
        N=order,
        Wn=[lo, hi],
        btype="band",
        ftype="butter",
        output="sos",
        fs=sample_rate,
    )


class BandPassFilter:
    """Wraps an SOS band-pass with persistent filter state across blocks."""

    def __init__(self, sample_rate: int) -> None:
        self._sr = int(sample_rate)
        self._sos: Optional[np.ndarray] = None
        self._zi: Optional[np.ndarray] = None
        self._fc = 1000.0
        self._bw = 500.0

    @property
    def fc(self) -> float:
        return self._fc

    @property
    def bw(self) -> float:
        return self._bw

    def set_params(self, fc: float, bw: float) -> None:
        """Rebuild the SOS. Keeps the existing ``zi`` when possible.

        When the number of sections changes we must re-initialise ``zi``; for
        a fixed-order filter the shape stays constant so a straight rebind is
        enough.
        """
        self._fc = float(fc)
        self._bw = float(bw)
        new_sos = make_bandpass_sos(self._fc, self._bw, self._sr)
        if self._zi is None or self._sos is None or self._zi.shape[0] != new_sos.shape[0]:
            self._zi = sosfilt_zi(new_sos) * 0.0  # start from silence, not steady state
        self._sos = new_sos

    def reset(self) -> None:
        if self._sos is None:
            return
        self._zi = sosfilt_zi(self._sos) * 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter a mono block in place (returns a new array)."""
        if self._sos is None:
            return block
        out, self._zi = sosfilt(self._sos, block, zi=self._zi)
        return out.astype(np.float32, copy=False)

    def sos_copy(self) -> Optional[np.ndarray]:
        return None if self._sos is None else self._sos.copy()
