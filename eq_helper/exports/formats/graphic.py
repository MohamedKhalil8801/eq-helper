"""GraphicEQ text: the exact correction as frequency/gain pairs.

Default: an Equalizer APO file (shared preamp, one ``GraphicEQ:`` line per
channel). Options for other apps:

* ``single_line`` — just ``GraphicEQ: ...`` for one channel (AutoEq
  ``GraphicEQ.txt`` style, e.g. Wavelet); the EQ must be shared-only.
* ``max_bands`` — resample to at most this many log-spaced bands.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from eq_helper.dsp.resonance import ChannelEQ, interp

from .base import ExportMeta, register


def _limit_bands(channel_eq: ChannelEQ, max_bands: int | None) -> ChannelEQ:
    if not max_bands:
        return channel_eq
    channels = {}
    for side, ch in channel_eq.channels.items():
        if len(ch.freqs) <= max_bands:
            channels[side] = ch
            continue
        grid = np.geomspace(max(20.0, ch.freqs[0]), min(20000.0, ch.freqs[-1]), int(max_bands))
        channels[side] = replace(ch, freqs=grid, predicted_db=interp(ch.freqs, ch.predicted_db, grid),
                                 target_db=interp(ch.freqs, ch.target_db, grid),
                                 correction_db=interp(ch.freqs, ch.correction_db, grid))
    return replace(channel_eq, channels=channels)


class GraphicEQFormat:
    id = "graphic_eq"
    options_schema = {"single_line": {"default": False}, "max_bands": {"default": None}}

    def export(self, channel_eq: ChannelEQ, options: dict, folder: Path, base_name: str,
               meta: ExportMeta) -> list[Path]:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        eq = _limit_bands(channel_eq, options.get("max_bands"))
        path = folder / f"{base_name}.txt"
        if options.get("single_line"):
            if len(eq.channels) != 1:
                raise ValueError("A single GraphicEQ line holds one channel; reduce to shared-only first.")
            (ch,) = eq.channels.values()
            path.write_text(ch.graphic_text(), encoding="utf-8")
            readme = folder / f"{base_name} README.txt"
            readme.write_text("\n".join(meta.header_lines() + [f"Preamp: {eq.preamp_db:.1f} dB (set in the app if it has one)"])
                              + "\n", encoding="utf-8")
            return [path, readme]
        comments = "".join(f"# {line}\n" for line in meta.header_lines())
        path.write_text(comments + eq.apo_text(), encoding="utf-8")
        return [path]


register(GraphicEQFormat())
