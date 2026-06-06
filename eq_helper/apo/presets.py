"""Preset library: device -> preset -> stacked EQ files, over Equalizer APO.

A **preset** is a device-scoped ``Include:`` in ``config.txt`` pointing at a
``.txt`` file; switching presets is a one-active radio within that device scope.
A preset file in turn ``Include:``s its **EQ files** (the actual filters), in
order, each individually enable-able — that's the stack. EQ-file contents are
edited by the EQ editor; this module only manages the files and include lists.

All writes go through :mod:`eq_helper.apo.writer` (atomic + one-shot backup).
App-created files live under ``<config>/eq-helper/<device>/{presets,eq}/``.
Imported EQ files are **copied** in (so the original is never mutated); imported
preset files are referenced in place (preset edits only touch include lines).
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from eq_helper.apo.include_file import (
    add_include,
    add_include_under_device,
    parse_includes,
    remove_include,
    reorder_includes,
    set_active_include_in_device,
    set_include_enabled,
)
from eq_helper.apo.paths import MAIN_CONFIG_NAME
from eq_helper.apo.scanner import (
    GLOBAL_DEVICE,
    device_raw_for_short,
    scan_config_includes,
    scan_devices,
)
from eq_helper.apo.writer import atomic_write_with_backup, edit_include_file


@dataclass(frozen=True)
class Preset:
    """A device-scoped preset (one ``Include:`` line in ``config.txt``)."""

    name: str           # display name (file stem)
    path: str           # resolved absolute path to the preset file
    include_path: str   # the include string exactly as written in config.txt
    is_active: bool
    device: str         # short device name (for display / folder naming)
    device_raw: str = ""  # full Device: value incl. {GUID} (for exact scoping)


@dataclass(frozen=True)
class EqFile:
    """One EQ file stacked inside a preset (one ``Include:`` in the preset file)."""

    name: str           # display name (file stem)
    path: str           # resolved absolute path to the EQ file
    include_path: str   # the include string exactly as written in the preset file
    enabled: bool


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str) -> str:
    return _INVALID.sub("", str(name)).strip()[:80] or "untitled"


class PresetLibrary:
    """Manages presets and their EQ-file stacks for an APO config directory."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = Path(config_dir)
        self._config_path = self._config_dir / MAIN_CONFIG_NAME
        self._root = self._config_dir / "eq-helper"

    # -------------------------------------------------------------- reads

    def _scan(self):
        return scan_config_includes(self._config_path)

    def list_devices(self) -> List[str]:
        return scan_devices(self._config_path)

    def list_presets(self, device: str) -> List[Preset]:
        out: List[Preset] = []
        for e in self._scan():
            if e.device == GLOBAL_DEVICE or e.device != device:
                continue
            out.append(
                Preset(
                    name=e.name,
                    path=os.path.normpath(str(e.resolved_path)),
                    include_path=e.include_path,
                    is_active=e.is_active,
                    device=device,
                    device_raw=e.device_raw,
                )
            )
        return out

    def active_preset(self, device: str) -> Optional[Preset]:
        for p in self.list_presets(device):
            if p.is_active:
                return p
        return None

    def list_eq_files(self, preset: Preset) -> List[EqFile]:
        text = self._read(Path(preset.path))
        base = Path(preset.path).parent
        out: List[EqFile] = []
        for ref in parse_includes(text):
            rp = Path(ref.path)
            resolved = str(rp) if rp.is_absolute() else os.path.normpath(str(base / rp))
            out.append(
                EqFile(
                    name=Path(ref.path).stem,
                    path=resolved,
                    include_path=ref.path,
                    enabled=ref.enabled,
                )
            )
        return out

    # ------------------------------------------------------------ presets

    def activate(self, preset: Preset) -> None:
        # Match the device scope by its full Device: value (preset.device_raw),
        # never the GUID-stripped short name, so we can't touch another
        # same-named device's includes.
        scope = preset.device_raw or self._device_raw(preset.device)
        edit_include_file(
            self._config_path,
            lambda t: set_active_include_in_device(
                t, scope, str(self._config_dir), preset.path
            ),
        )

    def create_preset(self, device: str, name: str) -> Preset:
        target = self._unique(self._managed_dir(device, "presets"), name)
        atomic_write_with_backup(target, f"# Preset: {name}\n")
        try:
            edit_include_file(
                self._config_path,
                lambda t: add_include_under_device(
                    t, device, str(target), enabled=False
                ),
            )
        except Exception:
            target.unlink(missing_ok=True)  # roll back the orphan preset file
            raise
        return Preset(
            name=target.stem,
            path=str(target),
            include_path=str(target),
            is_active=False,
            device=device,
            device_raw=self._device_raw(device),
        )

    def add_preset_from_file(self, device: str, file_path) -> Preset:
        abs_path = os.path.abspath(str(file_path))
        edit_include_file(
            self._config_path,
            lambda t: add_include_under_device(t, device, abs_path, enabled=False),
        )
        return Preset(
            name=Path(abs_path).stem,
            path=abs_path,
            include_path=abs_path,
            is_active=False,
            device=device,
            device_raw=self._device_raw(device),
        )

    def rename_preset(self, preset: Preset, new_name: str) -> Preset:
        if not self._is_managed(preset.path):
            raise ValueError("can only rename app-created presets")
        old_path = Path(preset.path)
        new_path = self._unique(old_path.parent, new_name)
        old_path.rename(new_path)
        new_abs = str(new_path)
        was_active = preset.is_active
        try:
            edit_include_file(
                self._config_path,
                lambda t: add_include_under_device(
                    remove_include(t, preset.include_path),
                    preset.device,
                    new_abs,
                    enabled=was_active,
                ),
            )
        except Exception:
            # Roll the file move back so disk and config.txt stay consistent.
            try:
                new_path.rename(old_path)
            except OSError:
                pass
            raise
        return Preset(
            name=new_path.stem,
            path=new_abs,
            include_path=new_abs,
            is_active=was_active,
            device=preset.device,
            device_raw=preset.device_raw,
        )

    def remove_preset(self, preset: Preset) -> None:
        # Edit config.txt first; only then delete (and never an external file).
        edit_include_file(
            self._config_path, lambda t: remove_include(t, preset.include_path)
        )
        if self._is_managed(preset.path):
            try:
                Path(preset.path).unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ EQ files

    def create_eq_file(self, preset: Preset, name: str) -> EqFile:
        target = self._unique(self._managed_dir(preset.device, "eq"), name)
        atomic_write_with_backup(target, "Preamp: 0 dB\n")
        try:
            edit_include_file(
                Path(preset.path), lambda t: add_include(t, str(target), enabled=True)
            )
        except Exception:
            target.unlink(missing_ok=True)  # roll back the orphan EQ file
            raise
        return EqFile(
            name=target.stem,
            path=str(target),
            include_path=str(target),
            enabled=True,
        )

    def add_eq_file_from_file(self, preset: Preset, file_path) -> EqFile:
        src = Path(file_path)
        target = self._unique(self._managed_dir(preset.device, "eq"), src.stem)
        shutil.copy2(src, target)  # copy in — never mutate the user's original
        try:
            edit_include_file(
                Path(preset.path), lambda t: add_include(t, str(target), enabled=True)
            )
        except Exception:
            target.unlink(missing_ok=True)  # roll back the orphan copy
            raise
        return EqFile(
            name=target.stem,
            path=str(target),
            include_path=str(target),
            enabled=True,
        )

    def set_eq_enabled(self, preset: Preset, eq_include_path: str, enabled: bool) -> None:
        edit_include_file(
            Path(preset.path),
            lambda t: set_include_enabled(t, eq_include_path, enabled),
        )

    def reorder_eq(self, preset: Preset, ordered_include_paths: List[str]) -> None:
        edit_include_file(
            Path(preset.path),
            lambda t: reorder_includes(t, ordered_include_paths),
        )

    def remove_eq(self, preset: Preset, eq_include_path: str) -> None:
        # Remove only the stack reference; never delete the EQ file (it may be
        # shared with another preset, or the user may re-add it).
        edit_include_file(
            Path(preset.path), lambda t: remove_include(t, eq_include_path)
        )

    # ------------------------------------------------------------ helpers

    def _device_raw(self, device: str) -> str:
        """Full Device: value for a short device name (falls back to the short
        name when no GUID/scope is found)."""
        return device_raw_for_short(self._config_path, device) or device

    def _managed_dir(self, device: str, kind: str) -> Path:
        return self._root / _safe(device) / kind

    def _unique(self, directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        base = _safe(name)
        cand = directory / f"{base}.txt"
        n = 2
        while cand.exists():
            cand = directory / f"{base} ({n}).txt"
            n += 1
        return cand

    def _is_managed(self, path: str) -> bool:
        p = os.path.normcase(os.path.abspath(str(path)))
        root = os.path.normcase(os.path.abspath(str(self._root)))
        return p == root or p.startswith(root + os.sep)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
