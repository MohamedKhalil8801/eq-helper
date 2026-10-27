"""Targets the customer can pick: shipped, bundled and their own imports.

Sources, highest precedence first (same file name = same target):

1. ``user`` — the data folder's ``Targets/`` (customer imports),
2. ``shipped`` — ``Targets/`` next to the app (operator's set in the download,
   Windows/Linux),
3. ``bundled`` — the targets built into the app (``assets/targets``), so the
   default target exists even if the download's folder is gone (and on macOS).

All folders are rescanned at launch. Levels are kept raw; matching aligns
targets at 1 kHz.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from eq_helper.dsp.overlay import load_frequency_response

from .paths import AppPaths

DEFAULT_TARGET = "PEQdB Diamond.txt"
TARGET_SUFFIXES = (".txt", ".csv")
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
BUNDLED_TARGETS_DIR = _ROOT / "assets" / "targets"


@dataclass(frozen=True, eq=False)
class TargetInfo:
    path: Path
    source: str  # "user" | "shipped" | "bundled"
    freqs: np.ndarray
    db: np.ndarray

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def name(self) -> str:
        return self.path.stem


@dataclass(frozen=True)
class InvalidTarget:
    path: Path
    reason: str


def load_target_curve(path) -> tuple[np.ndarray, np.ndarray]:
    """Load and validate a target. Raises ``ValueError`` with a plain reason."""
    freqs, db = load_frequency_response(path)  # OverlayLoadError is a ValueError
    freqs, db = np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)
    if len(freqs) < 2 or not (np.isfinite(freqs).all() and np.isfinite(db).all()):
        raise ValueError("The target needs at least two finite frequency/level rows.")
    if np.any(np.diff(freqs) <= 0):
        raise ValueError("The target has repeated frequencies.")
    if not freqs[0] <= 1000 <= freqs[-1]:
        raise ValueError("The target must cover 1 kHz so its level can be aligned.")
    return freqs, db


class TargetLibrary:
    def __init__(self, paths: AppPaths, bundled_dir: Path = BUNDLED_TARGETS_DIR):
        self.paths = paths
        self.bundled_dir = Path(bundled_dir)
        self.targets: list[TargetInfo] = []
        self.invalid: list[InvalidTarget] = []

    def _sources(self):
        yield "user", self.paths.user_targets_dir
        if self.paths.shipped_targets_dir is not None:
            yield "shipped", self.paths.shipped_targets_dir
        yield "bundled", self.bundled_dir

    def scan(self) -> tuple[list[TargetInfo], list[InvalidTarget]]:
        found: dict[str, TargetInfo] = {}
        invalid: list[InvalidTarget] = []
        for source, folder in self._sources():
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix.lower() not in TARGET_SUFFIXES:
                    continue
                key = path.name.casefold()
                if key in found:
                    continue
                try:
                    freqs, db = load_target_curve(path)
                except (OSError, ValueError) as exc:
                    invalid.append(InvalidTarget(path, str(exc)))
                    continue
                found[key] = TargetInfo(path, source, freqs, db)
        self.targets = sorted(found.values(), key=lambda t: t.name.casefold())
        self.invalid = invalid
        return self.targets, self.invalid

    def find(self, filename: str | None) -> TargetInfo | None:
        if not filename:
            return None
        key = Path(filename).name.casefold()
        return next((t for t in self.targets if t.filename.casefold() == key), None)

    def default_for(self, manifest_default: str | None) -> TargetInfo | None:
        """The calibration's default target, else PEQdB Diamond, else the first."""
        return (self.find(manifest_default) or self.find(DEFAULT_TARGET)
                or (self.targets[0] if self.targets else None))

    def import_file(self, path) -> TargetInfo:
        """Validate and copy a target into the customer's own targets folder."""
        src = Path(path)
        load_target_curve(src)
        folder = self.paths.user_targets_dir
        folder.mkdir(parents=True, exist_ok=True)
        dest, n = folder / src.name, 1
        while dest.exists() and not filecmp.cmp(dest, src, shallow=False):
            n += 1
            dest = folder / f"{src.stem} ({n}){src.suffix}"
        if not dest.exists():
            shutil.copyfile(src, dest)
        self.scan()
        return next(t for t in self.targets if t.path == dest)
