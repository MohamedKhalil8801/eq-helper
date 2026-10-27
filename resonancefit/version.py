"""The customer app's version.

Release builds carry ``assets/version.txt`` (written by CI from the tag);
source runs fall back to the installed package metadata, then "dev".
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
VERSION_FILE = _ROOT / "resonancefit" / "assets" / "version.txt"


def app_version(version_file: Path = VERSION_FILE) -> str:
    try:
        text = version_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        return metadata.version("eq-helper")
    except metadata.PackageNotFoundError:
        return "dev"
