"""Package a customer's download for each OS from a prebuilt app (ADR 0001).

* Windows: ``.zip`` — one folder with the app (exe + its files),
  ``Calibrations/<customer – IEM>.rescal``, ``Targets/``, ``First launch.txt``
  and the calibration sheet PDF.
* Linux: ``.tar.gz`` — the same folder with the AppImage (executable).
* macOS: ``.zip`` — the DMG built on CI plus the ``.rescal``, sheet and
  ``First launch.txt``; the app imports the calibration on first open, so no
  Mac is needed here.
* "Calibration file only" (recalibration): just the ``.rescal`` and sheet.

The app looks for ``Calibrations/`` and ``Targets/`` next to itself, so the
app sits at the top of the package folder. Names come from Branding.
"""

from __future__ import annotations

import io
import re
import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from eq_helper.calibration.rescal import RESCAL_SUFFIX

from .calibration_sheet import sheet_file_name

SYSTEM_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
FIRST_LAUNCH = "First launch.txt"


def _safe(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", text).strip(" .") or "untitled"


def rescal_name(manifest) -> str:
    return _safe(f"{manifest.customer.name} – {manifest.iem.display_name}") + RESCAL_SUFFIX


def folder_name(manifest, branding) -> str:
    return _safe(f"{branding.product_name} – {manifest.customer.name}")


def package_name(system: str, manifest, branding) -> str:
    ext = ".tar.gz" if system == "linux" else ".zip"
    return _safe(f"{branding.product_name} – {manifest.customer.name} – {manifest.iem.display_name} "
                 f"({SYSTEM_NAMES[system]})") + ext


def first_launch_text(system: str, branding) -> str:
    app = branding.product_name
    if system == "windows":
        steps = [
            f"1. Extract the whole folder first (right-click the zip → Extract All). Don't run {app} from "
            "inside the zip.",
            f"2. Open the extracted folder and double-click {branding.file_stem}.exe.",
            "3. Windows may say “Windows protected your PC” because the app isn't signed yet. "
            "Click “More info”, then “Run anyway”. You only need to do this once.",
            "4. Keep the Calibrations and Targets folders next to the app; your calibration loads by itself.",
        ]
    elif system == "macos":
        steps = [
            f"1. Open the .dmg and drag {app} into Applications.",
            f"2. The first time, right-click (or Control-click) {app} in Applications and choose Open, "
            "then Open again. The app isn't signed yet, so macOS asks once.",
            "   If macOS says it can't be opened, go to System Settings → Privacy & Security and click "
            "“Open Anyway”.",
            f"3. Double-click your .rescal calibration file (or drag it onto the {app} window) once. "
            "It's remembered from then on.",
        ]
    else:
        steps = [
            "1. Extract the archive and keep everything in one folder.",
            "2. If the AppImage doesn't start, make it executable: right-click → Properties → "
            "“Allow executing as program”, or run chmod +x on it in a terminal.",
            "3. Double-click the AppImage. Keep the Calibrations and Targets folders next to it; "
            "your calibration loads by itself.",
        ]
    return "\n".join([f"{branding.title}", "", "First launch", ""] + steps + [
        "", "Start with the volume low. Press Space at any time to stop all sound.", ""])


def _extras(rescal: Path, sheet: Path | None, targets, manifest, branding, system: str):
    """(archive path inside the package folder, source path or bytes) pairs."""
    items = [(f"Calibrations/{rescal_name(manifest)}" if system != "macos" else rescal_name(manifest), rescal)]
    if system != "macos":
        items += [(f"Targets/{Path(t).name}", Path(t)) for t in targets]
    if sheet is not None:
        items.append((sheet_file_name(manifest, branding), Path(sheet)))
    items.append((FIRST_LAUNCH, first_launch_text(system, branding).encode("utf-8")))
    return items


def _app_members(build_zip: Path):
    """Members of a prebuilt app zip, with a single top folder stripped; validated."""
    with zipfile.ZipFile(build_zip) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        for info in infos:
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or ":" in info.filename.split("/")[0]:
                raise ValueError(f"The prebuilt app contains an unsafe path: {info.filename}")
        tops = {PurePosixPath(i.filename).parts[0] for i in infos}
        strip = len(tops) == 1 and all(len(PurePosixPath(i.filename).parts) > 1 for i in infos)
        for info in infos:
            parts = PurePosixPath(info.filename).parts
            yield "/".join(parts[1:] if strip else parts), z.read(info), info.external_attr


def package(system: str, build: Path, rescal: Path, sheet: Path | None, targets, out_dir: Path,
            branding, manifest) -> Path:
    """Write the customer's download for ``system`` into ``out_dir``; returns the file."""
    if system not in SYSTEM_NAMES:
        raise ValueError(f"Unknown system {system!r}.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = folder_name(manifest, branding)
    target = out_dir / package_name(system, manifest, branding)
    partial = target.with_name(target.name + ".part")
    extras = _extras(Path(rescal), sheet, targets, manifest, branding, system)
    try:
        if system == "linux":
            with tarfile.open(partial, "w:gz") as tar:
                image = Path(build)
                info = tar.gettarinfo(str(image), f"{root}/{image.name}")
                info.mode = 0o755
                with image.open("rb") as stream:
                    tar.addfile(info, stream)
                for name, source in extras:
                    data = source if isinstance(source, bytes) else Path(source).read_bytes()
                    info = tarfile.TarInfo(f"{root}/{name}")
                    info.size, info.mode = len(data), 0o644
                    tar.addfile(info, io.BytesIO(data))
        else:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
                if system == "windows":
                    for name, data, attr in _app_members(Path(build)):
                        info = zipfile.ZipInfo(f"{root}/{name}")
                        info.external_attr, info.compress_type = attr, zipfile.ZIP_DEFLATED
                        z.writestr(info, data)
                else:
                    z.write(build, f"{root}/{Path(build).name}", compress_type=zipfile.ZIP_STORED)
                for name, source in extras:
                    if isinstance(source, bytes):
                        z.writestr(f"{root}/{name}", source)
                    else:
                        z.write(source, f"{root}/{name}")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


def calibration_only(rescal: Path, sheet: Path | None, out_dir: Path, branding, manifest) -> list[Path]:
    """Recalibration: just the ``.rescal`` (and sheet), named for the customer."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / rescal_name(manifest)]
    shutil.copyfile(rescal, written[0])
    if sheet is not None:
        written.append(out_dir / sheet_file_name(manifest, branding))
        shutil.copyfile(sheet, written[-1])
    return written
