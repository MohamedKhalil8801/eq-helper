r"""Resolve the Equalizer APO ``config\`` directory.

Default: ``C:\\Program Files\\EqualizerAPO\\config``; a custom install folder
is found through the installer's registry entry. A user-supplied override
takes precedence and is treated as authoritative even if it doesn't look like a
standard APO install (we assume the user knows what they're doing).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from eq_helper.apo.errors import ApoNotFoundError

DEFAULT_APO_CONFIG_DIR = Path(r"C:\Program Files\EqualizerAPO\config")
ALT_APO_CONFIG_DIRS = (
    Path(r"C:\Program Files (x86)\EqualizerAPO\config"),
)
MAIN_CONFIG_NAME = "config.txt"
TOOL_CONFIG_NAME = "eq_helper.txt"


REGISTRY_KEY = r"Software\EqualizerAPO"
REGISTRY_VALUE = "ConfigPath"


def registry_config_path() -> Optional[str]:
    """The config path Equalizer APO's installer records in
    ``HKLM\\Software\\EqualizerAPO\\ConfigPath`` (custom install folders), or None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY, 0, flags) as key:
            value, _kind = winreg.QueryValueEx(key, REGISTRY_VALUE)
            return str(value) if value else None
    except OSError:
        return None


def resolve_apo_config_dir(override: Optional[str | Path] = None, *,
                           registry: Optional[Callable[[], Optional[str]]] = None) -> Path:
    """Return a validated APO config dir.

    Precedence:

    1. ``override`` if supplied and exists.
    2. The installer's registry entry (``ConfigPath``), if it exists.
    3. :data:`DEFAULT_APO_CONFIG_DIR` if it exists.
    4. Each entry in :data:`ALT_APO_CONFIG_DIRS` in turn.

    Raises :class:`ApoNotFoundError` if nothing matches.
    """
    if override is not None:
        p = Path(override)
        if p.is_dir():
            return p
        raise ApoNotFoundError(f"configured APO path does not exist: {p}")

    recorded = (registry or registry_config_path)()
    if recorded and Path(recorded).is_dir():
        return Path(recorded)

    for candidate in (DEFAULT_APO_CONFIG_DIR, *ALT_APO_CONFIG_DIRS):
        if candidate.is_dir():
            return candidate

    raise ApoNotFoundError(
        "Could not find an Equalizer APO config directory. "
        "Install Equalizer APO or set the path override in Settings."
    )
