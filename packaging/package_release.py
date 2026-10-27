"""Turn a PyInstaller build into the release files the admin app downloads.

    python packaging/package_release.py version 1.2.0          # before pyinstaller: stamp the version
    python packaging/package_release.py package --system linux --version 1.2.0 [--appimagetool PATH]
    python packaging/package_release.py checksums release/      # SHA256SUMS for every file

Release names (eq_helper/admin/build_cache.py relies on them):
``<Product>-<version>-windows.zip`` (one top folder holding the app),
``<Product>-<version>-macos.dmg`` (the .app plus an Applications link) and
``<Product>-<version>-linux.AppImage`` (with .desktop entry and .rescal MIME type).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "resonancefit" / "assets"


def product() -> tuple[str, str]:
    data = json.loads((ASSETS / "branding.json").read_text(encoding="utf-8"))
    name = data.get("product_name") or "ResonanceFit"
    return name, data.get("byline") or ""


def release_name(system: str, version: str) -> str:
    name, _ = product()
    ext = {"windows": "zip", "macos": "dmg", "linux": "AppImage"}[system]
    return f"{name}-{version}-{system}.{ext}"


def write_version(version: str) -> Path:
    path = ASSETS / "version.txt"
    path.write_text(version.strip() + "\n", encoding="utf-8")
    return path


def package_windows(dist: Path, out: Path, version: str) -> Path:
    name, _ = product()
    app = dist / name
    if not (app / f"{name}.exe").exists():
        raise SystemExit(f"No Windows build at {app}")
    target = out / release_name("windows", version)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                z.write(path, f"{name}/{path.relative_to(app).as_posix()}")
    return target


def package_macos(dist: Path, out: Path, version: str) -> Path:
    name, _ = product()
    app = dist / f"{name}.app"
    if not app.is_dir():
        raise SystemExit(f"No macOS app bundle at {app}")
    staging = out / "dmg-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    subprocess.run(["ditto", str(app), str(staging / app.name)], check=True)
    (staging / "Applications").symlink_to("/Applications")
    target = out / release_name("macos", version)
    target.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", name, "-srcfolder", str(staging), "-ov",
                    "-format", "UDZO", str(target)], check=True)
    shutil.rmtree(staging, ignore_errors=True)
    return target


def package_linux(dist: Path, out: Path, version: str, appimagetool: str) -> Path:
    sys.path.insert(0, str(ROOT))
    from eq_helper.calibration.branding import load_branding
    from resonancefit.os_integration import desktop_entry, desktop_id, mime_xml

    branding = load_branding(ASSETS / "branding.json")
    name = branding.product_name
    app = dist / name
    if not (app / name).exists():
        raise SystemExit(f"No Linux build at {app}")
    appdir = out / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    lib = appdir / "usr" / "lib" / desktop_id(branding)
    shutil.copytree(app, lib, symlinks=True)
    ident = desktop_id(branding)
    apprun = appdir / "AppRun"
    apprun.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                      f'exec "$HERE/usr/lib/{ident}/{name}" "$@"\n', encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (appdir / f"{ident}.desktop").write_text(desktop_entry(branding, "AppRun", ident)
                                             .replace('Exec="AppRun" %f', "Exec=AppRun %f"), encoding="utf-8")
    shutil.copyfile(ASSETS / "icon.png", appdir / f"{ident}.png")
    shutil.copyfile(ASSETS / "icon.png", appdir / ".DirIcon")
    mime = appdir / "usr" / "share" / "mime" / "packages"
    mime.mkdir(parents=True)
    (mime / f"{ident}-rescal.xml").write_text(mime_xml(branding), encoding="utf-8")
    target = out / release_name("linux", version)
    target.unlink(missing_ok=True)
    env = {**os.environ, "ARCH": os.environ.get("ARCH", "x86_64")}
    subprocess.run([appimagetool, "--appimage-extract-and-run", "--no-appstream", str(appdir), str(target)],
                   check=True, env=env)
    shutil.rmtree(appdir, ignore_errors=True)
    return target


def write_checksums(folder: Path) -> Path:
    lines = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {path.name}")
    target = folder / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("version")
    v.add_argument("version")
    p = sub.add_parser("package")
    p.add_argument("--system", required=True, choices=("windows", "macos", "linux"))
    p.add_argument("--version", required=True)
    p.add_argument("--dist", default=str(ROOT / "dist"))
    p.add_argument("--out", default=str(ROOT / "release"))
    p.add_argument("--appimagetool", default="appimagetool")
    c = sub.add_parser("checksums")
    c.add_argument("folder")
    args = parser.parse_args(argv)
    if args.command == "version":
        print(write_version(args.version))
    elif args.command == "checksums":
        print(write_checksums(Path(args.folder)).read_text(encoding="utf-8"), end="")
    else:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        dist = Path(args.dist)
        if args.system == "windows":
            target = package_windows(dist, out, args.version)
        elif args.system == "macos":
            target = package_macos(dist, out, args.version)
        else:
            target = package_linux(dist, out, args.version, args.appimagetool)
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
