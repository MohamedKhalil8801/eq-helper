"""Target library: shipped + customer targets, default resolution, import."""

import numpy as np
import pytest

from eq_helper.dsp.overlay import load_frequency_response
from eq_helper.dsp.resonance import Measurements, match_target
from resonancefit.paths import AppPaths
from resonancefit.targets import BUNDLED_TARGETS_DIR, DEFAULT_TARGET, TargetLibrary


def _write_target(path, tilt=0.0, level=75.0):
    f = np.geomspace(20, 20000, 384)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(path, np.c_[f, level + tilt * np.log10(f / 1000)], fmt="%.4f")
    return path


@pytest.fixture
def bundled(tmp_path):
    folder = tmp_path / "bundled"
    _write_target(folder / "PEQdB Diamond.txt", -2)
    _write_target(folder / "Harman IE 2019.txt", -4)
    return folder


@pytest.fixture
def paths(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    return AppPaths(platform="windows", data_dir=tmp_path / "data", app_dir=app)


def test_real_bundled_targets_present_and_valid():
    lib = TargetLibrary(AppPaths(platform="macos", data_dir=BUNDLED_TARGETS_DIR.parent / "_unused"),
                        bundled_dir=BUNDLED_TARGETS_DIR)
    targets, invalid = lib.scan()
    assert invalid == []
    assert len(targets) == 14
    assert lib.default_for(None).filename == DEFAULT_TARGET == "PEQdB Diamond.txt"


def test_shipped_user_and_bundled_merged_by_precedence(paths, bundled):
    _write_target(paths.shipped_targets_dir / "PEQdB Diamond.txt", +3)   # operator's shipped copy
    _write_target(paths.shipped_targets_dir / "Knowles.txt")
    _write_target(paths.user_targets_dir / "Mine.csv")
    lib = TargetLibrary(paths, bundled_dir=bundled)
    targets, invalid = lib.scan()
    assert invalid == []
    by_name = {t.filename: t for t in targets}
    assert set(by_name) == {"PEQdB Diamond.txt", "Harman IE 2019.txt", "Knowles.txt", "Mine.csv"}
    assert by_name["PEQdB Diamond.txt"].source == "shipped"
    assert by_name["Harman IE 2019.txt"].source == "bundled"
    assert by_name["Mine.csv"].source == "user" and by_name["Mine.csv"].name == "Mine"
    assert [t.filename for t in targets][0] == "Harman IE 2019.txt"  # sorted by name


def test_default_resolution_and_missing_default_fallback(paths, bundled):
    lib = TargetLibrary(paths, bundled_dir=bundled)
    lib.scan()
    assert lib.default_for("harman ie 2019.TXT").filename == "Harman IE 2019.txt"
    assert lib.default_for("Gone.txt").filename == "PEQdB Diamond.txt"
    (bundled / "PEQdB Diamond.txt").unlink()
    lib.scan()
    assert lib.default_for("Gone.txt").filename == "Harman IE 2019.txt"
    empty = TargetLibrary(paths, bundled_dir=paths.data_dir / "none")
    empty.scan()
    assert empty.default_for(None) is None


def test_import_adds_to_user_targets_and_rejects_bad(paths, bundled, tmp_path):
    lib = TargetLibrary(paths, bundled_dir=bundled)
    lib.scan()
    src = _write_target(tmp_path / "dl" / "My Target.txt", 1)
    t = lib.import_file(src)
    assert t.source == "user" and t.path.parent == paths.user_targets_dir and t.path.exists()
    assert lib.import_file(src).path == t.path  # identical file: no duplicate
    _write_target(src, 2)
    assert lib.import_file(src).path.name == "My Target (2).txt"
    assert len([x for x in lib.targets if x.name.startswith("My Target")]) == 2
    narrow = tmp_path / "narrow.txt"
    np.savetxt(narrow, [[2000, 0], [20000, 0]])
    with pytest.raises(ValueError, match="1 kHz"):
        lib.import_file(narrow)
    junk = tmp_path / "junk.txt"
    junk.write_text("hello")
    with pytest.raises(ValueError):
        lib.import_file(junk)
    assert not (paths.user_targets_dir / "junk.txt").exists()


def test_invalid_target_files_listed_with_reason(paths, bundled):
    paths.user_targets_dir.mkdir(parents=True)
    (paths.user_targets_dir / "broken.txt").write_text("nothing numeric")
    (paths.user_targets_dir / "notes.md").write_text("ignored")
    lib = TargetLibrary(paths, bundled_dir=bundled)
    targets, invalid = lib.scan()
    assert [p.path.name for p in invalid] == ["broken.txt"] and invalid[0].reason
    assert "broken.txt" not in {t.filename for t in targets}


def test_one_khz_normalisation_unaffected(paths, bundled):
    lib = TargetLibrary(paths, bundled_dir=bundled)
    lib.scan()
    diamond = lib.default_for(None)
    f, d = load_frequency_response(diamond.path)
    np.testing.assert_array_equal(diamond.freqs, f)
    np.testing.assert_array_equal(diamond.db, d)  # raw levels kept; matching normalises
    model = Measurements.bundled().models["L"]
    a = match_target(model, 7700, diamond.freqs, diamond.db, "a")
    b = match_target(model, 7700, diamond.freqs, diamond.db + 10, "b")
    np.testing.assert_allclose(a.correction_db, b.correction_db)
