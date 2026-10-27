"""The customer app never imports EQ Helper's UI, the admin tools, the mic or optimize code."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_PREFIXES = ("eq_helper.ui", "eq_helper.admin")
FORBIDDEN_MODULES = ("eq_helper.dsp.eq_optimize", "eq_helper.ui.mic_panel", "eq_helper.dsp.sweep_measurement",
                     "eq_helper.ui.live_balance_dialog", "eq_helper.dsp.balance_eq")

PROBE = """
import json, sys
import resonancefit.app, resonancefit.ui.main_window, resonancefit.selftest
for name in ("export_flow", "app_catalog_page", "test_songs", "sound_tools", "settings_page", "about_page",
             "walkthrough", "tuning_panel", "history_panel", "listen_save", "header_card", "import_screen"):
    __import__("resonancefit.ui." + name)
print(json.dumps(sorted(m for m in sys.modules if m.startswith("eq_helper"))))
"""


def test_customer_app_imports_stay_inside_the_boundary():
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=180)
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    bad = [m for m in loaded if m.startswith(FORBIDDEN_PREFIXES) or m in FORBIDDEN_MODULES]
    assert bad == [], f"customer app pulled in operator-only modules: {bad}"
    assert "eq_helper.widgets.convolution_export_dialog" in loaded  # the shared, allowed home
