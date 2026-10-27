"""Packaging customer downloads per OS from prebuilt apps."""

import stat
import tarfile
import zipfile

import pytest

from eq_helper.admin.packager import (
    calibration_only, first_launch_text, package, package_name, rescal_name,
)
from eq_helper.calibration.branding import Branding
from eq_helper.calibration.rescal import write_rescal
from resonancefit.library import CalibrationLibrary
from resonancefit.paths import AppPaths
from tests.rf.conftest import make_calibration

BR = Branding()


@pytest.fixture
def inputs(tmp_path):
    cal = make_calibration(customer="Ada Lovelace", model="Nova")
    rescal = write_rescal(tmp_path / "in" / "x.rescal", cal)
    sheet = tmp_path / "in" / "sheet.pdf"
    sheet.write_bytes(b"%PDF-1.4 sheet")
    targets = []
    for name in ("PEQdB Diamond.txt", "Harman IE 2019.txt"):
        p = tmp_path / "in" / name
        p.write_text("20 70\n1000 75\n20000 70\n")
        targets.append(p)
    builds = tmp_path / "builds"
    builds.mkdir()
    win = builds / "ResonanceFit-1.0.0-windows.zip"
    with zipfile.ZipFile(win, "w") as z:
        z.writestr("ResonanceFit/ResonanceFit.exe", b"MZ")
        z.writestr("ResonanceFit/_internal/python313.dll", b"dll")
    lin = builds / "ResonanceFit-1.0.0-linux.AppImage"
    lin.write_bytes(b"\x7fELF appimage")
    mac = builds / "ResonanceFit-1.0.0-macos.dmg"
    mac.write_bytes(b"dmg")
    return cal, rescal, sheet, targets, {"windows": win, "linux": lin, "macos": mac}


def test_names_use_branding_and_are_safe(inputs):
    cal = inputs[0]
    assert rescal_name(cal.manifest) == "Ada Lovelace – Truthear Nova.rescal"
    assert package_name("windows", cal.manifest, BR) == "ResonanceFit – Ada Lovelace – Truthear Nova (Windows).zip"
    assert package_name("linux", cal.manifest, Branding(product_name="Ear/Fit")).startswith("Ear-Fit – ")
    assert package_name("linux", cal.manifest, BR).endswith("(Linux).tar.gz")


def test_windows_zip_layout_and_discovery(tmp_path, inputs):
    cal, rescal, sheet, targets, builds = inputs
    out = package("windows", builds["windows"], rescal, sheet, targets, tmp_path / "out", BR, cal.manifest)
    assert out.name == package_name("windows", cal.manifest, BR)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        root = "ResonanceFit – Ada Lovelace/"
        assert root + "ResonanceFit.exe" in names and root + "_internal/python313.dll" in names
        assert root + "Calibrations/Ada Lovelace – Truthear Nova.rescal" in names
        assert root + "Targets/PEQdB Diamond.txt" in names and root + "Targets/Harman IE 2019.txt" in names
        assert root + "First launch.txt" in names
        assert any(n.startswith(root) and n.endswith(".pdf") for n in names)
        z.extractall(tmp_path / "x")
    app_dir = tmp_path / "x" / root.rstrip("/")
    lib = CalibrationLibrary(AppPaths("windows", tmp_path / "data", app_dir))
    assert [e.calibration_id for e in lib.scan().imported] == [cal.manifest.calibration_id]


def test_linux_tar_layout_executable_appimage_and_discovery(tmp_path, inputs):
    cal, rescal, sheet, targets, builds = inputs
    out = package("linux", builds["linux"], rescal, sheet, targets, tmp_path / "out", BR, cal.manifest)
    with tarfile.open(out, "r:gz") as t:
        members = {m.name: m for m in t.getmembers()}
        root = "ResonanceFit – Ada Lovelace/"
        image = members[root + "ResonanceFit-1.0.0-linux.AppImage"]
        assert image.mode & stat.S_IXUSR
        assert root + "Calibrations/Ada Lovelace – Truthear Nova.rescal" in members
        assert root + "Targets/PEQdB Diamond.txt" in members and root + "First launch.txt" in members
        t.extractall(tmp_path / "x", filter="data")
    app_dir = tmp_path / "x" / root.rstrip("/")
    lib = CalibrationLibrary(AppPaths("linux", tmp_path / "data", app_dir))
    assert len(lib.scan().imported) == 1


def test_macos_zip_holds_dmg_calibration_and_sheet(tmp_path, inputs):
    cal, rescal, sheet, targets, builds = inputs
    out = package("macos", builds["macos"], rescal, sheet, targets, tmp_path / "out", BR, cal.manifest)
    with zipfile.ZipFile(out) as z:
        names = {n.split("/", 1)[1] for n in z.namelist()}
    assert "ResonanceFit-1.0.0-macos.dmg" in names
    assert "Ada Lovelace – Truthear Nova.rescal" in names
    assert "First launch.txt" in names and any(n.endswith(".pdf") for n in names)


def test_calibration_file_only(tmp_path, inputs):
    cal, rescal, sheet, *_ = inputs
    files = calibration_only(rescal, sheet, tmp_path / "out", BR, cal.manifest)
    assert sorted(p.name for p in files)[1] == "Ada Lovelace – Truthear Nova.rescal"
    assert all(p.exists() for p in files)


def test_first_launch_texts_explain_unsigned_apps():
    assert "Run anyway" in first_launch_text("windows", BR)
    assert "Open" in first_launch_text("macos", BR) and ".rescal" in first_launch_text("macos", BR)
    assert "chmod +x" in first_launch_text("linux", BR)
    assert "ResonanceFit" in first_launch_text("windows", BR)


def test_rejects_unsafe_prebuilt_zip(tmp_path, inputs):
    cal, rescal, sheet, targets, _ = inputs
    bad = tmp_path / "bad-windows.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.exe", b"x")
    with pytest.raises(ValueError, match="unsafe"):
        package("windows", bad, rescal, sheet, targets, tmp_path / "out", BR, cal.manifest)
