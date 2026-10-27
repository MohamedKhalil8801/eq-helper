# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for EQ Helper (the operator app), Windows onedir build.

    pyinstaller packaging/eq_helper.spec      # → dist/EQ Helper/EQ Helper.exe

Bundles everything EQ Helper reads at run time, including what "Generate
customer app…" needs: the measurement data, all targets (Flat Line included,
it's offered unchecked), the EQ app catalog, branding/icons and the test-noise
asset. Built by .github/workflows/eq-helper-build.yml.
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "eq_helper" / "dsp" / "nova_w1.npz"), "eq_helper/dsp"),
    (str(ROOT / "eq_helper" / "exports" / "catalog.json"), "eq_helper/exports"),
    (str(ROOT / "assets" / "targets"), "assets/targets"),
    (str(ROOT / "assets" / "grey_noise.wav"), "assets"),
    (str(ROOT / "resonancefit" / "assets"), "resonancefit/assets"),
    (str(ROOT / "LICENSE"), "."),
]

a = Analysis(
    [str(ROOT / "eq_helper" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["eq_helper.exports.formats.graphic", "eq_helper.exports.formats.convolution",
                   "eq_helper.exports.formats.parametric", "PySide6.QtNetwork"],
    runtime_hooks=[str(ROOT / "pyi_rth_winquirks.py")],
    excludes=["tkinter", "IPython", "pytest", "pytestqt", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EQ Helper",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="EQ Helper")
