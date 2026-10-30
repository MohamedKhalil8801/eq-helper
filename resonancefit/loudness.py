"""Level-matching the guided test's cards.

Each card is the same sound through a different EQ, and a louder card tends to
sound "better" whatever its shape. So every card in a round gets a gain after
its EQ that makes it as loud as the quietest card, for the sound being played.

Loudness here is a simple model of hearing: the sound's spectrum through the
EQ, weighted by how sensitive the ear is at each frequency, summed per
frequency band with Stevens' power law (loudness grows as intensity^0.3).
"""

from __future__ import annotations

import numpy as np

from eq_helper.audio.engine import GREY_HIGHPASS_HZ, region_gain

from .audio import FOCUS_WIDTH_OCTAVES, REGION_OCTAVES

GRID = np.geomspace(20.0, 20000.0, 481)  # 48 points per octave: equal weight per band is pink
EXPONENT = 0.3  # Stevens: loudness ∝ intensity^0.3


def a_weighting_db(freqs) -> np.ndarray:
    """IEC 61672 A-weighting: roughly how much quieter each frequency sounds than 1 kHz."""
    f2 = np.asarray(freqs, dtype=float) ** 2
    ra = (12194.0 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
                                     * (f2 + 12194.0 ** 2))
    return 20 * np.log10(ra) + 2.0


def heard_weights(sound: str, centre_hz: float, freqs=GRID) -> np.ndarray:
    """Power per band of ``sound`` as heard (source spectrum × the ear's sensitivity)."""
    f = np.asarray(freqs, dtype=float)
    hearing = 10 ** (a_weighting_db(f) / 10)
    grey = (f / GREY_HIGHPASS_HZ) ** 8 / (1 + (f / GREY_HIGHPASS_HZ) ** 8)  # grey already sounds even
    if sound == "pink":
        return hearing
    if sound == "grey":
        return grey
    if sound == "region":
        return grey * region_gain(f, centre_hz, REGION_OCTAVES) ** 2
    if sound == "focus":  # a sine sweeping evenly (per octave) across the width
        inside = np.abs(np.log2(f / centre_hz)) <= FOCUS_WIDTH_OCTAVES / 2
        return hearing * inside
    raise ValueError(f"Unknown sound {sound!r}")


def level_offsets(curves, sound: str, centre_hz: float) -> list[float]:
    """Gain in dB (≤ 0) for each ``(freqs, gain_db)`` curve so all sound equally loud."""
    weights = heard_weights(sound, centre_hz)
    loudness = []
    for freqs, gain_db in curves:
        gain = np.interp(np.log(GRID), np.log(np.asarray(freqs, dtype=float)), np.asarray(gain_db, dtype=float))
        loudness.append(float(np.sum((weights * 10 ** (gain / 10)) ** EXPONENT)))
    quietest = min(loudness)
    # Scaling every band by G (power) scales loudness by G^EXPONENT.
    return [10 / EXPONENT * float(np.log10(quietest / n)) if n > 0 else 0.0 for n in loudness]


def card_curve(channel_eq, side: str):
    """The tested ear's response through ``channel_eq``: correction plus the shared preamp."""
    ch = channel_eq.channels.get(side) or next(iter(channel_eq.channels.values()))
    return ch.freqs, ch.correction_db + channel_eq.preamp_db
