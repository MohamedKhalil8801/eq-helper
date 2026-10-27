"""Equalizer APO GraphicEQ text: the exact correction, one line per channel."""

from __future__ import annotations

from pathlib import Path

from eq_helper.dsp.resonance import ChannelEQ

from .base import ExportMeta, register


class GraphicEQFormat:
    id = "graphic_eq"
    options_schema: dict = {}

    def export(self, channel_eq: ChannelEQ, options: dict, folder: Path, base_name: str,
               meta: ExportMeta) -> list[Path]:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{base_name}.txt"
        comments = "".join(f"# {line}\n" for line in meta.header_lines())
        path.write_text(comments + channel_eq.apo_text(), encoding="utf-8")
        return [path]


register(GraphicEQFormat())
