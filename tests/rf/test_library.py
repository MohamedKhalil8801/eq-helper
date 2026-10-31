"""Calibration discovery: sibling/data folders, import, duplicates, selection."""

from pathlib import Path

import pytest

from resonancefit.library import CalibrationLibrary
from resonancefit.paths import AppPaths, is_translocated, should_show_import_screen


@pytest.fixture
def win_paths(tmp_path):
    app = tmp_path / "app"
    (app / "Calibrations").mkdir(parents=True)
    return AppPaths(platform="windows", data_dir=tmp_path / "data", app_dir=app)


def test_platform_data_dirs(tmp_path):
    env = {"APPDATA": str(tmp_path / "Roaming")}
    assert AppPaths.default_data_dir("windows", env, tmp_path) == tmp_path / "Roaming" / "ResonanceFit"
    assert AppPaths.default_data_dir("macos", {}, tmp_path) == \
        tmp_path / "Library" / "Application Support" / "ResonanceFit"
    assert AppPaths.default_data_dir("linux", {"XDG_DATA_HOME": str(tmp_path / "xdg")}, tmp_path) == \
        tmp_path / "xdg" / "resonancefit"
    assert AppPaths.default_data_dir("linux", {}, tmp_path) == tmp_path / ".local" / "share" / "resonancefit"
    p = AppPaths(platform="linux", data_dir=tmp_path / "d", app_dir=tmp_path / "a")
    assert p.settings_path == tmp_path / "d" / "settings.json"
    assert ".eq_helper" not in str(p.settings_path)


def test_app_dir_detection(tmp_path):
    exe = tmp_path / "RF" / "app.exe"
    assert AppPaths.detect_app_dir(frozen=True, executable=str(exe), env={}) == exe.parent
    image = tmp_path / "dl" / "RF.AppImage"
    assert AppPaths.detect_app_dir(frozen=True, executable="/tmp/.mount_x/usr/bin/rf",
                                   env={"APPIMAGE": str(image)}) == image.parent
    assert AppPaths.detect_app_dir(frozen=False, executable="python", env={}) is None


def test_scans_sibling_calibrations_folder_every_launch(win_paths, write_calibration):
    write_calibration(win_paths.app_dir / "Calibrations" / "Ada – Nova.rescal")
    lib = CalibrationLibrary(win_paths)
    report = lib.scan()
    assert len(report.imported) == 1 and not report.problems
    assert [e.manifest.customer.name for e in lib.list()] == ["Ada"]
    # A second launch re-scans but imports nothing new.
    assert CalibrationLibrary(win_paths).scan().imported == []
    assert len(CalibrationLibrary(win_paths).list()) == 1


def test_new_file_dropped_later_is_imported_on_next_launch(win_paths, write_calibration):
    write_calibration(win_paths.app_dir / "Calibrations" / "a.rescal", customer="Ada")
    CalibrationLibrary(win_paths).scan()
    write_calibration(win_paths.app_dir / "Calibrations" / "b.RESCAL", customer="Bob", model="Aria")
    lib = CalibrationLibrary(win_paths)
    assert [e.manifest.customer.name for e in lib.scan().imported] == ["Bob"]
    assert {e.manifest.customer.name for e in lib.list()} == {"Ada", "Bob"}


def test_imported_copy_survives_source_deletion(win_paths, write_calibration):
    src = write_calibration(win_paths.app_dir / "Calibrations" / "a.rescal")
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    src.unlink()
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    (entry,) = lib.list()
    assert entry.path.exists() and entry.path.parent == win_paths.library_dir
    assert lib.load(entry.calibration_id).manifest.customer.name == "Ada"


def test_duplicate_calibration_id_not_imported_twice(win_paths, write_calibration, tmp_path):
    a = write_calibration(win_paths.app_dir / "Calibrations" / "a.rescal", calibration_id="11111111-1111-4111-8111-111111111111")
    b = write_calibration(tmp_path / "copy.rescal", calibration_id="11111111-1111-4111-8111-111111111111")
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    again = lib.import_file(b)
    assert len(lib.list()) == 1 and again.calibration_id == lib.list()[0].calibration_id
    # The same id with new contents (operator re-issued it) replaces the stored copy.
    c = write_calibration(tmp_path / "reissued.rescal", calibration_id="11111111-1111-4111-8111-111111111111",
                          customer="Ada K.")
    lib.import_file(c)
    assert [e.manifest.customer.name for e in lib.list()] == ["Ada K."]
    assert a.exists()


def test_newer_created_date_selected_by_default(win_paths, write_calibration):
    cal = win_paths.app_dir / "Calibrations"
    write_calibration(cal / "old.rescal", customer="Old", created="2026-01-02")
    write_calibration(cal / "new.rescal", customer="New", created="2026-10-01")
    write_calibration(cal / "mid.rescal", customer="Mid", created="2026-05-01")
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    assert [e.manifest.customer.name for e in lib.list()] == ["New", "Mid", "Old"]
    assert lib.selected.manifest.customer.name == "New"
    old = next(e for e in lib.list() if e.manifest.customer.name == "Old")
    lib.select(old.calibration_id)
    assert CalibrationLibrary(win_paths).selected.calibration_id == old.calibration_id
    with pytest.raises(KeyError):
        lib.select("nope")


def test_remove_is_not_undone_by_next_scan_but_explicit_import_restores(win_paths, write_calibration):
    src = write_calibration(win_paths.app_dir / "Calibrations" / "a.rescal")
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    (entry,) = lib.list()
    lib.remove(entry.calibration_id)
    assert lib.list() == [] and not entry.path.exists() and lib.selected is None
    assert CalibrationLibrary(win_paths).scan().imported == []
    lib = CalibrationLibrary(win_paths)
    lib.import_file(src)
    assert len(lib.list()) == 1


def test_macos_uses_data_folder_not_sibling(tmp_path, write_calibration):
    app = tmp_path / "Volumes" / "RF"
    (app / "Calibrations").mkdir(parents=True)
    write_calibration(app / "Calibrations" / "sibling.rescal", customer="Sibling")
    paths = AppPaths(platform="macos", data_dir=tmp_path / "data", app_dir=app)
    assert paths.scan_dirs == [tmp_path / "data" / "Calibrations"]
    assert paths.calibrations_folder == tmp_path / "data" / "Calibrations"
    write_calibration(paths.calibrations_folder / "data.rescal", customer="Data")
    lib = CalibrationLibrary(paths)
    lib.scan()
    assert [e.manifest.customer.name for e in lib.list()] == ["Data"]


def test_translocated_path_detected_shows_import_screen(tmp_path):
    moved = Path("/private/var/folders/ab/T/AppTranslocation/1234-ABCD/d/RF.app/Contents/MacOS")
    assert is_translocated(moved)
    assert not is_translocated(Path("/Applications/RF.app/Contents/MacOS"))
    paths = AppPaths(platform="macos", data_dir=tmp_path / "data", app_dir=moved)
    lib = CalibrationLibrary(paths)
    lib.scan()
    assert should_show_import_screen(lib)


def test_invalid_file_reported_not_fatal(win_paths, write_calibration):
    cal = win_paths.app_dir / "Calibrations"
    (cal / "broken.rescal").write_text("not a zip")
    write_calibration(cal / "good.rescal")
    lib = CalibrationLibrary(win_paths)
    report = lib.scan()
    assert len(report.imported) == 1
    assert [(p.name, "not a calibration file" in m) for p, m in report.problems] == [("broken.rescal", True)]
    assert not should_show_import_screen(lib)


def test_corrupt_index_rebuilds_from_store(win_paths, write_calibration):
    write_calibration(win_paths.app_dir / "Calibrations" / "a.rescal")
    CalibrationLibrary(win_paths).scan()
    win_paths.library_index.write_text("{broken")
    lib = CalibrationLibrary(win_paths)
    assert len(lib.list()) == 1


def test_library_mirrors_the_calibrations_folder(win_paths, write_calibration, tmp_path):
    """A calibration replaced or deleted in the folder is forgotten at the next launch; explicit
    imports from elsewhere stay; an emptied folder forgets nothing."""
    cal = win_paths.app_dir / "Calibrations"
    write_calibration(cal / "Nova.rescal", customer="Old Nova")
    write_calibration(cal / "Aria.rescal", customer="Aria")
    write_calibration(cal / "Kato.rescal", customer="Kato")
    CalibrationLibrary(win_paths).scan()
    elsewhere = CalibrationLibrary(win_paths).import_file(write_calibration(tmp_path / "x.rescal", customer="Mail"))
    write_calibration(cal / "Nova.rescal", customer="New Nova")  # re-issued: a new calibration id
    (cal / "Kato.rescal").unlink()
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    assert {e.manifest.customer.name for e in lib.list()} == {"New Nova", "Aria", "Mail"}
    assert len(list(win_paths.library_dir.glob("*.rescal"))) == 3
    assert elsewhere.calibration_id in {e.calibration_id for e in lib.list()}
    for f in cal.iterdir():
        f.unlink()
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    assert len(lib.list()) == 3  # the download may just have been emptied or moved


def test_libraries_from_before_sources_were_kept_are_tidied(win_paths, write_calibration):
    import json
    cal = win_paths.app_dir / "Calibrations"
    write_calibration(cal / "a.rescal", customer="Kept")
    write_calibration(cal / "b.rescal", customer="Gone")
    CalibrationLibrary(win_paths).scan()
    index = json.loads(win_paths.library_index.read_text(encoding="utf-8"))
    index.pop("sources")
    for info in index["imported"].values():
        info.pop("source", None)
    win_paths.library_index.write_text(json.dumps(index), encoding="utf-8")
    (cal / "b.rescal").unlink()
    lib = CalibrationLibrary(win_paths)
    lib.scan()
    assert [e.manifest.customer.name for e in lib.list()] == ["Kept"]
