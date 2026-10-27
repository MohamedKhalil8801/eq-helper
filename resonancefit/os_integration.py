"""Make ``.rescal`` files open in the app when double-clicked — per user, no admin.

* Windows: ``HKCU\\Software\\Classes`` file association for the app's exe.
* Linux (AppImage): a MIME type, a ``.desktop`` entry and an icon in the user's
  data folder, pointing at the AppImage file.
* macOS: declared in the app's Info.plist at build time; nothing to do here.

Offered once on first launch of a release build; never required.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from eq_helper.calibration.rescal import RESCAL_SUFFIX

MIME_TYPE = "application/x-rescal"


def prog_id(branding) -> str:
    return f"{branding.file_stem.replace(' ', '')}.Calibration"


def open_command(exe) -> str:
    return f'"{exe}" "%1"'


def target_executable(platform: str, env=None, frozen=None, executable=None) -> Path | None:
    """What a double-click should run, or None when this isn't a release build."""
    env = os.environ if env is None else env
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return None
    if platform == "windows":
        return Path(executable or sys.executable)
    if platform == "linux" and env.get("APPIMAGE"):
        return Path(env["APPIMAGE"])
    return None


# ── Windows ─────────────────────────────────────────────────────────────────

def _winreg():
    import winreg  # noqa: PLC0415 - Windows only
    return winreg


def windows_registered(exe, branding, winreg=None) -> bool:
    reg = winreg or _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"Software\Classes\{RESCAL_SUFFIX}") as key:
            if reg.QueryValueEx(key, "")[0] != prog_id(branding):
                return False
        with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id(branding)}\shell\open\command") as key:
            return reg.QueryValueEx(key, "")[0] == open_command(exe)
    except OSError:
        return False


def register_windows(exe, branding, winreg=None) -> None:
    reg = winreg or _winreg()
    pid = prog_id(branding)

    def put(path, value):
        with reg.CreateKey(reg.HKEY_CURRENT_USER, rf"Software\Classes\{path}") as key:
            reg.SetValueEx(key, "", 0, reg.REG_SZ, value)

    put(RESCAL_SUFFIX, pid)
    put(pid, f"{branding.product_name} calibration")
    put(rf"{pid}\DefaultIcon", f'"{exe}",0')
    put(rf"{pid}\shell\open\command", open_command(exe))
    if winreg is None:
        try:  # tell Explorer the associations changed
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except Exception:  # noqa: BLE001
            pass


# ── Linux ───────────────────────────────────────────────────────────────────

def _data_home(env) -> Path:
    return Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME", str(Path.home()))) / ".local" / "share")


def desktop_id(branding) -> str:
    return branding.file_stem.lower().replace(" ", "-")


def mime_xml(branding) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
            f'  <mime-type type="{MIME_TYPE}">\n'
            f'    <comment>{branding.product_name} calibration</comment>\n'
            f'    <glob pattern="*{RESCAL_SUFFIX}"/>\n'
            f'  </mime-type>\n</mime-info>\n')


def desktop_entry(branding, exec_path, icon_name: str) -> str:
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={branding.product_name}\nComment={branding.byline}\n"
            f'Exec="{exec_path}" %f\nIcon={icon_name}\n'
            f"MimeType={MIME_TYPE};\nCategories=AudioVideo;Audio;\nTerminal=false\n")


def linux_registered(appimage, branding, env=None) -> bool:
    env = os.environ if env is None else env
    entry = _data_home(env) / "applications" / f"{desktop_id(branding)}.desktop"
    try:
        return f'Exec="{appimage}" %f' in entry.read_text(encoding="utf-8")
    except OSError:
        return False


def register_linux(appimage, branding, icon: Path | None = None, env=None, run=None) -> None:
    env = os.environ if env is None else env
    injected = run is not None
    run = run or (lambda args: subprocess.run(args, check=False, capture_output=True, timeout=20))
    data = _data_home(env)
    name = desktop_id(branding)
    (data / "mime" / "packages").mkdir(parents=True, exist_ok=True)
    (data / "mime" / "packages" / f"{name}-rescal.xml").write_text(mime_xml(branding), encoding="utf-8")
    if icon is not None and icon.is_file():
        icons = data / "icons" / "hicolor" / "256x256" / "apps"
        icons.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(icon, icons / f"{name}.png")
    (data / "applications").mkdir(parents=True, exist_ok=True)
    (data / "applications" / f"{name}.desktop").write_text(desktop_entry(branding, appimage, name), encoding="utf-8")
    for args in (["update-mime-database", str(data / "mime")],
                 ["update-desktop-database", str(data / "applications")],
                 ["xdg-mime", "default", f"{name}.desktop", MIME_TYPE]):
        if injected or shutil.which(args[0]):
            try:
                run(args)
            except Exception:  # noqa: BLE001 - registration is best effort
                pass


# ── one entry point ─────────────────────────────────────────────────────────

class FileAssociation:
    def __init__(self, platform: str, branding, exe: Path | None, icon: Path | None = None, env=None,
                 winreg=None, run=None):
        self.platform, self.branding, self.exe, self.icon = platform, branding, exe, icon
        self.env, self.winreg, self.run = env, winreg, run

    @classmethod
    def detect(cls, platform: str, branding, icon=None) -> "FileAssociation":
        return cls(platform, branding, target_executable(platform), icon)

    @property
    def supported(self) -> bool:
        return self.exe is not None and self.platform in ("windows", "linux")

    def registered(self) -> bool:
        if not self.supported:
            return False
        if self.platform == "windows":
            return windows_registered(self.exe, self.branding, self.winreg)
        return linux_registered(self.exe, self.branding, self.env)

    def register(self) -> None:
        if self.platform == "windows":
            register_windows(self.exe, self.branding, self.winreg)
        elif self.platform == "linux":
            register_linux(self.exe, self.branding, self.icon, self.env, self.run)
