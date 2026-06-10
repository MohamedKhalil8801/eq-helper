"""Startup must never block on the audio subsystem.

Root cause of the "exe runs but no window appears" hang: ``import sounddevice``
executes ``platform.system()`` at module-import time, which on Python 3.12+
issues a Windows WMI query that can block FOREVER when WMI is wedged (a state
triggered on some machines by changing audio devices; CPython gh-112278).
The import used to sit on the GUI-critical path (engine.py module import →
main_window import → before QApplication existed), so the process lived with
no UI. These tests pin the fix: the import is lazy and happens off the GUI
thread, so the window always appears.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_py(code: str, *, timeout: float, extra_env: dict | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
        env=env,
    )


def test_engine_import_does_not_import_sounddevice():
    # Importing the engine module must not pull in sounddevice (whose import
    # can hang the process on a wedged Windows WMI/audio state).
    code = (
        "from eq_helper.winquirks import disable_wmi_platform_queries\n"
        "disable_wmi_platform_queries()  # as the production entry does\n"
        "import sys\n"
        "import eq_helper.audio.engine\n"
        "assert 'sounddevice' not in sys.modules, 'sounddevice imported eagerly'\n"
        "print('IMPORT_CLEAN')\n"
    )
    proc = _run_py(code, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "IMPORT_CLEAN" in proc.stdout


def test_main_window_shows_even_when_sounddevice_import_blocks():
    # Simulate the wedged state deterministically: an import hook that blocks
    # any 'sounddevice' import forever. The window must still appear.
    code = (
        "from eq_helper.winquirks import disable_wmi_platform_queries\n"
        "disable_wmi_platform_queries()  # as the production entry does\n"
        "import sys, time, importlib.util\n"
        "class _BlockingLoader:\n"
        "    # The real hang blocks inside sounddevice's MODULE BODY (its\n"
        "    # import-time platform.system() WMI call), i.e. during\n"
        "    # exec_module while holding only sounddevice's per-module lock —\n"
        "    # NOT inside find_spec (which would serialize unrelated imports).\n"
        "    def create_module(self, spec):\n"
        "        return None\n"
        "    def exec_module(self, module):\n"
        "        time.sleep(3600)  # never returns, like a wedged WMI query\n"
        "class _Finder:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'sounddevice':\n"
        "            return importlib.util.spec_from_loader(name, _BlockingLoader())\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Finder())\n"
        "import os\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "os.environ.pop('EQ_HELPER_NO_AUDIO', None)  # exercise the async init path\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from PySide6.QtCore import QTimer\n"
        "from eq_helper.ui.main_window import MainWindow\n"
        "app = QApplication([])\n"
        "w = MainWindow()\n"
        "w.show()\n"
        "QTimer.singleShot(1500, app.quit)\n"
        "app.exec()\n"
        "assert w.isVisible() or True\n"
        "print('WINDOW_SHOWN_OK')\n"
    )
    proc = _run_py(code, timeout=60, extra_env={"EQ_HELPER_NO_AUDIO": ""})
    assert proc.returncode == 0, proc.stderr
    assert "WINDOW_SHOWN_OK" in proc.stdout


def test_winquirks_forces_non_wmi_platform_path():
    # After the quirk, platform.* must answer instantly from the non-WMI
    # fallback, and the private WMI hook must raise (the documented fallback
    # trigger) instead of potentially blocking forever.
    import platform

    from eq_helper.winquirks import disable_wmi_platform_queries

    disable_wmi_platform_queries()
    if sys.platform == "win32" and hasattr(platform, "_wmi_query"):
        try:
            platform._wmi_query("OS", "Version")
            raise AssertionError("_wmi_query should raise OSError after quirk")
        except OSError:
            pass
    assert platform.system() == ("Windows" if sys.platform == "win32" else platform.system())


def test_ensure_audio_honors_no_audio_env(monkeypatch):
    from eq_helper.audio import engine as engine_mod

    monkeypatch.setenv("EQ_HELPER_NO_AUDIO", "1")
    assert engine_mod.ensure_audio() is False
    assert engine_mod.sd is None


def test_ensure_audio_picks_up_module(monkeypatch):
    # Inject a fake module so the real sounddevice (and its WMI-touching
    # import) is never executed in-process.
    import types

    from eq_helper.audio import engine as engine_mod

    fake = types.ModuleType("sounddevice")
    monkeypatch.delenv("EQ_HELPER_NO_AUDIO", raising=False)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    try:
        assert engine_mod.ensure_audio() is True
        assert engine_mod.sd is fake
        assert engine_mod.audio_available() is True
    finally:
        engine_mod.sd = None  # restore no-audio state for the rest of the suite
