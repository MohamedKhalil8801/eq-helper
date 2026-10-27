"""Common test fixtures and path setup.

Adds the project root to ``sys.path`` so tests can import the ``eq_helper``
package without requiring ``pip install -e .``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Neutralize platform's WMI queries before test modules import numpy/scipy —
# on a wedged Windows WMI service those queries block for minutes to forever
# (the same hang the app itself guards against via eq_helper.app).
from eq_helper.winquirks import disable_wmi_platform_queries  # noqa: E402

disable_wmi_platform_queries()

# Use the offscreen Qt platform in tests that don't need a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never open a real PortAudio output during tests — the realtime thread
# interacts badly with pytest teardown on Windows (access violations on the
# enum machinery). The AudioEngine is still fully exercised via
# ``engine.process_block`` which drives the callback synchronously.
os.environ.setdefault("EQ_HELPER_NO_AUDIO", "1")


# On GitHub Actions, report each failed test as an error annotation (visible on
# the run page and through the API, without downloading logs).
if os.environ.get("GITHUB_ACTIONS"):
    def _escape(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")

    def pytest_runtest_logreport(report):  # noqa: D103
        if not report.failed:
            return
        path, line, _ = report.location
        message = str(report.longrepr)[-3000:]
        title = f"{report.nodeid} ({report.when})"
        print(f"\n::error file={path},line={(line or 0) + 1},title={_escape(title)}::{_escape(message)}", flush=True)
