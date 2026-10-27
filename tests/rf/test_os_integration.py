"""Release-build integration: version, self-test, single instance, file associations."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QFileOpenEvent

from eq_helper.calibration.branding import Branding
from eq_helper.calibration.rescal import write_rescal
from resonancefit.app import ICON
from resonancefit.context import AppContext
from resonancefit.os_integration import FileAssociation, desktop_id, open_command, prog_id, target_executable
from resonancefit.paths import AppPaths
from resonancefit.single_instance import FileOpenFilter, SingleInstance, server_name
from resonancefit.ui.main_window import MainWindow
from resonancefit.version import app_version

from .conftest import make_calibration

ROOT = Path(__file__).resolve().parents[2]


def test_version_file_wins_then_metadata(tmp_path):
    f = tmp_path / "version.txt"
    f.write_text("1.2.3\n")
    assert app_version(f) == "1.2.3"
    assert app_version(tmp_path / "missing.txt") not in ("", None)


def test_icon_assets_exist():
    assert ICON.is_file()
    for name in ("icon.png", "icon.ico", "icon.icns"):
        assert (ROOT / "resonancefit" / "assets" / name).stat().st_size > 1000


def test_self_test_passes_in_a_fresh_process(tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg"),
           "APPDATA": str(tmp_path / "appdata"), "EQ_HELPER_NO_AUDIO": "1", "PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-m", "resonancefit", "--self-test"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SELF-TEST PASSED" in proc.stdout
    assert "export equalizer-apo: " in proc.stdout and "calibration loaded: yes" in proc.stdout


def test_second_launch_forwards_files_to_the_first(qtbot, tmp_path):
    name = server_name(f"rf-test-{os.getpid()}-{tmp_path.name}")
    first, received = SingleInstance(name), []
    assert first.listen(received.extend)
    target = str(tmp_path / "a.rescal")
    probe = ("import sys\nfrom PySide6.QtCore import QCoreApplication\napp = QCoreApplication([])\n"
             "from resonancefit.single_instance import SingleInstance\n"
             f"sys.exit(0 if SingleInstance({name!r}).forward([{target!r}]) else 1)\n")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "QT_QPA_PLATFORM": "offscreen"}
    second = subprocess.Popen([sys.executable, "-c", probe], env=env)  # a real second launch
    qtbot.waitUntil(lambda: received == [target], timeout=20000)
    qtbot.waitUntil(lambda: second.poll() is not None, timeout=20000)
    assert second.returncode == 0
    first.server.close()
    assert not SingleInstance(name).forward([])  # nobody listening any more


def test_macos_file_open_event_is_handled():
    opened = []
    filt = FileOpenFilter(opened.append)
    event = QFileOpenEvent(QUrl.fromLocalFile("/tmp/x.rescal"))
    assert event.type() == QEvent.FileOpen
    assert filt.eventFilter(None, event)
    assert opened == ["/tmp/x.rescal"]


class FakeWinreg:
    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKey(self, root, path):  # noqa: N802
        return self._Key(self, path)

    def OpenKey(self, root, path):  # noqa: N802
        if path not in self.values:
            raise OSError(path)
        return self._Key(self, path)

    def SetValueEx(self, key, name, _r, _t, value):  # noqa: N802
        self.values[key.path] = value

    def QueryValueEx(self, key, name):  # noqa: N802
        return self.values[key.path], 1


def test_windows_association_per_user():
    reg, br = FakeWinreg(), Branding()
    assoc = FileAssociation("windows", br, Path(r"C:\Apps\ResonanceFit\ResonanceFit.exe"), winreg=reg)
    assert assoc.supported and not assoc.registered()
    assoc.register()
    assert reg.values[r"Software\Classes\.rescal"] == prog_id(br)
    assert reg.values[rf"Software\Classes\{prog_id(br)}\shell\open\command"] == open_command(assoc.exe)
    assert assoc.registered()
    moved = FileAssociation("windows", br, Path(r"D:\Elsewhere\ResonanceFit.exe"), winreg=reg)
    assert not moved.registered()  # app moved: offer again would fix it


def test_linux_association_writes_user_mime_and_desktop_files(tmp_path):
    ran, br = [], Branding()
    image = tmp_path / "ResonanceFit-1.0.0-linux.AppImage"
    assoc = FileAssociation("linux", br, image, icon=ICON, env={"XDG_DATA_HOME": str(tmp_path / "share")},
                            run=ran.append)
    assert not assoc.registered()
    assoc.register()
    share = tmp_path / "share"
    desktop = (share / "applications" / f"{desktop_id(br)}.desktop").read_text()
    assert f'Exec="{image}" %f' in desktop and "MimeType=application/x-rescal;" in desktop
    assert "*.rescal" in (share / "mime" / "packages" / f"{desktop_id(br)}-rescal.xml").read_text()
    assert (share / "icons" / "hicolor" / "256x256" / "apps" / f"{desktop_id(br)}.png").exists()
    assert ["xdg-mime", "default", f"{desktop_id(br)}.desktop", "application/x-rescal"] in ran
    assert assoc.registered()


def test_only_release_builds_offer_association():
    assert target_executable("windows", env={}, frozen=False) is None
    assert target_executable("windows", env={}, frozen=True, executable="C:/a.exe") == Path("C:/a.exe")
    assert target_executable("linux", env={}, frozen=True) is None  # not an AppImage
    assert target_executable("linux", env={"APPIMAGE": "/x.AppImage"}, frozen=True) == Path("/x.AppImage")
    assert target_executable("macos", env={}, frozen=True) is None  # Info.plist handles it


class FakeAssociation:
    supported = True

    def __init__(self):
        self.done = False

    def registered(self):
        return self.done

    def register(self):
        self.done = True


def test_association_offered_once_after_walkthrough(qtbot, tmp_path):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration())
    assoc, asked = FakeAssociation(), []
    ctx = AppContext.create(AppPaths("windows", tmp_path / "data", tmp_path / "app"))
    w = MainWindow(ctx, start_audio=False, association=assoc)
    w.ask_file_association = lambda: asked.append(1) or True
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: assoc.done, timeout=20000)
    assert asked == [1] and ctx.settings.get("file_association_offered")
    w.offer_file_association()
    assert asked == [1]


def test_open_files_imports_and_ignores_other_files(qtbot, tmp_path):
    w = MainWindow(AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app")), start_audio=False,
                   association=FakeAssociation())
    qtbot.addWidget(w)
    src = write_rescal(tmp_path / "x.rescal", make_calibration(customer="Forwarded"))
    w.open_files([str(tmp_path / "notes.txt"), str(src)], activate=False)
    qtbot.waitUntil(lambda: w.current is not None and w.current.manifest.customer.name == "Forwarded", timeout=20000)
