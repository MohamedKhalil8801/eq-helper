"""``--self-test``: prove a build works without a person or an audio device.

Used by CI on every built app: makes a synthetic calibration in a temporary
folder laid out like a customer download, starts the real main window
(offscreen unless a display is wanted), waits for the calibration to load
with the default target, exports for a few catalogued apps and checks the
files. Prints a short report (also written to ``$RF_SELFTEST_REPORT`` when set,
since a Windows GUI build has no console); exit code 0 = pass.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np

REQUIRED_APPS = ("equalizer-apo", "easyeffects", "camilladsp", "eqmac")


def synthetic_calibration():
    from eq_helper.calibration.rescal import Calibration, Customer, Iem, Manifest
    from eq_helper.dsp.resonance import Fit

    f = np.unique(np.r_[np.geomspace(20, 20000, 400), 1000])
    fits = [Fit(side, f"{side}{i}", f, 75 + offset + 9 * np.exp(-0.5 * (np.log(f / peak) / .075) ** 2))
            for side, offset in (("L", 0.0), ("R", 1.0)) for i, peak in enumerate((6500, 7300, 8100, 8900))]
    return Calibration(Manifest(customer=Customer("Self test"), iem=Iem("Test", "IEM")), fits)


def check_audio_library(report) -> bool:
    """sounddevice must import (PortAudio bundled) — devices aren't needed."""
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        report(f"audio library: NOT available ({exc})")
        # Release builds must bundle PortAudio (CI sets RF_REQUIRE_AUDIO); a source run on a
        # machine without it still passes so developers can self-test anywhere.
        return not (os.environ.get("RF_REQUIRE_AUDIO") or getattr(sys, "frozen", False))
    report("audio library: ok")
    return True


def run_self_test(argv=None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: list[str] = []
    code = _run(lines, argv)
    report_path = os.environ.get("RF_SELFTEST_REPORT")
    if report_path:
        Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def _run(lines: list[str], argv) -> int:
    argv = list(argv or sys.argv)
    # --use-download: check a real customer download instead of a synthetic one — the
    # calibration(s) and Targets/ shipped next to this app must be found by themselves.
    use_download = "--use-download" in argv

    def report(text: str) -> None:
        lines.append(text)
        print(text, flush=True)

    try:
        from PySide6.QtWidgets import QApplication

        from eq_helper.calibration.rescal import write_rescal
        from eq_helper.exports.catalog import export_for_app
        from eq_helper.exports.formats.base import ExportMeta

        from .apo_apply import ApoApplier
        from .context import AppContext
        from .os_integration import FileAssociation
        from .paths import AppPaths, current_platform
        from .targets import DEFAULT_TARGET
        from .ui.common import MainThreadGC
        from .ui.main_window import MainWindow
        from .version import app_version

        report(f"version: {app_version()}")
        ok = check_audio_library(report)
        app = QApplication.instance()
        if app is None:
            app = QApplication(argv[:1])
            MainThreadGC(parent=app)
        with tempfile.TemporaryDirectory(prefix="selftest-") as tmp:
            tmp = Path(tmp)
            platform = current_platform()
            if use_download:
                download = os.environ.get("RF_DOWNLOAD_DIR")
                app_dir = Path(download) if download else AppPaths.detect().app_dir
                report(f"download folder: {app_dir}")
                paths = AppPaths(platform, tmp / "data", app_dir)
            else:
                paths = AppPaths(platform, tmp / "data", tmp / "download")
                # where this OS's app looks (next to the app; the data folder on macOS)
                paths.calibrations_folder.mkdir(parents=True)
                write_rescal(paths.calibrations_folder / "Self test.rescal", synthetic_calibration())
            ctx = AppContext.create(paths)
            # no first-launch prompts: nobody is there to answer them
            ctx.settings.update(walkthrough_seen=True, quiet_warning_seen=True, file_association_offered=True)
            report(f"calibrations found: {len(ctx.library.list())}")
            report(f"targets: {len(ctx.targets.targets)} (default {DEFAULT_TARGET} "
                   f"{'present' if ctx.targets.find(DEFAULT_TARGET) else 'MISSING'})")
            ok &= len(ctx.library.list()) >= 1 and ctx.targets.find(DEFAULT_TARGET) is not None
            if use_download:
                shipped = [t for t in ctx.targets.targets if t.source == "shipped"]
                report(f"targets shipped in the download: {len(shipped)}")
                ok &= bool(shipped) or platform == "macos"
                selected = ctx.library.selected
                if selected is not None:
                    m = selected.manifest
                    report(f"selected: {m.customer.name} – {m.iem.display_name} ({m.created})")
            window = MainWindow(ctx, start_audio=False, apo=ApoApplier(platform, ctx.settings, tmp / "no-apo"),
                                association=FileAssociation(platform, ctx.branding, None))
            window.show()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and window.tuning.channel_eq is None:
                app.processEvents()
                time.sleep(0.02)
            eq = window.tuning.channel_eq
            ok &= eq is not None
            report(f"calibration loaded: {'yes' if eq is not None else 'NO'}; target "
                   f"{window.tuning.session.target_name if window.tuning.session else '-'}")
            if eq is not None:
                job = window.export_job()
                for app_id in REQUIRED_APPS:
                    entry = window.catalog.get(app_id)
                    result = export_for_app(entry, eq, tmp / "exports" / app_id, job.base_name,
                                            ExportMeta("Self test", "Test IEM", "2026-01-01"), device_rate=48000)
                    good = bool(result.files) and all(p.stat().st_size > 0 for p in result.files)
                    ok &= good
                    report(f"export {app_id}: {len(result.files)} file(s) {'ok' if good else 'FAILED'}")
            window.close()
            app.processEvents()
    except Exception:  # noqa: BLE001
        report("SELF-TEST CRASHED:\n" + traceback.format_exc())
        return 1
    report("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1
