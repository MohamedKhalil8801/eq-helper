"""Download and install a new version of the app itself, on every OS.

The release for this OS is downloaded in the background into the data folder
(``updates/<version>/``), checked against the release's ``SHA256SUMS`` and
unpacked there. It's installed when the app closes, or at once with "Install
and restart":

* Windows: the new build, started from where it was unpacked with
  ``--rf-install-update``, waits for this app to exit, then replaces the app's
  own files in its folder (the exe and ``_internal``; the Calibrations,
  Targets and PDFs next to them are left alone) and, if asked, starts it again.
* macOS: the same, replacing the whole ``.app`` bundle.
* Linux: the AppImage file (``$APPIMAGE``) is swapped for the new one (a
  running AppImage can be replaced), then started again if asked.

If replacing fails the old files are put back (and the error is shown next
time). Where the app can't replace itself (a read-only folder, macOS App
Translocation, a Linux build not run as an AppImage) it falls back to the
download link.

Development builds never update: a source run, or any version other than a
plain ``X.Y.Z`` (local builds are stamped like ``0.1.3-dev``). They don't even
check, so a release can never replace the build being worked on.

Keep ``--rf-install-update <source> <target> <pid> [--relaunch]`` working in
every version: the *old* app starts the *new* build with it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import is_translocated
from .updates import TIMEOUT_S, newer

INSTALL_FLAG = "--rf-install-update"
RELAUNCH_FLAG = "--relaunch"
RELEASE_VERSION = re.compile(r"^\d+(\.\d+)+$")
CHUNK = 1 << 16
WAIT_FOR_EXIT_S = 120   # the old app has this long to close before the install gives up
RETRY_S = 15            # files still locked a moment after the app exits (virus scanners…)
OLD_SUFFIX = ".old-update"
READY_FILE = "ready.json"
FAILED_FILE = "failed.txt"
INSTALLING_FILE = "installing"


class UpdateError(Exception):
    """Plain-language reason a download or install didn't work."""


class UpdateCancelled(UpdateError):
    pass


def is_release_build(version: str, frozen: bool | None = None) -> bool:
    """Only a packaged build with a plain release version may check for and install updates."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return frozen and bool(RELEASE_VERSION.match(str(version).strip()))


# ── where the running app lives ──────────────────────────────────────────────

@dataclass(frozen=True)
class AppLocation:
    platform: str
    target: Path      # what's replaced: the app's folder (Windows), the .app (macOS), the AppImage (Linux)
    executable: Path  # the app's own executable (its name is the same in the new build)

    @classmethod
    def detect(cls, platform: str, frozen: bool, executable: str, env) -> "AppLocation | None":
        if not frozen:
            return None
        exe = Path(executable)
        if platform == "windows":
            return cls(platform, exe.parent, exe)
        if platform == "macos":
            bundle = exe.parents[2] if len(exe.parents) > 2 else exe
            return cls(platform, bundle, exe) if bundle.suffix == ".app" else None
        if env.get("APPIMAGE"):
            image = Path(env["APPIMAGE"])
            return cls(platform, image, image)
        return None

    def problem(self) -> str:
        """Why the app can't replace itself here ("" when it can)."""
        if self.platform == "macos" and is_translocated(self.target):
            return "macOS is running the app from a temporary copy (move it to Applications first)"
        folder = self.target if self.platform == "windows" else self.target.parent
        if not _writable(folder) or (self.platform == "macos" and not os.access(self.target, os.W_OK)):
            return f"the app's folder ({folder}) can't be written to"
        return ""


def _writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


# ── downloading ──────────────────────────────────────────────────────────────

class UrllibDownload:
    def open(self, url: str):
        request = urllib.request.Request(url, headers={"User-Agent": "update-download"})
        return urllib.request.urlopen(request, timeout=TIMEOUT_S * 3)


@dataclass(frozen=True)
class Staged:
    version: str
    path: Path  # the unpacked new app: a folder (Windows), a .app (macOS), an AppImage (Linux)


def _expected_hash(sums: str, name: str) -> str:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    raise UpdateError(f"the release has no checksum for {name}")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _retry(action: Callable[[], None], seconds: float = RETRY_S) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            return action()
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.25)


class Updater:
    """Downloads, unpacks and installs the release found by the update check."""

    def __init__(self, location: AppLocation | None, root: Path, current_version: str, http=None,
                 spawn: Callable | None = None, platform_tools=None):
        self.location = location
        self.root = Path(root)
        self.current_version = current_version
        self.http = http or UrllibDownload()
        self.spawn = spawn or spawn_detached
        self.tools = platform_tools or MacTools()

    # ── state ──────────────────────────────────────────────────────────────
    def problem(self) -> str:
        if self.location is None:
            return "this copy of the app can't update itself"
        return self.location.problem()

    @property
    def can_install(self) -> bool:
        return not self.problem()

    def _read_ready(self) -> dict | None:
        try:
            data = json.loads((self.root / READY_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def ready(self) -> Staged | None:
        """The downloaded update waiting to be installed, if it's newer than this app."""
        data = self._read_ready()
        if not data or not newer(str(data.get("version", "")), self.current_version):
            return None
        path = Path(str(data.get("path", "")))
        return Staged(str(data["version"]), path) if path.exists() else None

    def last_failure(self) -> str:
        """Why the last install didn't work ("" when it did, or none was tried)."""
        try:
            return (self.root / FAILED_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def tidy(self) -> None:
        """At launch: forget an update that's now installed, and anything an install left behind."""
        ready = self.ready()
        if ready is None:
            shutil.rmtree(self.root, ignore_errors=True)
        elif self.root.is_dir():
            for entry in self.root.iterdir():
                if entry.is_dir() and entry.name != ready.version:
                    shutil.rmtree(entry, ignore_errors=True)
        if self.location is None:
            return
        target = self.location.target
        try:
            leftovers = ([e for e in target.iterdir() if e.name.endswith(OLD_SUFFIX)]
                         if self.location.platform == "windows" else
                         [target.with_name(target.name + OLD_SUFFIX), target.with_name(f".{target.name}.new-update")])
        except OSError:
            return
        for entry in leftovers:
            try:
                if entry.exists() or entry.is_symlink():
                    _remove(entry)
            except OSError:
                pass

    # ── downloading ────────────────────────────────────────────────────────
    def download(self, result, progress: Callable[[int, int], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> Staged:
        """Download ``result`` (an UpdateResult), check it and unpack it. Safe on a worker thread."""
        ready = self.ready()
        if ready is not None and ready.version == result.latest:
            return ready
        if not (result.download_url and result.asset_name and result.checksums_url):
            raise UpdateError("the release has no download for this system")
        folder = self.root / result.latest
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        with self.http.open(result.checksums_url) as response:
            expected = _expected_hash(response.read().decode("utf-8", "replace"), result.asset_name)
        archive = folder / result.asset_name
        part = archive.with_name(archive.name + ".part")
        digest = hashlib.sha256()
        with self.http.open(result.download_url) as response, part.open("wb") as out:
            total = int(getattr(response, "length", None) or result.size or 0)
            done = 0
            while chunk := response.read(CHUNK):
                if cancelled is not None and cancelled():
                    raise UpdateCancelled("cancelled")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, max(total, done))
        if digest.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise UpdateError("the download was damaged (its checksum doesn't match); it will try again")
        part.replace(archive)
        staged = Staged(result.latest, self._unpack(archive, folder))
        (self.root / READY_FILE).write_text(json.dumps({"version": staged.version, "path": str(staged.path)}),
                                            encoding="utf-8")
        (self.root / FAILED_FILE).unlink(missing_ok=True)
        return staged

    def _unpack(self, archive: Path, folder: Path) -> Path:
        name = self.location.executable.name if self.location else ""
        platform = self.location.platform if self.location else ""
        if platform == "windows":
            app = folder / "app"
            with zipfile.ZipFile(archive) as z:
                members = [m for m in z.infolist() if not m.is_dir()]
                tops = {Path(m.filename).parts[0] for m in members}
                if len(tops) != 1:
                    raise UpdateError("the download isn't laid out as expected")
                top = tops.pop()
                for member in members:
                    relative = Path(*Path(member.filename).parts[1:])
                    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                        raise UpdateError("the download isn't laid out as expected")
                    dest = app / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
            archive.unlink()
            if not (app / name).is_file():
                raise UpdateError(f"the download has no {name} (found {top})")
            return app
        if platform == "macos":
            bundle = self.tools.copy_app_from_dmg(archive, folder)
            archive.unlink()
            if not (bundle / "Contents" / "MacOS" / name).is_file():
                raise UpdateError(f"the download has no {name} app")
            return bundle
        archive.chmod(archive.stat().st_mode | 0o755)
        return archive

    # ── installing ─────────────────────────────────────────────────────────
    def install(self, relaunch: bool) -> bool:
        """Install the downloaded update as the app closes; ``relaunch`` starts it again afterwards.
        False when there's nothing to install. Raises UpdateError if it can't even start."""
        staged = self.ready()
        if staged is None or self.location is None:
            return False
        location = self.location
        try:
            if location.platform == "linux":
                # A running AppImage can be replaced: copy next to it, then swap in one step.
                fresh = location.target.with_name(f".{location.target.name}.new-update")
                shutil.copy2(staged.path, fresh)
                fresh.chmod(fresh.stat().st_mode | 0o755)
                os.replace(fresh, location.target)
                if relaunch:
                    self.spawn(["/bin/sh", "-c", 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec "$2"',
                                "sh", str(os.getpid()), str(location.target)])
                return True
            exe = (staged.path / location.executable.name if location.platform == "windows"
                   else staged.path / "Contents" / "MacOS" / location.executable.name)
            args = [str(exe), INSTALL_FLAG, str(staged.path), str(location.target), str(os.getpid())]
            self.spawn(args + ([RELAUNCH_FLAG] if relaunch else []), cwd=str(self.root))
            return True
        except OSError as exc:
            raise UpdateError(f"couldn't start installing ({exc})") from exc


class MacTools:
    """macOS disk-image handling (kept apart so tests on other systems can stand in for it)."""

    def copy_app_from_dmg(self, dmg: Path, folder: Path) -> Path:
        mount = folder / "mnt"
        mount.mkdir(exist_ok=True)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount),
                        str(dmg)], check=True, capture_output=True)
        try:
            apps = sorted(mount.glob("*.app"))
            if not apps:
                raise UpdateError("the disk image has no app in it")
            bundle = folder / apps[0].name
            subprocess.run(["ditto", str(apps[0]), str(bundle)], check=True, capture_output=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        return bundle


# ── starting other processes ─────────────────────────────────────────────────

def clean_env(env=None, bundle: str | None = None) -> dict:
    """This process's environment minus what PyInstaller (and an AppImage) added to it, so the
    process started next loads its own libraries, not this build's."""
    env = dict(os.environ if env is None else env)
    bundle = bundle if bundle is not None else getattr(sys, "_MEIPASS", None)
    for key in list(env):
        if key.endswith("_ORIG") and key[:-5] in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            env[key[:-5]] = env.pop(key)
        elif key.startswith(("_PYI_", "_MEIPASS")) or key in ("APPIMAGE", "APPDIR", "ARGV0", "OWD"):
            del env[key]
    if bundle:
        for key in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            if key in env:
                kept = [p for p in env[key].split(os.pathsep) if p and not p.startswith(bundle)]
                if kept:
                    env[key] = os.pathsep.join(kept)
                else:
                    del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def spawn_detached(args: list[str], cwd: str | None = None) -> None:
    """Start ``args`` on its own: it keeps running after this app exits."""
    kwargs = dict(env=clean_env(), cwd=cwd, close_fds=True, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:  # a job that doesn't allow breaking away
            subprocess.Popen(args, creationflags=flags, **kwargs)
    else:
        subprocess.Popen(args, start_new_session=True, **kwargs)


def wait_for_exit(pid: int, timeout: float) -> bool:
    """True once process ``pid`` has exited (or never existed); False if it's still running."""
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)


def wait_for_running_install(root: Path, timeout: float = WAIT_FOR_EXIT_S) -> None:
    """At launch: if an update is being installed right now, let it finish first."""
    marker = Path(root) / INSTALLING_FILE
    try:
        pid = int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid != os.getpid():
        wait_for_exit(pid, timeout)


# ── the install itself (run by the NEW build) ────────────────────────────────

def replace_app(source: Path, target: Path, platform: str, copy: Callable[[Path, Path], None] | None = None) -> None:
    """Put the new app at ``source`` in place of the old one at ``target``; on any failure the old
    files are put back. Windows replaces each of the app's own entries in its folder; macOS the bundle."""
    pairs = ([(entry, target / entry.name) for entry in sorted(source.iterdir())] if platform == "windows"
             else [(source, target)])
    copy = copy or (_ditto if platform == "macos" else _copy)
    aside: list[tuple[Path, Path]] = []
    added: list[Path] = []
    try:
        for new, dest in pairs:
            old = dest.with_name(dest.name + OLD_SUFFIX)
            if old.exists() or old.is_symlink():
                _retry(lambda: _remove(old))
            if dest.exists():
                _retry(lambda: dest.rename(old))
                aside.append((dest, old))
            added.append(dest)
            copy(new, dest)
    except BaseException:
        for dest in reversed(added):
            try:
                _retry(lambda: _remove(dest), 5)
            except OSError:
                pass
        for dest, old in reversed(aside):
            _retry(lambda: old.rename(dest))
        raise
    for _, old in aside:
        try:
            _remove(old)
        except OSError:
            pass  # tidied at the next launch


def _copy(src: Path, dest: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest)


def _ditto(src: Path, dest: Path) -> None:
    subprocess.run(["ditto", str(src), str(dest)], check=True, capture_output=True)


def relaunch(target: Path, platform: str, executable_name: str, spawn: Callable = spawn_detached) -> None:
    if platform == "macos":
        spawn(["open", str(target)])
    else:
        spawn([str(target / executable_name)], cwd=str(target))


def run_install(argv: list[str], show_progress: Callable | None = None, spawn: Callable = spawn_detached) -> int:
    """``<new app> --rf-install-update <source> <target> <old app's pid> [--relaunch]``."""
    from .paths import current_platform

    i = argv.index(INSTALL_FLAG)
    source, target, pid = Path(argv[i + 1]), Path(argv[i + 2]), int(argv[i + 3])
    again = RELAUNCH_FLAG in argv
    platform = current_platform()
    root = source.parents[1]
    marker, failed = root / INSTALLING_FILE, root / FAILED_FILE
    executable = Path(sys.executable).name

    def work() -> None:
        try:
            marker.write_text(str(os.getpid()), encoding="utf-8")
            if not wait_for_exit(pid, WAIT_FOR_EXIT_S):
                raise UpdateError("the app didn't close")
            replace_app(source, target, platform)
            failed.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - shown at the next launch; the old app is still there
            try:
                failed.write_text(str(exc) or type(exc).__name__, encoding="utf-8")
            except OSError:
                pass
        finally:
            marker.unlink(missing_ok=True)

    if again and show_progress is not None:
        show_progress(work)
    else:
        work()
    if again:
        relaunch(target, platform, executable, spawn)
    return 0
