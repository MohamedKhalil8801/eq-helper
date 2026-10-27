"""Best-effort detection of installed EQ apps, driven by catalog hints.

Read-only probes, desktop only: Windows known paths and registry uninstall
entries (HKCU/HKLM), macOS ``.app`` bundles, Linux commands on PATH and
Flatpak ids. Equalizer APO is detected the way the APO bridge finds it.
Detection never raises, never hides an app (it only adds an "Installed"
badge and sorts first) and stops at a short deadline.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

from eq_helper.apo.paths import resolve_apo_config_dir

APO_APP_ID = "equalizer-apo"
_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def read_uninstall_names() -> list[str]:
    """DisplayName of every uninstall entry (HKCU + HKLM); [] off Windows."""
    if not sys.platform.startswith("win"):
        return []
    import winreg  # noqa: PLC0415 - Windows only

    names = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in _UNINSTALL_KEYS:
            try:
                key = winreg.OpenKey(hive, key_path)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            names.append(str(winreg.QueryValueEx(sub, "DisplayName")[0]))
                    except OSError:
                        continue
    return names


def _expand(path: str, env) -> Path | None:
    missing = False

    def sub(match):
        nonlocal missing
        value = env.get(match.group(1))
        if value is None:
            missing = True
            return ""
        return value
    expanded = re.sub(r"%([^%]+)%", sub, path)
    return None if missing else Path(expanded.replace("\\", "/"))


def detect_installed(catalog, platform: str, *, env=None, exists=None, which=None, registry_names=None,
                     app_dirs=None, flatpak_dirs=None, apo_override=None, apo_resolver=None,
                     timeout: float = 1.5) -> set[str]:
    """Return ids of catalog apps that look installed on this computer."""
    env = os.environ if env is None else env
    exists = exists or (lambda p: Path(p).exists())
    which = which or shutil.which
    deadline = time.monotonic() + timeout
    found: set[str] = set()

    def safe(probe, *args):
        if time.monotonic() > deadline:
            return False
        try:
            return bool(probe(*args))
        except Exception:  # noqa: BLE001 - detection must never fail the app
            return False

    if platform not in ("windows", "macos", "linux"):
        return found

    registry = None
    if platform == "windows":
        if apo_override is not None or apo_resolver is not None or sys.platform.startswith("win"):
            resolver = apo_resolver or resolve_apo_config_dir
            if safe(resolver, apo_override):
                found.add(APO_APP_ID)
        try:
            registry = [n.casefold() for n in (registry_names or read_uninstall_names)()]
        except Exception:  # noqa: BLE001
            registry = []

    if app_dirs is None:
        app_dirs = [Path("/Applications"), Path.home() / "Applications"]
    if flatpak_dirs is None:
        flatpak_dirs = [Path.home() / ".local/share/flatpak/app", Path("/var/lib/flatpak/app")]

    for app in catalog.apps:
        hints = app.detection.get(platform) or {}
        if app.id in found or platform not in app.platforms or not hints:
            continue
        hit = False
        if platform == "windows":
            for raw in hints.get("paths", []):
                path = _expand(raw, env)
                if path is not None and safe(exists, path):
                    hit = True
                    break
            if not hit and registry:
                hit = any(h.casefold() in name for h in hints.get("registry_uninstall", []) for name in registry)
        elif platform == "macos":
            hit = any(safe(exists, d / bundle) for bundle in hints.get("bundles", []) for d in app_dirs)
        else:
            hit = (any(safe(which, cmd) for cmd in hints.get("commands", []))
                   or any(safe(exists, d / fid) for fid in hints.get("flatpak", []) for d in flatpak_dirs))
        if hit:
            found.add(app.id)
    return found
