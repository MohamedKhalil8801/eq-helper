"""TuningSession: the customer's choices for one calibration, without Qt.

A *tuning* (see GLOSSARY) is the primary resonance per side, the optional
upper resonance per side, and the target. The session turns the current
choices into a :class:`~eq_helper.dsp.resonance.ChannelEQ`, and snapshots /
restores them as a :class:`Tuning` — the basis for history entries.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

import numpy as np

from eq_helper.dsp.resonance import (
    CONTROL_RANGE, PERSONAL_TREBLE_RANGE, ChannelEQ, Measurements, match_channels,
)

DEFAULT_PRIMARY_HZ = 7700.0


def target_hash(freqs, db) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(freqs, dtype="<f8").tobytes())
    h.update(b"|")
    h.update(np.ascontiguousarray(db, dtype="<f8").tobytes())
    return h.hexdigest()


def _hz(value, what) -> float:
    try:
        hz = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{what} must be a valid frequency.") from exc
    if not math.isfinite(hz):
        raise ValueError(f"{what} must be a valid frequency.")
    return hz


@dataclass(frozen=True)
class Tuning:
    """One saved set of customer choices (immutable, JSON-friendly)."""

    primary: dict[str, float]
    upper: dict[str, float] = field(default_factory=dict)
    upper_enabled: bool = False
    target_name: str = ""
    target_path: str | None = None
    target_hash: str | None = None

    def to_dict(self) -> dict:
        return {"primary": dict(self.primary), "upper": dict(self.upper),
                "upper_enabled": self.upper_enabled, "target_name": self.target_name,
                "target_path": self.target_path, "target_hash": self.target_hash}

    @classmethod
    def from_dict(cls, data: dict) -> "Tuning":
        return cls({str(k): float(v) for k, v in data["primary"].items()},
                   {str(k): float(v) for k, v in (data.get("upper") or {}).items()},
                   bool(data.get("upper_enabled", False)), str(data.get("target_name", "")),
                   data.get("target_path"), data.get("target_hash"))


class TuningSession:
    def __init__(self, measurements: Measurements):
        self.measurements = measurements
        self.primary: dict[str, float] = {s: DEFAULT_PRIMARY_HZ for s in measurements.models}
        self.upper: dict[str, float] = {}
        self.upper_enabled = False
        self.target: tuple[np.ndarray, np.ndarray] | None = None
        self.target_name = ""
        self.target_path: str | None = None

    @property
    def sides(self) -> tuple[str, ...]:
        return tuple(self.measurements.models)

    def _side(self, side) -> str:
        if side not in self.measurements.models:
            raise ValueError(f"Unknown side {side!r}; this calibration has sides {self.sides}.")
        return side

    # ── choices ────────────────────────────────────────────────────────────
    def set_measurements(self, measurements: Measurements, primary=None) -> None:
        self.measurements = measurements
        primary = primary or {}
        self.primary = {s: float(np.clip(_hz(primary.get(s, DEFAULT_PRIMARY_HZ), "Primary resonance"),
                                         *CONTROL_RANGE))
                        for s in measurements.models}

    def set_primary(self, side, hz) -> float:
        value = float(np.clip(_hz(hz, "Primary resonance"), *CONTROL_RANGE))
        self.primary[self._side(side)] = value
        return value

    def set_upper(self, side, hz) -> float:
        value = _hz(hz, "Upper resonance")
        lo, hi = PERSONAL_TREBLE_RANGE
        if not lo <= value <= hi:
            raise ValueError(f"Upper resonance must be between {lo:,} and {hi:,} Hz.")
        self.upper[self._side(side)] = value
        return value

    def clear_upper(self, side) -> None:
        self.upper.pop(side, None)

    def set_target(self, freqs, db, name: str, path: str | None = None) -> None:
        """Set the target; raises ``ValueError`` (keeping the old one) if unusable."""
        freqs, db = np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)
        match_channels(self.measurements, self.primary, freqs, db, name)  # validate
        self.target = (freqs, db)
        self.target_name = name
        self.target_path = None if path is None else str(path)

    # ── results ────────────────────────────────────────────────────────────
    def channel_eq(self) -> ChannelEQ:
        if self.target is None:
            raise ValueError("Choose a target before generating an EQ.")
        upper = self.upper if self.upper_enabled else None
        return match_channels(self.measurements, self.primary, *self.target, self.target_name,
                              personal_treble=upper)

    def snapshot(self) -> Tuning:
        return Tuning(dict(self.primary), dict(self.upper), self.upper_enabled, self.target_name,
                      self.target_path, None if self.target is None else target_hash(*self.target))

    def restore(self, tuning: Tuning, target=None) -> None:
        """Restore ``tuning``. ``target`` is the (freqs, db) curve it was made with;
        it may be omitted only when that same curve is already loaded."""
        if set(tuning.primary) != set(self.measurements.models):
            raise ValueError(f"Tuning sides {sorted(tuning.primary)} do not match this calibration's "
                             f"sides {sorted(self.measurements.models)}.")
        if target is not None:
            freqs, db = np.asarray(target[0], dtype=float), np.asarray(target[1], dtype=float)
            if tuning.target_hash is not None and target_hash(freqs, db) != tuning.target_hash:
                raise ValueError("The supplied target curve does not match the saved tuning.")
            new_target = (freqs, db)
        elif self.target is not None and target_hash(*self.target) == tuning.target_hash:
            new_target = self.target
        elif tuning.target_hash is None:
            new_target = None
        else:
            raise ValueError("The tuning's target curve is not loaded; supply it to restore.")
        primary = {s: float(np.clip(_hz(v, "Primary resonance"), *CONTROL_RANGE))
                   for s, v in tuning.primary.items()}
        if new_target is not None:
            match_channels(self.measurements, primary, *new_target, tuning.target_name)
        self.primary = primary
        self.upper = dict(tuning.upper)
        self.upper_enabled = tuning.upper_enabled
        self.target = new_target
        self.target_name = tuning.target_name
        self.target_path = tuning.target_path
