"""Generate pipeline (Qt-free part of "Generate customer app…")."""

import zipfile
from pathlib import Path

import numpy as np
import pytest

from eq_helper.admin.customer_library import CustomerLibrary, MeasurementSource
from eq_helper.admin.generate import GenerateRequest, generate, load_measurement_sets, preview
from eq_helper.calibration.branding import Branding
from eq_helper.calibration.rescal import Accessory, Customer, Iem, Manifest, read_rescal

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "assets" / "targets"


def write_fits(folder: Path, side_names=("L", "R"), peaks=(6500, 7300, 8100, 8900), per_side_folder=False):
    f = np.unique(np.r_[np.geomspace(20, 20000, 400), 1000])
    written = []
    for side in side_names:
        for i, p in enumerate(peaks):
            db = 75 + 9 * np.exp(-0.5 * (np.log(f / p) / .075) ** 2)
            target = (folder / side) if per_side_folder else folder
            target.mkdir(parents=True, exist_ok=True)
            name = f"fit{i}.txt" if per_side_folder else f"{side} fit{i}.txt"
            path = target / name
            path.write_text("\n".join(f"{x:.4f} {y:.4f}" for x, y in zip(f, db)))
            written.append(path)
    return written


class FakeCache:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.win = folder / "ResonanceFit-1.0.0-windows.zip"
        with zipfile.ZipFile(self.win, "w") as z:
            z.writestr("ResonanceFit/ResonanceFit.exe", b"MZ")
        self.lin = folder / "ResonanceFit-1.0.0-linux.AppImage"
        self.lin.write_bytes(b"ELF")
        self.calls = []

    def resolve(self, system, check_online=True):
        self.calls.append(system)
        return {"windows": self.win, "linux": self.lin}[system]


def _manifest(name="Ada"):
    return Manifest(customer=Customer(name), iem=Iem("Truthear", "Nova"),
                    accessories=[Accessory("dac", "Apple dongle", "operator")], coupler="IEC-711 clone")


def test_preview_counts_fits_and_peak_ranges_and_warns_below_three(tmp_path):
    write_fits(tmp_path / "m")
    info = preview(MeasurementSource.from_folder(tmp_path / "m"))
    assert info.sides == {"L": 4, "R": 4}
    lo, hi = info.peak_range["L"]
    assert 6300 < lo < 6700 and 8700 < hi < 9100
    assert not info.warnings_below_three()
    write_fits(tmp_path / "few", peaks=(7000, 8000))
    assert preview(MeasurementSource.from_folder(tmp_path / "few")).warnings_below_three()


def test_separate_left_and_right_folders(tmp_path):
    write_fits(tmp_path / "m", per_side_folder=True)
    # files carry no L/R label; the folder chosen for each side decides
    for p in (tmp_path / "m" / "L").iterdir():
        p.rename(p.with_name("a" + p.name))
    fits, _ = load_measurement_sets(MeasurementSource.from_sides(tmp_path / "m" / "L", tmp_path / "m" / "R"))
    assert {f.side for f in fits} == {"L", "R"} and len(fits) == 8


def test_generate_writes_calibration_sheet_packages_and_record(qapp, tmp_path):
    write_fits(tmp_path / "m")
    library = CustomerLibrary(tmp_path / "lib")
    cache = FakeCache(tmp_path / "builds")
    targets = [TARGETS / "PEQdB Diamond.txt", TARGETS / "Harman IE 2019.txt"]
    request = GenerateRequest(_manifest(), MeasurementSource.from_folder(tmp_path / "m"), targets,
                              ["windows", "linux"], tmp_path / "out")
    steps = []
    result = generate(request, library, cache, Branding(), progress=steps.append)
    names = sorted(p.name for p in result.outputs)
    assert any(n.endswith("(Windows).zip") for n in names) and any(n.endswith("(Linux).tar.gz") for n in names)
    assert any(n.endswith(".rescal") for n in names) and any(n.endswith(".pdf") for n in names)
    cal = read_rescal(next(p for p in result.outputs if p.suffix == ".rescal"))
    assert len(cal.fits) == 8 and cal.manifest.default_target == "PEQdB Diamond.txt"
    record = library.get(result.record.record_id)
    assert record.systems == ["windows", "linux"] and record.manifest == cal.manifest
    assert [Path(t).name for t in record.targets] == ["PEQdB Diamond.txt", "Harman IE 2019.txt"]
    assert library.operator_accessories("dac") == ["Apple dongle"]
    assert cache.calls == ["windows", "linux"]
    assert steps and steps[-1].startswith("Done")


def test_calibration_file_only_needs_no_build(qapp, tmp_path):
    write_fits(tmp_path / "m")
    cache = FakeCache(tmp_path / "builds")
    request = GenerateRequest(_manifest(), MeasurementSource.from_folder(tmp_path / "m"),
                              [TARGETS / "PEQdB Diamond.txt"], [], tmp_path / "out")
    result = generate(request, CustomerLibrary(tmp_path / "lib"), cache, Branding())
    assert sorted(p.suffix for p in result.outputs) == [".pdf", ".rescal"]
    assert cache.calls == []


def test_default_target_must_be_included(tmp_path):
    write_fits(tmp_path / "m")
    m = _manifest()
    m.default_target = "Missing.txt"
    request = GenerateRequest(m, MeasurementSource.from_folder(tmp_path / "m"), [TARGETS / "Flat Line.txt"], [],
                              tmp_path / "out")
    with pytest.raises(ValueError, match="default target"):
        generate(request, CustomerLibrary(tmp_path / "lib"), FakeCache(tmp_path / "b"), Branding())


def test_unusable_measurements_explained(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="No usable measurements"):
        preview(MeasurementSource.from_folder(tmp_path / "empty"))


def test_bundled_customer_targets_are_coupler_targets():
    """Every target offered to customers carries the IEC-711 ear gain (≈ +8–12 dB near 3 kHz);
    flat or compensated curves would make the EQ boost the upper mids by ~10 dB."""
    from eq_helper.admin.generate import DIAGNOSTIC_TARGETS
    from eq_helper.dsp.overlay import load_frequency_response
    for path in sorted(TARGETS.glob("*.txt")):
        if path.name in DIAGNOSTIC_TARGETS:
            continue
        f, db = (np.asarray(x, float) for x in load_frequency_response(path))
        rise = np.interp(np.log(3000), np.log(f), db) - np.interp(np.log(1000), np.log(f), db)
        assert 6 <= rise <= 14, f"{path.name}: {rise:.1f} dB at 3 kHz vs 1 kHz"
