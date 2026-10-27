"""Product branding for the customer app, in one changeable place.

Every customer-facing string, file name and README takes the product name,
byline, contact and logo from :class:`Branding`, loaded from
``resonancefit/assets/branding.json`` (missing or invalid values fall back to
the defaults below).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path

# In a PyInstaller build the package data sits under sys._MEIPASS.
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
DEFAULT_BRANDING_PATH = _ROOT / "resonancefit" / "assets" / "branding.json"


@dataclass(frozen=True)
class Branding:
    product_name: str = "ResonanceFit"
    byline: str = "by Mohamed Khalil"
    contact_url: str = ""
    logo: str = ""  # file name relative to the branding file, optional
    base_dir: Path | None = None

    @property
    def title(self) -> str:
        return f"{self.product_name}, {self.byline}" if self.byline else self.product_name

    @property
    def file_stem(self) -> str:
        """Product name made safe for file and folder names on every OS."""
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", self.product_name).strip(" .") or "app"

    @property
    def logo_path(self) -> Path | None:
        if not self.logo:
            return None
        return (self.base_dir or DEFAULT_BRANDING_PATH.parent) / self.logo

    def __eq__(self, other):  # base_dir is where it was loaded from, not branding
        if not isinstance(other, Branding):
            return NotImplemented
        return all(getattr(self, f.name) == getattr(other, f.name)
                   for f in fields(self) if f.name != "base_dir")

    __hash__ = None


def load_branding(path: str | Path | None = None) -> Branding:
    path = Path(path) if path is not None else DEFAULT_BRANDING_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Branding()
    if not isinstance(data, dict):
        return Branding()
    values = {f.name: data[f.name] for f in fields(Branding)
              if f.name != "base_dir" and isinstance(data.get(f.name), str)}
    return Branding(**values, base_dir=path.parent)
