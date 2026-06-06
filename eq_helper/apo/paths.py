"""Resolve the Equalizer APO ``config\`` directory.

Default: ``C:\\Program Files\\EqualizerAPO\\config``. A user-supplied override
takes precedence and is treated as authoritative even if it doesn't look like a
standard APO install (we assume the user knows what they're doing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from eq_helper.apo.errors import ApoNotFoundError

DEFAULT_APO_CONFIG_DIR = Path(r"C:\Program Files\EqualizerAPO\config")
ALT_APO_CONFIG_DIRS = (
    Path(r"C:\Program Files (x86)\EqualizerAPO\config"),
)
MAIN_CONFIG_NAME = "config.txt"
TOOL_CONFIG_NAME = "eq_helper.txt"


def resolve_apo_config_dir(override: Optional[str | Path] = None) -> Path:
    """Return a validated APO config dir.

    Precedence:

    1. ``override`` if supplied and exists.
    2. :data:`DEFAULT_APO_CONFIG_DIR` if it exists.
    3. Each entry in :data:`ALT_APO_CONFIG_DIRS` in turn.

    Raises :class:`ApoNotFoundError` if nothing matches.
    """
    if override is not None:
        p = Path(override)
        if p.is_dir():
            return p
        raise ApoNotFoundError(f"configured APO path does not exist: {p}")

    for candidate in (DEFAULT_APO_CONFIG_DIR, *ALT_APO_CONFIG_DIRS):
        if candidate.is_dir():
            return candidate

    raise ApoNotFoundError(
        "Could not find an Equalizer APO config directory. "
        "Install Equalizer APO or set the path override in Settings."
    )
