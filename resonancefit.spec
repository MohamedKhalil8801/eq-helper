# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the customer app (one generic build per OS — ADR 0001).

    pyinstaller resonancefit.spec            # → dist/<Product>/ (onedir); macOS also dist/<Product>.app

Bundles: branding + icons + version, the operator's targets, the EQ app
catalog, the grey-noise asset and the licence. Never bundles EQ Helper's UI,
the admin tools or the measurement data (nova_w1.npz) — see
tests/rf/test_import_boundaries.py. Then run packaging/package_release.py.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
branding = json.loads((ROOT / "resonancefit" / "assets" / "branding.json").read_text(encoding="utf-8"))
NAME = branding.get("product_name") or "ResonanceFit"
VERSION_FILE = ROOT / "resonancefit" / "assets" / "version.txt"
VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "0.0.0"
BUNDLE_ID = "io.github.mohamedkhalil8801." + "".join(c for c in NAME.lower() if c.isalnum())
UTI = BUNDLE_ID + ".calibration"

datas = [
    (str(ROOT / "resonancefit" / "assets"), "resonancefit/assets"),
    # bundled targets, minus diagnostic ones that aren't coupler targets (see eq_helper/admin/generate.py)
    *[(str(t), "assets/targets") for t in sorted((ROOT / "assets" / "targets").glob("*.txt"))
      if t.name not in ("Flat Line.txt",)],
    (str(ROOT / "assets" / "grey_noise.wav"), "assets"),
    (str(ROOT / "eq_helper" / "exports" / "catalog.json"), "eq_helper/exports"),
    (str(ROOT / "LICENSE"), "."),
]

excludes = [
    # EQ Helper / operator-only code never ships to customers
    "eq_helper.ui", "eq_helper.admin", "eq_helper.dsp.eq_optimize",
    # not used by the app
    "tkinter", "matplotlib", "IPython", "pytest", "pytestqt", "PyQt5", "PyQt6", "PySide2",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtWebSockets", "PySide6.QtHttpServer",
]

binaries = []
if sys.platform.startswith("linux"):
    # sounddevice needs PortAudio; most Linux desktops don't ship it, so bundle it
    # (packaging/pyi_rth_portaudio.py points sounddevice at the bundled copy).
    import glob
    found = sorted(glob.glob("/usr/lib/*/libportaudio.so.2") + glob.glob("/usr/lib/libportaudio.so.2")
                   + glob.glob("/usr/lib64/libportaudio.so.2"))
    if found:
        binaries.append((os.path.realpath(found[0]), "."))
    elif os.environ.get("RF_REQUIRE_AUDIO"):
        raise SystemExit("libportaudio.so.2 not found: install libportaudio2 before building")
    # Qt's X11 plugin needs these; PyInstaller leaves them to the system, where minimal installs
    # (WSL, some containers) lack them and the window can't open. Bundle them like the rest of xcb.
    for lib in ("libxcb-icccm.so.4", "libxcb-keysyms.so.1", "libxcb-shape.so.0"):
        found = sorted(glob.glob(f"/usr/lib/*/{lib}") + glob.glob(f"/usr/lib64/{lib}"))
        if found:
            binaries.append((os.path.realpath(found[0]), "."))

icon = {"win32": "icon.ico", "darwin": "icon.icns"}.get(sys.platform)
icon = str(ROOT / "resonancefit" / "assets" / icon) if icon else None

a = Analysis(
    [str(ROOT / "resonancefit" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["eq_helper.exports.formats.graphic", "eq_helper.exports.formats.convolution",
                   "eq_helper.exports.formats.parametric", "PySide6.QtNetwork"],
    runtime_hooks=[str(ROOT / "pyi_rth_winquirks.py"), str(ROOT / "packaging" / "pyi_rth_portaudio.py")],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    icon=icon,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": f"{NAME} calibration",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": [UTI],
            }],
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": UTI,
                "UTTypeDescription": f"{NAME} calibration",
                "UTTypeConformsTo": ["public.data"],
                "UTTypeTagSpecification": {"public.filename-extension": ["rescal"]},
            }],
        },
    )
