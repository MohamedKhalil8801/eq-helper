"""Best-effort, read-only detection of installed EQ apps (desktop only)."""

from pathlib import Path

import pytest

from eq_helper.exports import detection
from eq_helper.exports.catalog import load_catalog
from eq_helper.exports.detection import detect_installed


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_windows_paths_registry_and_apo(catalog, tmp_path):
    pf = tmp_path / "Program Files"
    (pf / "foobar2000").mkdir(parents=True)
    (pf / "foobar2000" / "foobar2000.exe").write_text("")
    apo = tmp_path / "apo-config"
    apo.mkdir()
    found = detect_installed(catalog, "windows",
                             env={"ProgramFiles": str(pf), "LOCALAPPDATA": str(tmp_path / "local")},
                             registry_names=lambda: ["Peace Equalizer 1.6", "Something else"],
                             apo_override=apo)
    assert found == {"foobar2000", "peace", "equalizer-apo"}


def test_apo_missing_means_not_detected(catalog, tmp_path):
    found = detect_installed(catalog, "windows", env={}, registry_names=lambda: [],
                             apo_override=tmp_path / "nope")
    assert "equalizer-apo" not in found


def test_macos_bundles(catalog, tmp_path):
    apps = tmp_path / "Applications"
    (apps / "SoundSource.app").mkdir(parents=True)
    found = detect_installed(catalog, "macos", app_dirs=[apps, tmp_path / "home" / "Applications"])
    assert found == {"soundsource"}


def test_linux_commands_and_flatpak(catalog, tmp_path):
    flat = tmp_path / "flatpak" / "app" / "com.github.wwmm.easyeffects"
    flat.mkdir(parents=True)
    found = detect_installed(catalog, "linux", which=lambda cmd: "/usr/bin/camilladsp" if cmd == "camilladsp" else None,
                             flatpak_dirs=[tmp_path / "flatpak" / "app"])
    assert found == {"camilladsp", "easyeffects"}


def test_never_raises_and_phones_are_ignored(catalog, monkeypatch):
    def boom(*a, **k):
        raise OSError("denied")
    found = detect_installed(catalog, "windows", env={"ProgramFiles": "C:/x"}, registry_names=boom,
                             exists=boom, apo_override=None, apo_resolver=boom)
    assert found == set()
    assert detect_installed(catalog, "android") == set()


def test_deadline_stops_probing(catalog, tmp_path, monkeypatch):
    clock = iter([0.0] + [10.0] * 100)
    monkeypatch.setattr(detection.time, "monotonic", lambda: next(clock))
    calls = []
    detect_installed(catalog, "linux", which=lambda c: calls.append(c), flatpak_dirs=[], timeout=1.0)
    assert calls == []


def test_registry_reader_is_safe_off_windows():
    assert isinstance(detection.read_uninstall_names(), list)
