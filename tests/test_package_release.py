"""packaging/package_release.py: release names, Windows zip layout, checksums."""

import hashlib
import importlib.util
import zipfile
from pathlib import Path

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
