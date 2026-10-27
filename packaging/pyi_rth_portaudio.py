"""PyInstaller runtime hook (Linux): let sounddevice find the bundled PortAudio.

sounddevice looks PortAudio up with ctypes.util.find_library('portaudio'),
which only searches the system. The Linux build bundles libportaudio next to
the app; point that lookup at it. Runs before any app import.
"""

import os
import sys

if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
    import ctypes.util
    import glob

    _found = sorted(glob.glob(os.path.join(sys._MEIPASS, "libportaudio.so*")))  # type: ignore[attr-defined]
    if _found:
        _original = ctypes.util.find_library

        def _find_library(name, _original=_original, _path=_found[0]):
            return _path if name == "portaudio" else _original(name)

        ctypes.util.find_library = _find_library
