"""The export-format interface shared by every exporter.

A format turns a :class:`~eq_helper.dsp.resonance.ChannelEQ` into files.
Formats are code; which app takes which format, with what limits, is data
in the app catalog (ADR 0004).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from eq_helper.dsp.resonance import ChannelEQ


@dataclass(frozen=True)
class ExportMeta:
    """Who and what an export is for; written into every file's comments/README."""

    customer: str
    iem: str
    date: str
    tuning: str = ""

    def header_lines(self) -> list[str]:
        lines = [f"Customer: {self.customer}", f"IEM: {self.iem}", f"Date: {self.date}"]
        if self.tuning:
            lines.append(f"Tuning: {self.tuning}")
        return lines

    def as_dict(self) -> dict[str, str]:
        return dict(line.split(": ", 1) for line in self.header_lines())


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
