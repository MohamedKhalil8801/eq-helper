"""PyInstaller runtime hook: neutralize platform's WMI queries.

Runs before ANY application or library import in the frozen app. Python 3.12+
``platform.win32_ver()`` issues a WMI query that blocks forever on a wedged
Windows WMI service (a state triggered on some machines by changing audio
devices — CPython gh-112278). numpy/scipy/sounddevice all call ``platform.*``
at import time, which froze the exe before the window could appear. The stdlib
has a designed non-WMI fallback (``sys.getwindowsversion()`` + registry); make
the WMI hook raise so that fallback is always taken.

Self-contained on purpose: runtime hooks execute too early to rely on app
modules (eq_helper.winquirks mirrors this for source runs).
"""

import sys

if sys.platform == "win32":
    try:
        import platform

        if hasattr(platform, "_wmi_query"):
            def _no_wmi(*_args, **_kwargs):
                raise OSError(
                    "WMI queries disabled by eq-helper (can hang forever on a "
                    "wedged WMI service; CPython gh-112278)"
                )

            platform._wmi_query = _no_wmi
    except Exception:
        pass
