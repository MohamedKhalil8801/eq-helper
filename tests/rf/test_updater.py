"""Updating the app itself: download, checksum, unpack, then the new build replaces the old one."""

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from resonancefit.updater import (
    INSTALL_FLAG, OLD_SUFFIX, RELAUNCH_FLAG, AppLocation, Updater, UpdateCancelled, UpdateError, clean_env,
    is_release_build, replace_app, run_install, wait_for_running_install,
)
from resonancefit.updates import UpdateResult

EXE = "ResonanceFit.exe"


class Response(io.BytesIO):
    def __init__(self, data: bytes, length=True):
        super().__init__(data)
        self.length = len(data) if length else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeDownload:
    def __init__(self, files: dict[str, bytes]):
        self.files, self.opened = files, []

    def open(self, url):
        self.opened.append(url)
        if url not in self.files:
            raise OSError(f"404 {url}")
        return Response(self.files[url])


def windows_zip(version="0.4.0", exe=EXE, extra=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(f"ResonanceFit/{exe}", f"exe {version}")
        z.writestr("ResonanceFit/_internal/lib.dll", f"lib {version}")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buffer.getvalue()


def release(data: bytes, name="ResonanceFit-0.4.0-windows.zip", version="0.4.0", damaged=False):
    digest = hashlib.sha256(data + (b"x" if damaged else b"")).hexdigest()
    http = FakeDownload({"https://dl/app": data, "https://dl/sums": f"{digest}  {name}\nabc  other.dmg\n".encode()})
    result = UpdateResult(True, True, version, "https://page", "https://dl/app", asset_name=name, size=len(data),
                          checksums_url="https://dl/sums")
    return http, result


def windows_app(folder: Path, version="0.3.0") -> AppLocation:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / EXE).write_text(f"exe {version}")
    (folder / "_internal").mkdir(exist_ok=True)
    (folder / "_internal" / "lib.dll").write_text(f"lib {version}")
    (folder / "_internal" / "gone.dll").write_text("only in the old build")
    (folder / "Calibrations").mkdir(exist_ok=True)
    (folder / "Calibrations" / "me.rescal").write_text("mine")
    (folder / "Me - sheet.pdf").write_text("pdf")
    return AppLocation("windows", folder, folder / EXE)


def test_only_packaged_release_versions_update():
    assert is_release_build("0.3.0", frozen=True) and is_release_build("1.10.2", frozen=True)
    assert not is_release_build("0.1.3-dev", frozen=True)   # local builds are stamped like this
    assert not is_release_build("dev", frozen=True)
    assert not is_release_build("0.3.0", frozen=False)       # a source run


def test_where_the_app_lives_on_each_system(tmp_path):
    exe = tmp_path / "RF" / EXE
    assert AppLocation.detect("windows", True, str(exe), {}) == AppLocation("windows", exe.parent, exe)
    assert AppLocation.detect("windows", False, str(exe), {}) is None
    mac = tmp_path / "Applications" / "ResonanceFit.app" / "Contents" / "MacOS" / "ResonanceFit"
    assert AppLocation.detect("macos", True, str(mac), {}).target == tmp_path / "Applications" / "ResonanceFit.app"
    assert AppLocation.detect("macos", True, str(tmp_path / "bin" / "x" / "ResonanceFit"), {}) is None
    image = tmp_path / "ResonanceFit-0.3.0-linux.AppImage"
    linux = AppLocation.detect("linux", True, "/tmp/.mount_x/ResonanceFit", {"APPIMAGE": str(image)})
    assert linux.target == image and linux.executable == image
    assert AppLocation.detect("linux", True, "/opt/rf/ResonanceFit", {}) is None  # not an AppImage


def test_problems_that_fall_back_to_the_download_link(tmp_path):
    translocated = tmp_path / "AppTranslocation" / "X" / "ResonanceFit.app"
    translocated.mkdir(parents=True)
    assert "temporary copy" in AppLocation("macos", translocated, translocated).problem()
    app = windows_app(tmp_path / "RF")
    assert app.problem() == ""
    assert Updater(None, tmp_path / "u", "0.3.0").problem()


def test_download_checks_and_unpacks_then_waits_for_install(tmp_path):
    location = windows_app(tmp_path / "RF")
    http, result = release(windows_zip())
    updater = Updater(location, tmp_path / "data" / "updates", "0.3.0", http=http)
    seen = []
    staged = updater.download(result, progress=lambda done, total: seen.append((done, total)))
    assert staged.version == "0.4.0" and (staged.path / EXE).read_text() == "exe 0.4.0"
    assert (staged.path / "_internal" / "lib.dll").is_file()
    assert seen[-1][0] == seen[-1][1] == len(windows_zip())
    assert not list(staged.path.parent.glob("*.zip*"))       # the archive is gone once unpacked
    assert updater.ready() == staged
    assert Updater(location, tmp_path / "data" / "updates", "0.3.0").ready() == staged  # survives a restart
    http.opened.clear()
    assert updater.download(result) == staged and http.opened == []  # not downloaded twice


def test_damaged_download_is_refused(tmp_path):
    http, result = release(windows_zip(), damaged=True)
    updater = Updater(windows_app(tmp_path / "RF"), tmp_path / "u", "0.3.0", http=http)
    with pytest.raises(UpdateError, match="damaged"):
        updater.download(result)
    assert updater.ready() is None


def test_release_without_a_checksum_or_download_is_refused(tmp_path):
    http, result = release(windows_zip(), name="ResonanceFit-0.4.0-windows.zip")
    updater = Updater(windows_app(tmp_path / "RF"), tmp_path / "u", "0.3.0", http=http)
    with pytest.raises(UpdateError, match="no checksum"):
        updater.download(UpdateResult(True, True, "0.4.0", "", "https://dl/app", asset_name="Other.zip",
                                      checksums_url="https://dl/sums"))
    with pytest.raises(UpdateError, match="no download"):
        updater.download(UpdateResult(True, True, "0.4.0", "", "https://dl/app"))


def test_download_can_be_cancelled(tmp_path):
    http, result = release(windows_zip() * 50)
    updater = Updater(windows_app(tmp_path / "RF"), tmp_path / "u", "0.3.0", http=http)
    with pytest.raises(UpdateCancelled):
        updater.download(result, cancelled=lambda: True)
    assert updater.ready() is None


def test_zip_escaping_its_folder_is_refused(tmp_path):
    http, result = release(windows_zip(extra={"ResonanceFit/../../evil.txt": "x"}))
    updater = Updater(windows_app(tmp_path / "RF"), tmp_path / "u", "0.3.0", http=http)
    with pytest.raises(UpdateError, match="laid out"):
        updater.download(result)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "u" / "evil.txt").exists()


def test_windows_install_starts_the_new_build_to_replace_the_old(tmp_path):
    location = windows_app(tmp_path / "RF")
    http, result = release(windows_zip())
    spawned = []
    updater = Updater(location, tmp_path / "u", "0.3.0", http=http, spawn=lambda args, cwd=None: spawned.append(args))
    assert updater.install(relaunch=True) is False            # nothing downloaded yet
    staged = updater.download(result)
    assert updater.install(relaunch=True)
    assert spawned[-1] == [str(staged.path / EXE), INSTALL_FLAG, str(staged.path), str(location.target),
                           str(os.getpid()), RELAUNCH_FLAG]
    updater.install(relaunch=False)
    assert RELAUNCH_FLAG not in spawned[-1]


def test_replacing_keeps_the_customers_files(tmp_path):
    location = windows_app(tmp_path / "RF")
    new = tmp_path / "new"
    (new / "_internal").mkdir(parents=True)
    (new / EXE).write_text("exe 0.4.0")
    (new / "_internal" / "lib.dll").write_text("lib 0.4.0")
    replace_app(new, location.target, "windows")
    target = location.target
    assert (target / EXE).read_text() == "exe 0.4.0"
    assert (target / "_internal" / "lib.dll").read_text() == "lib 0.4.0"
    assert not (target / "_internal" / "gone.dll").exists()   # the old build's folder is replaced whole
    assert (target / "Calibrations" / "me.rescal").read_text() == "mine" and (target / "Me - sheet.pdf").exists()
    assert not list(target.glob(f"*{OLD_SUFFIX}"))


def test_a_failed_replace_puts_the_old_app_back(tmp_path):
    location = windows_app(tmp_path / "RF")
    new = tmp_path / "new"
    (new / "_internal").mkdir(parents=True)
    (new / EXE).write_text("exe 0.4.0")
    (new / "_internal" / "lib.dll").write_text("lib 0.4.0")

    def copy(src, dest):
        if src.name == EXE:
            raise OSError("disk full")
        import shutil
        shutil.copytree(src, dest)

    with pytest.raises(OSError, match="disk full"):
        replace_app(new, location.target, "windows", copy=copy)
    target = location.target
    assert (target / EXE).read_text() == "exe 0.3.0"
    assert (target / "_internal" / "lib.dll").read_text() == "lib 0.3.0"
    assert (target / "_internal" / "gone.dll").exists()
    assert not list(target.glob(f"*{OLD_SUFFIX}"))


def _finished_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_run_install_replaces_then_restarts(tmp_path, monkeypatch):
    location = windows_app(tmp_path / "RF")
    http, result = release(windows_zip())
    staged = Updater(location, tmp_path / "u", "0.3.0", http=http).download(result)
    pid = _finished_pid()
    monkeypatch.setattr("resonancefit.paths.current_platform", lambda: "windows")
    monkeypatch.setattr(sys, "executable", str(staged.path / EXE))
    spawned = []
    argv = ["x", INSTALL_FLAG, str(staged.path), str(location.target), str(pid), RELAUNCH_FLAG]
    assert run_install(argv, spawn=lambda args, cwd=None: spawned.append(args)) == 0
    assert (location.target / EXE).read_text() == "exe 0.4.0"
    assert spawned == [[str(location.target / EXE)]]
    assert not (tmp_path / "u" / "installing").exists() and not (tmp_path / "u" / "failed.txt").exists()
    after = Updater(location, tmp_path / "u", "0.4.0")       # the new version's first launch
    assert after.ready() is None
    after.tidy()
    assert not (tmp_path / "u").exists()


def test_run_install_failure_is_remembered_and_the_old_app_restarts(tmp_path, monkeypatch):
    location = windows_app(tmp_path / "RF")
    http, result = release(windows_zip())
    updater = Updater(location, tmp_path / "u", "0.3.0", http=http)
    staged = updater.download(result)
    monkeypatch.setattr("resonancefit.paths.current_platform", lambda: "windows")
    monkeypatch.setattr("resonancefit.updater.replace_app", lambda *a: (_ for _ in ()).throw(OSError("locked")))
    spawned = []
    argv = ["x", INSTALL_FLAG, str(staged.path), str(location.target), str(_finished_pid()), RELAUNCH_FLAG]
    run_install(argv, spawn=lambda args, cwd=None: spawned.append(args))
    assert updater.last_failure() == "locked" and updater.ready() == staged  # offered again next time
    assert (location.target / EXE).read_text() == "exe 0.3.0" and spawned


def test_linux_swaps_the_appimage(tmp_path):
    image = tmp_path / "ResonanceFit-0.3.0-linux.AppImage"
    image.write_bytes(b"old")
    location = AppLocation("linux", image, image)
    data = b"new appimage"
    http, result = release(data, name="ResonanceFit-0.4.0-linux.AppImage")
    spawned = []
    updater = Updater(location, tmp_path / "u", "0.3.0", http=http, spawn=lambda args, cwd=None: spawned.append(args))
    updater.download(result)
    assert updater.install(relaunch=False) and image.read_bytes() == data and spawned == []
    assert not list(tmp_path.glob(".*new-update"))
    assert updater.install(relaunch=True)
    assert spawned[0][:2] == ["/bin/sh", "-c"] and spawned[0][-2:] == [str(os.getpid()), str(image)]


def test_macos_copies_the_app_out_of_the_disk_image(tmp_path):
    bundle = tmp_path / "Applications" / "ResonanceFit.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    exe = bundle / "Contents" / "MacOS" / "ResonanceFit"
    exe.write_text("old")
    location = AppLocation("macos", bundle, exe)

    class Tools:
        def copy_app_from_dmg(self, dmg, folder):
            assert dmg.name.endswith(".dmg")
            app = folder / "ResonanceFit.app" / "Contents" / "MacOS"
            app.mkdir(parents=True)
            (app / "ResonanceFit").write_text("new")
            return folder / "ResonanceFit.app"

    http, result = release(b"dmg", name="ResonanceFit-0.4.0-macos.dmg")
    spawned = []
    updater = Updater(location, tmp_path / "u", "0.3.0", http=http, platform_tools=Tools(),
                      spawn=lambda args, cwd=None: spawned.append(args))
    staged = updater.download(result)
    assert staged.path.name == "ResonanceFit.app" and not list(staged.path.parent.glob("*.dmg"))
    updater.install(relaunch=True)
    assert spawned[0][:3] == [str(staged.path / "Contents" / "MacOS" / "ResonanceFit"), INSTALL_FLAG,
                              str(staged.path)]
    assert spawned[0][3] == str(bundle)


def test_tidy_removes_leftovers_but_keeps_a_pending_update(tmp_path):
    location = windows_app(tmp_path / "RF")
    http, result = release(windows_zip())
    updater = Updater(location, tmp_path / "u", "0.3.0", http=http)
    staged = updater.download(result)
    (tmp_path / "u" / "0.3.9").mkdir()
    (location.target / f"_internal{OLD_SUFFIX}").mkdir()
    updater.tidy()
    assert updater.ready() == staged and not (tmp_path / "u" / "0.3.9").exists()
    assert not (location.target / f"_internal{OLD_SUFFIX}").exists()
    assert (location.target / "Calibrations").exists()


def test_new_processes_get_a_clean_environment():
    bundle = os.path.join("C:", "RF", "_internal")
    env = {"PATH": os.pathsep.join([bundle, os.path.join(bundle, "PySide6"), "C:/Windows"]),
           "_PYI_APPLICATION_HOME_DIR": bundle, "_MEIPASS2": bundle, "LD_LIBRARY_PATH": bundle,
           "LD_LIBRARY_PATH_ORIG": "/usr/lib", "APPIMAGE": "/x.AppImage", "QT_PLUGIN_PATH": bundle, "HOME": "/h"}
    clean = clean_env(env, bundle=bundle)
    assert clean["PATH"] == "C:/Windows" and clean["LD_LIBRARY_PATH"] == "/usr/lib"
    assert not {"_PYI_APPLICATION_HOME_DIR", "_MEIPASS2", "APPIMAGE", "QT_PLUGIN_PATH"} & set(clean)
    assert clean["HOME"] == "/h" and clean["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_launch_waits_only_for_a_live_install(tmp_path):
    wait_for_running_install(tmp_path)                        # no marker: nothing to wait for
    (tmp_path / "installing").write_text(str(_finished_pid()))
    wait_for_running_install(tmp_path, timeout=5)             # the installer is gone: returns at once
