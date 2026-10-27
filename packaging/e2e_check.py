"""End-to-end check of the customer pipeline on this computer (plan Task 31).

    python packaging/e2e_check.py [--measurements FOLDER] [--work DIR] [--keep]

1. Downloads the latest prebuilt app for this OS from the public releases repo
   through the admin build cache (SHA-256 verified).
2. Generates a customer with EQ Helper's pipeline: .rescal, calibration sheet
   and this OS's package (real measurements if --measurements is given,
   otherwise synthetic fits).
3. Extracts the package the way a customer would.
4. Launches the packaged app with ``--self-test --use-download``: the
   calibration and Targets/ shipped next to it must load by themselves, with
   PEQdB Diamond as the default target, and exports must work.

On macOS step 4 runs the app from the DMG after importing the calibration the
way a customer does (double-click), since macOS reads calibrations from the
app's data folder. Exit code 0 = everything passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def current_system() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def synthetic_measurements(folder: Path) -> Path:
    import numpy as np
    f = np.unique(np.r_[np.geomspace(20, 20000, 400), 1000])
    for side, offset in (("L", 0.0), ("R", 1.5)):
        for i, peak in enumerate((6400, 7100, 7800, 8500, 9000)):
            db = 75 + offset + 9 * np.exp(-0.5 * (np.log(f / peak) / .075) ** 2)
            (folder / f"{side} fit{i}.txt").write_text("\n".join(f"{x:.4f} {y:.4f}" for x, y in zip(f, db)))
    return folder


def run_packaged(system: str, package: Path, work: Path) -> tuple[int, str]:
    report = work / "selftest-report.txt"
    env = {**os.environ, "RF_SELFTEST_REPORT": str(report), "QT_QPA_PLATFORM": "offscreen"}
    env.pop("RF_DOWNLOAD_DIR", None)
    extract = work / "customer"
    shutil.rmtree(extract, ignore_errors=True)
    if system == "linux":
        with tarfile.open(package) as tar:
            tar.extractall(extract, filter="data")
        top = next(extract.iterdir())
        image = next(top.glob("*.AppImage"))
        image.chmod(0o755)
        cmd = [str(image), "--appimage-extract-and-run", "--self-test", "--use-download"]
        env["APPIMAGE"] = str(image)  # what the AppImage runtime sets; extract-and-run keeps it
    elif system == "windows":
        with zipfile.ZipFile(package) as z:
            z.extractall(extract)
        top = next(extract.iterdir())
        cmd = [str(next(top.glob("*.exe"))), "--self-test", "--use-download"]
    else:
        with zipfile.ZipFile(package) as z:
            z.extractall(extract)
        top = next(extract.iterdir())
        dmg = next(top.glob("*.dmg"))
        rescal = next(top.glob("*.rescal"))
        mount = Path(tempfile.mkdtemp())
        for attempt in range(4):  # a freshly written DMG can be briefly busy
            if subprocess.run(["hdiutil", "attach", str(dmg), "-mountpoint", str(mount), "-nobrowse",
                               "-readonly"]).returncode == 0:
                break
            time.sleep(5)
        else:
            raise RuntimeError(f"Couldn't mount {dmg.name}")
        try:
            macos = next(mount.glob("*.app")) / "Contents" / "MacOS"
            binary = next(p for p in macos.iterdir() if p.is_file())
            # macOS keeps calibrations in the app's data folder: the customer opens the .rescal once
            proc = subprocess.run([str(binary), "--self-test", "--use-download", str(rescal)], env=env,
                                  cwd=top, timeout=600)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=False)
        return proc.returncode, report.read_text(encoding="utf-8") if report.exists() else ""
    proc = subprocess.run(cmd, env=env, cwd=top, timeout=600)
    return proc.returncode, report.read_text(encoding="utf-8") if report.exists() else ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--measurements", help="a measurement folder (L/R detected); default: synthetic fits")
    parser.add_argument("--work", help="working folder (default: a temporary one)")
    parser.add_argument("--keep", action="store_true", help="keep the working folder")
    parser.add_argument("--repo", default=None, help="releases repo (default: the admin app's)")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from eq_helper.admin.build_cache import DEFAULT_RELEASES_REPO, BuildCache
    from eq_helper.admin.customer_library import CustomerLibrary, MeasurementSource
    from eq_helper.admin.generate import GenerateRequest, generate
    from eq_helper.calibration.branding import load_branding
    from eq_helper.calibration.rescal import Accessory, Customer, Iem, Manifest, Operator

    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 - the sheet needs fonts
    system = current_system()
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="rf-e2e-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"working in {work}")
    ok = True
    try:
        cache = BuildCache(work / "build_cache", args.repo or DEFAULT_RELEASES_REPO)
        release = cache.check_latest()
        build = cache.fetch(system, release)
        print(f"1. downloaded {build.name} from {release.version} (checksum verified)")

        if args.measurements:
            measurements = Path(args.measurements)
        else:
            measurements = work / "measurements"
            measurements.mkdir(exist_ok=True)
            synthetic_measurements(measurements)
        manifest = Manifest(customer=Customer("E2E Customer"), iem=Iem("Truthear", "Nova", "E2E-1"),
                            accessories=[Accessory("tips", "Spinfit W1 M", "customer"),
                                         Accessory("dac", "Apple USB-C dongle", "operator")],
                            coupler="IEC-711 clone", operator=Operator("Mohamed Khalil"))
        from eq_helper.admin.generate import DIAGNOSTIC_TARGETS
        targets = [t for t in sorted((ROOT / "assets" / "targets").glob("*.txt")) if t.name not in DIAGNOSTIC_TARGETS]
        result = generate(GenerateRequest(manifest, MeasurementSource.from_folder(measurements), targets, [system],
                                          work / "out"),
                          CustomerLibrary(work / "library"), cache, load_branding(), progress=lambda m: None)
        package = next(p for p in result.outputs if p.suffix in (".zip", ".gz") and "(" in p.name)
        print(f"2. generated {package.name} (+ {', '.join(p.name for p in result.outputs if p != package)})")
        for warning in result.warnings:
            print(f"   note: {warning}")

        code, report = run_packaged(system, package, work)
        print("3-4. extracted and launched the packaged app:")
        print("   " + "\n   ".join(report.strip().splitlines()))
        ok = code == 0 and "selected: E2E Customer" in report and "target PEQdB Diamond" in report
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        ok = False
    finally:
        if not args.keep and not args.work:
            shutil.rmtree(work, ignore_errors=True)
    print("END-TO-END CHECK " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
