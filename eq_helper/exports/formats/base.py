"""The export-format interface shared by every exporter.

A format turns a :class:`~eq_helper.dsp.resonance.ChannelEQ` into files.
Formats are code; which app takes which format, with what limits, is data
in the app catalog (ADR 0004).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from eq_helper.dsp.resonance import ChannelEQ, interp


@dataclass(frozen=True)
class ExportMeta:
    """Who and what an export is for; written into every file's comments/README."""

    customer: str
    iem: str
    date: str
    tuning: str = ""
    notes: tuple[str, ...] = ()  # e.g. the shared-only compromise

    def header_lines(self) -> list[str]:
        lines = [f"Customer: {self.customer}", f"IEM: {self.iem}", f"Date: {self.date}"]
        if self.tuning:
            lines.append(f"Tuning: {self.tuning}")
        return lines + [f"Note: {note}" for note in self.notes]

    def as_dict(self) -> dict[str, str]:
        return {"Customer": self.customer, "IEM": self.iem, "Date": self.date, "Tuning": self.tuning}


@runtime_checkable
class ExportFormat(Protocol):
    id: str
    options_schema: dict

    def export(self, channel_eq: ChannelEQ, options: dict, folder: Path, base_name: str,
               meta: ExportMeta) -> list[Path]:
        ...


_REGISTRY: dict[str, ExportFormat] = {}


def register(fmt: ExportFormat) -> ExportFormat:
    _REGISTRY[fmt.id] = fmt
    return fmt


def get_format(format_id: str) -> ExportFormat:
    return _REGISTRY[format_id]


def registered_formats() -> list[str]:
    return sorted(_REGISTRY)


def prepend_readme(path: Path, meta: ExportMeta, extra: list[str] | None = None) -> None:
    """Put customer/IEM/date/tuning (and any app notes) at the top of a README."""
    body = path.read_text(encoding="utf-8")
    head = meta.header_lines() + [""]
    if extra:
        head += extra + [""]
    path.write_text("\n".join(head) + "\n" + body, encoding="utf-8")


# ── shared-only reduction ───────────────────────────────────────────────────

BALANCE_BAND_HZ = (500.0, 2000.0)
BALANCE_MIN_DB = 0.05


@dataclass(frozen=True)
class SharedReduction:
    channel_eq: ChannelEQ          # one "M" channel for both ears
    balance_db: float | None       # + = right needs more level than left
    notes: tuple[str, ...]


def shared_only(channel_eq: ChannelEQ, balance_control: bool) -> SharedReduction:
    """Average L/R for apps without per-channel EQ (shared preamp kept).

    The left/right level difference around 1 kHz becomes a balance value when
    the app has a balance control; the averaged EQ keeps the rest.
    """
    if set(channel_eq.channels) != {"L", "R"}:
        return SharedReduction(channel_eq, None, ())
    left, right = channel_eq.channels["L"], channel_eq.channels["R"]
    grid = left.freqs

    def on_grid(eq, values):
        return interp(eq.freqs, values, grid)

    r_corr = on_grid(right, right.correction_db)
    mean = replace(left, peak_hz=float(np.sqrt(left.peak_hz * right.peak_hz)),
                   predicted_db=(left.predicted_db + on_grid(right, right.predicted_db)) / 2,
                   target_db=(left.target_db + on_grid(right, right.target_db)) / 2,
                   correction_db=(left.correction_db + r_corr) / 2,
                   prediction_note=f"L/R average of {left.peak_hz:.0f} / {right.peak_hz:.0f} Hz",
                   personal_peak_hz=None, coupler_treble_peak_hz=None,
                   applied_personal_peak_hz=None, personal_treble_band_hz=None)
    reduced = ChannelEQ({"M": mean}, channel_eq.target_name, channel_eq.preamp_db,
                        channel_eq.personal_treble_enabled)
    notes = ["This app uses one EQ for both ears, so the left/right correction is averaged "
             "(reduced L/R accuracy)."]
    balance = None
    if balance_control:
        band = (grid >= BALANCE_BAND_HZ[0]) & (grid <= BALANCE_BAND_HZ[1])
        diff = float(np.mean(r_corr[band] - left.correction_db[band]))
        if abs(diff) >= BALANCE_MIN_DB:
            balance = diff
            side = "right" if diff > 0 else "left"
            notes.append(f"Set the app's balance {abs(diff):.1f} dB towards the {side} ear.")
    return SharedReduction(reduced, balance, tuple(notes))
