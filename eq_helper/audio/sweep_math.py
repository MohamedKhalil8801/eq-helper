"""Pure math for the focused sweep: choose a frequency band and duration.

Kept Qt- and engine-free so the band/duration rules can be unit-tested in
isolation. The band is centred *geometrically* on the target frequency (pitch
is logarithmic), so the target sits at the log-midpoint of the sweep.
"""

from __future__ import annotations

_F_MIN_HZ = 20.0
_F_MAX_HZ = 20000.0
_MIN_DURATION_S = 0.1


def focused_sweep_params(
    center_hz: float,
    width_octaves: float,
    seconds_per_octave: float,
    f_min: float = _F_MIN_HZ,
    f_max: float = _F_MAX_HZ,
) -> tuple[float, float, float]:
    """Return ``(f_start, f_end, duration_s)`` for a focused sweep.

    * ``f_start = center · 2^(-W/2)``, ``f_end = center · 2^(+W/2)`` where
      ``W = width_octaves`` — i.e. half the width to each side in log-frequency.
    * Both ends are clamped to ``[f_min, f_max]``.
    * ``duration_s = width_octaves · seconds_per_octave`` (one-way glide time),
      so the perceived glide speed stays constant as the width changes.
    """
    half = width_octaves / 2.0
    f_start = center_hz * (2.0 ** -half)
    f_end = center_hz * (2.0 ** half)

    f_start = max(f_min, min(f_max, f_start))
    f_end = max(f_min, min(f_max, f_end))

    # Guard the degenerate case (centre pinned against a bound): keep a tiny,
    # strictly-increasing band so the generator never sees f_start == f_end.
    if f_end <= f_start:
        f_end = min(f_max, f_start * (2.0 ** 0.05))
        if f_end <= f_start:
            f_start = max(f_min, f_end * (2.0 ** -0.05))

    duration_s = max(_MIN_DURATION_S, width_octaves * seconds_per_octave)
    return f_start, f_end, duration_s
