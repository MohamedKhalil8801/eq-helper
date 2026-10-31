"""packaging/package_release.py: release names, Windows zip layout, checksums."""

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", ROOT / "packaging" / "package_release.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


def test_release_names_match_what_the_admin_build_cache_expects():
    from eq_helper.admin.build_cache import asset_os
    for system in ("windows", "macos", "linux"):
        name = pr.release_name(system, "1.2.0")
        assert name.startswith("ResonanceFit-1.2.0-") and asset_os(name) == system


def test_windows_zip_has_one_top_folder_with_the_exe(tmp_path):
    app = tmp_path / "dist" / "ResonanceFit"
    (app / "_internal").mkdir(parents=True)
    (app / "ResonanceFit.exe").write_bytes(b"MZ")
    (app / "_internal" / "a.dll").write_bytes(b"x")
    out = pr.package_windows(tmp_path / "dist", tmp_path, "1.2.0")
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["ResonanceFit/ResonanceFit.exe", "ResonanceFit/_internal/a.dll"]


def test_checksums_cover_every_release_file(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"one")
    (tmp_path / "b.dmg").write_bytes(b"two")
    sums = pr.write_checksums(tmp_path).read_text().splitlines()
    assert sums == [f"{hashlib.sha256(b'one').hexdigest()}  a.zip", f"{hashlib.sha256(b'two').hexdigest()}  b.dmg"]


def test_appimage_marker_is_blanked_so_appimagelauncher_leaves_it_alone(tmp_path):
    image = tmp_path / "a.AppImage"
    image.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02" + bytes(100))
    pr.unmark_appimage(image)
    data = image.read_bytes()
    assert data[:8] == b"\x7fELF\x02\x01\x01\x00" and data[8:11] == b"\0\0\0" and len(data) == 111
    pr.unmark_appimage(image)  # already blank: fine
    (tmp_path / "b").write_bytes(b"not an elf" * 10)
    with pytest.raises(SystemExit):
        pr.unmark_appimage(tmp_path / "b")


def test_appdir_is_made_readable_by_everyone(tmp_path):
    import os
    import stat
    if os.name == "nt":
        pytest.skip("POSIX modes")
    private = tmp_path / "AppDir" / "usr"
    private.mkdir(parents=True)
    (private / "lib.so").write_text("x")
    private.chmod(0o700)
    (private / "lib.so").chmod(0o600)
    pr.readable_by_everyone(tmp_path / "AppDir")
    assert stat.S_IMODE(private.stat().st_mode) == 0o755
    assert stat.S_IMODE((private / "lib.so").stat().st_mode) == 0o644

def test_appimage_tells_appimagelauncher_not_to_move_it():
    from eq_helper.calibration.branding import load_branding
    entry = pr.appimage_desktop_entry(load_branding(), "resonancefit")
    assert "X-AppImage-Integrate=false" in entry and "Exec=AppRun %f" in entry
