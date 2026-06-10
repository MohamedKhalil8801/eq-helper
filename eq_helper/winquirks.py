"""Windows startup quirks.

On Python 3.12+, ``platform.win32_ver()`` issues a WMI query
(``platform._wmi_query`` → ``_wmi.exec_query``) that can block FOREVER when the
Windows WMI service is wedged — a state some machines enter after changing
audio devices (CPython gh-112278). Many libraries call ``platform.system()`` /
``platform.machine()`` at import time (numpy.testing, sounddevice, …), and
``platform.uname()`` runs the WMI query eagerly, so a wedged WMI froze this app
before any window appeared: the process existed but the UI never showed, and
only a reboot (which resets WMI) recovered the machine.

``platform`` ships a full non-WMI fallback (``sys.getwindowsversion()`` + the
registry) that kicks in when the WMI query raises ``OSError``. We force that
path by replacing the private hook with one that raises immediately. The values
``platform.*`` returns are the same in practice — just sourced without WMI.
"""

from __future__ import annotations

import sys


def disable_wmi_platform_queries() -> None:
    """Make ``platform.*`` never touch WMI in this process. Call FIRST, before
    importing anything that might call ``platform.system()``/``machine()``."""
    if sys.platform != "win32":
        return
    try:
        import platform

        if hasattr(platform, "_wmi_query"):
            def _no_wmi(*_args: object, **_kwargs: object) -> tuple:
                raise OSError(
                    "WMI queries disabled by eq-helper: they can hang forever "
                    "on a wedged WMI service (CPython gh-112278)"
                )

            platform._wmi_query = _no_wmi  # type: ignore[attr-defined]
    except Exception:
        # Worst case we keep stock behavior; never break startup over this.
        pass
