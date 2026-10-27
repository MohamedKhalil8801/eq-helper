"""Where the customer app keeps and finds things, per OS.

* Data folder (settings, imported calibrations, model cache, history, the
  customer's own targets) — never ``~/.eq_helper``:
  Windows ``%APPDATA%\\<Product>``, macOS ``~/Library/Application Support/<Product>``,
  Linux ``$XDG_DATA_HOME/<product>`` (default ``~/.local/share``).
* Calibrations/targets shipped with the download: ``Calibrations/`` and
  ``Targets/`` next to the app on Windows/Linux (next to the AppImage file
  via ``$APPIMAGE``). On macOS App Translocation hides the app's real
  location, so the data folder's ``Calibrations/`` is used instead (ADR 0001).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from eq_helper.calibration.branding import load_branding


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def is_translocated(app_dir: Path | None) -> bool:
    """macOS runs quarantined apps from a random read-only AppTranslocation path."""
    return app_dir is not None and "AppTranslocation" in Path(app_dir).parts


@dataclass(frozen=True)
class AppPaths:
    platform: str
    data_dir: Path
    app_dir: Path | None = None

    # ── construction ───────────────────────────────────────────────────────
    @staticmethod
    def default_data_dir(platform: str, env, home: Path) -> Path:
        name = load_branding().file_stem
        if platform == "windows":
            base = env.get("APPDATA")
            return (Path(base) if base else home / "AppData" / "Roaming") / name
        if platform == "macos":
            return home / "Library" / "Application Support" / name
        base = env.get("XDG_DATA_HOME")
        return (Path(base) if base else home / ".local" / "share") / name.lower()

    @staticmethod
    def detect_app_dir(frozen: bool, executable: str, env) -> Path | None:
        """Folder the customer sees the app in; ``None`` when running from source."""
        if not frozen:
            return None
        if env.get("APPIMAGE"):
            return Path(env["APPIMAGE"]).parent
        return Path(executable).parent

    @classmethod
    def detect(cls) -> "AppPaths":
        platform = current_platform()
        return cls(platform=platform,
                   data_dir=cls.default_data_dir(platform, os.environ, Path.home()),
                   app_dir=cls.detect_app_dir(bool(getattr(sys, "frozen", False)),
                                              sys.executable, os.environ))

    # ── locations ──────────────────────────────────────────────────────────
    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def library_dir(self) -> Path:
        """Imported copies of every calibration (survive the download being deleted)."""
        return self.data_dir / "library"

    @property
    def library_index(self) -> Path:
        return self.library_dir / "index.json"

    @property
    def model_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "models"

    @property
    def history_dir(self) -> Path:
        return self.data_dir / "history"

    @property
    def user_targets_dir(self) -> Path:
        """Targets the customer imported themselves."""
        return self.data_dir / "Targets"

    @property
    def uses_sibling_folders(self) -> bool:
        return self.platform != "macos" and self.app_dir is not None

    @property
    def calibrations_folder(self) -> Path:
        """The folder "Open calibrations folder" shows and new files are dropped in."""
        if self.uses_sibling_folders:
            return self.app_dir / "Calibrations"
        return self.data_dir / "Calibrations"

    @property
    def scan_dirs(self) -> list[Path]:
        return [self.calibrations_folder]

    @property
    def shipped_targets_dir(self) -> Path | None:
        """``Targets/`` shipped next to the app, when the OS lets us see it."""
        return self.app_dir / "Targets" if self.uses_sibling_folders else None


def should_show_import_screen(library) -> bool:
    """No calibration available: ask the customer to open the file they received."""
    return not library.list()
