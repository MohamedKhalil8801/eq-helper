"""Parametric EQ exports: fit within an app's limits, write that app's syntax.

Writers (chosen per catalog entry):

* ``apo_filters`` — one Equalizer APO-style file, ``Channel: L/R`` blocks.
* ``autoeq_text`` — AutoEq ``ParametricEQ.txt`` style, one file per side
  (what Wavelet, Poweramp, Peace and many others import).
* ``csv`` — a plain table for apps you type values into by hand.

Every file states how close the fit is ("within ±1 dB up to 10 kHz").
"""

from __future__ import annotations

from pathlib import Path

from eq_helper.dsp.parametric_fit import ParametricEQ, PeqLimits, fit_stereo
from eq_helper.dsp.resonance import ChannelEQ

from .base import ExportMeta, register

WRITERS = ("apo_filters", "autoeq_text", "csv")


def limits_from_options(options: dict) -> PeqLimits:
    raw = dict(options.get("limits") or {})
    for key in ("gain_range", "q_range", "fc_range", "types"):
        if key in raw:
            raw[key] = tuple(raw[key])
    if "sample_rate" in options and "sample_rate" not in raw:
        raw["sample_rate"] = int(options["sample_rate"])
    return PeqLimits(**raw)


def _filter_line(n: int, f) -> str:
    return f"Filter {n}: ON {f.type} Fc {f.fc:.0f} Hz Gain {f.gain_db:.1f} dB Q {f.q:.2f}"


class ParametricFormat:
    id = "parametric"
    options_schema = {
        "writer": {"choices": WRITERS, "default": "autoeq_text"},
        "limits": {"type": "PeqLimits fields"},
        "sample_rate": {"default": 48000},
    }

    def fit(self, channel_eq: ChannelEQ, options: dict) -> dict[str, ParametricEQ]:
        curves = {side: (ch.freqs, ch.correction_db) for side, ch in channel_eq.channels.items()}
        return fit_stereo(curves, limits_from_options(options))

    def export(self, channel_eq: ChannelEQ, options: dict, folder: Path, base_name: str,
               meta: ExportMeta) -> list[Path]:
        writer = options.get("writer", "autoeq_text")
        if writer not in WRITERS:
            raise ValueError(f"Unknown parametric writer {writer!r}; choose from {WRITERS}.")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        fits = self.fit(channel_eq, options)
        head = [f"# {line}" for line in meta.header_lines()]
        head += [f"# Accuracy {side}: {peq.accuracy.describe()}" for side, peq in fits.items()]
        preamp = next(iter(fits.values())).preamp_db

        if writer == "apo_filters":
            lines = head + [f"Preamp: {preamp:.1f} dB"]
            stereo = len(fits) > 1
            for side, peq in fits.items():
                if stereo:
                    lines.append(f"Channel: {side}")
                lines += [_filter_line(n, f) for n, f in enumerate(peq.filters, 1)]
            if stereo:
                lines.append("Channel: ALL")
            path = folder / f"{base_name}.txt"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return [path]

        if writer == "autoeq_text":
            written = []
            for side, peq in fits.items():
                suffix = "" if side == "M" else f" {side}"
                path = folder / f"{base_name}{suffix} ParametricEQ.txt"
                lines = head + [f"Preamp: {preamp:.1f} dB"]
                lines += [_filter_line(n, f) for n, f in enumerate(peq.filters, 1)]
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                written.append(path)
            return written

        lines = head + ["side,type,fc_hz,gain_db,q", f"preamp,,,{preamp:.1f},"]
        for side, peq in fits.items():
            lines += [f"{side},{f.type},{f.fc:.0f},{f.gain_db:.1f},{f.q:.2f}" for f in peq.filters]
        path = folder / f"{base_name} filters.csv"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return [path]


register(ParametricFormat())
