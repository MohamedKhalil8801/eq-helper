"""Tests for PresetLibrary (device -> preset -> stacked EQ files) over a temp
Equalizer APO config dir. No real APO install required."""

from __future__ import annotations

from pathlib import Path

import pytest

from eq_helper.apo.presets import EqFile, Preset, PresetLibrary

DEV = "Device: HD600 {0123abcd-0000-0000-0000-000000000000}"
SPK = "Device: Speakers {0123abcd-1111-1111-1111-111111111111}"


def _lib(tmp_path: Path, config_text: str) -> PresetLibrary:
    (tmp_path / "config.txt").write_text(config_text, encoding="utf-8")
    return PresetLibrary(tmp_path)


def _config(tmp_path: Path) -> str:
    return (tmp_path / "config.txt").read_text(encoding="utf-8")


class TestReads:
    def test_list_devices(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"Include: g.txt\n{DEV}\nInclude: A.txt\n{SPK}\nInclude: S.txt\n")
        assert lib.list_devices() == ["HD600", "Speakers"]

    def test_list_devices_includes_device_with_no_presets(self, tmp_path: Path) -> None:
        # A device with a Device: line but zero includes must still appear, so
        # the user can create its first preset.
        lib = _lib(tmp_path, f"{DEV}\n{SPK}\nInclude: S.txt\n")
        assert lib.list_devices() == ["HD600", "Speakers"]

    def test_list_presets_and_active(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n# Include: B.txt\n")
        ps = lib.list_presets("HD600")
        assert [p.name for p in ps] == ["A", "B"]
        assert [p.is_active for p in ps] == [True, False]
        assert lib.active_preset("HD600").name == "A"

    def test_no_active_preset_returns_none(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\n# Include: A.txt\n")
        assert lib.active_preset("HD600") is None


class TestActivate:
    def test_activate_does_not_touch_same_short_name_other_device(self, tmp_path: Path) -> None:
        # Two distinct devices share the friendly name "Headset" (different
        # GUIDs). Activating a preset under one must not corrupt the other.
        a = "Device: Headset {aaaa0000-0000-0000-0000-000000000000}"
        b = "Device: Headset {bbbb0000-0000-0000-0000-000000000000}"
        lib = _lib(tmp_path, f"{a}\nInclude: a1.txt\n# Include: a2.txt\n{b}\nInclude: b1.txt\n")
        presets = lib.list_presets("Headset")
        a2 = next(p for p in presets if p.name == "a2")
        lib.activate(a2)
        lines = _config(tmp_path).splitlines()
        assert lines[1] == "# Include: a1.txt"   # AAAA a1 -> off
        assert lines[2] == "Include: a2.txt"      # AAAA a2 -> on
        assert lines[4] == "Include: b1.txt"      # BBBB device untouched

    def test_radio_is_device_scoped(self, tmp_path: Path) -> None:
        # A.txt appears under BOTH devices; activating B in HD600 must not touch
        # Speakers' identical A.txt or comment the wrong scope.
        lib = _lib(
            tmp_path,
            f"{DEV}\nInclude: A.txt\n# Include: B.txt\n{SPK}\nInclude: A.txt\n",
        )
        by_name = {p.name: p for p in lib.list_presets("HD600")}
        lib.activate(by_name["B"])
        lines = _config(tmp_path).splitlines()
        assert lines[1] == "# Include: A.txt"   # HD600 A -> off
        assert lines[2] == "Include: B.txt"       # HD600 B -> on
        assert lines[4] == "Include: A.txt"       # Speakers A untouched


class TestPresetCrud:
    def test_create_preset(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        p = lib.create_preset("HD600", "Bass Boost")
        assert Path(p.path).exists()
        assert p.path.replace("\\", "/").endswith("HD600/presets/Bass Boost.txt")
        assert p.is_active is False
        cfg = _config(tmp_path)
        assert "Bass Boost.txt" in cfg and "# Include:" in cfg
        assert any(pr.name == "Bass Boost" for pr in lib.list_presets("HD600"))

    def test_create_preset_unique_names(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        lib.create_preset("HD600", "Bass")
        p2 = lib.create_preset("HD600", "Bass")
        assert p2.path.endswith("Bass (2).txt")

    def test_add_preset_from_file_references_in_place(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        ext = tmp_path / "ext_profile.txt"
        ext.write_text("# my profile\nInclude: x.txt\n", encoding="utf-8")
        p = lib.add_preset_from_file("HD600", ext)
        assert Path(p.path) == ext  # not copied
        assert any(pr.name == "ext_profile" for pr in lib.list_presets("HD600"))

    def test_remove_managed_preset_deletes_file(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        p = lib.create_preset("HD600", "P")
        assert Path(p.path).exists()
        lib.remove_preset(p)
        assert not Path(p.path).exists()
        assert "P.txt" not in _config(tmp_path)

    def test_create_preset_rolls_back_orphan_on_config_failure(self, tmp_path: Path) -> None:
        # No config.txt -> the include edit raises; the just-written preset file
        # must be rolled back (no orphan left behind).
        from eq_helper.apo.errors import ApoWriteError

        lib = PresetLibrary(tmp_path)  # tmp_path has no config.txt
        with pytest.raises(ApoWriteError):
            lib.create_preset("HD600", "P")
        root = tmp_path / "eq-helper"
        leftovers = list(root.rglob("*.txt")) if root.exists() else []
        assert leftovers == []

    def test_remove_external_preset_keeps_file(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        ext = tmp_path / "ext.txt"
        ext.write_text("Preamp: 0 dB\n", encoding="utf-8")
        p = lib.add_preset_from_file("HD600", ext)
        lib.remove_preset(p)
        assert ext.exists()  # external file never deleted

    def test_rename_managed_preset(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        p = lib.create_preset("HD600", "Old")
        p2 = lib.rename_preset(p, "New")
        assert p2.name == "New" and Path(p2.path).exists()
        assert not Path(p.path).exists()
        names = [pr.name for pr in lib.list_presets("HD600")]
        assert "New" in names and "Old" not in names

    def test_rename_external_preset_raises(self, tmp_path: Path) -> None:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        ext = tmp_path / "ext.txt"
        ext.write_text("Preamp: 0 dB\n", encoding="utf-8")
        p = lib.add_preset_from_file("HD600", ext)
        with pytest.raises(ValueError):
            lib.rename_preset(p, "X")


class TestEqStack:
    def _preset(self, tmp_path: Path) -> tuple[PresetLibrary, Preset]:
        lib = _lib(tmp_path, f"{DEV}\nInclude: A.txt\n")
        return lib, lib.create_preset("HD600", "P")

    def test_create_and_list_eq_files(self, tmp_path: Path) -> None:
        lib, preset = self._preset(tmp_path)
        eq = lib.create_eq_file(preset, "bass")
        assert Path(eq.path).exists()
        files = lib.list_eq_files(preset)
        assert [f.name for f in files] == ["bass"]
        assert files[0].enabled is True

    def test_add_eq_file_from_file_copies_original(self, tmp_path: Path) -> None:
        lib, preset = self._preset(tmp_path)
        ext = tmp_path / "autoeq.txt"
        ext.write_text("Preamp: -6 dB\nFilter 1: ON PK Fc 100 Hz Gain 3 dB Q 1\n", encoding="utf-8")
        eq = lib.add_eq_file_from_file(preset, ext)
        assert Path(eq.path) != ext               # copied, not referenced in place
        assert "eq-helper" in eq.path.replace("\\", "/")
        assert "Filter 1" in Path(eq.path).read_text(encoding="utf-8")
        assert any(f.name == "autoeq" for f in lib.list_eq_files(preset))

    def test_enable_disable_reorder_remove(self, tmp_path: Path) -> None:
        lib, preset = self._preset(tmp_path)
        e1 = lib.create_eq_file(preset, "one")
        e2 = lib.create_eq_file(preset, "two")

        lib.set_eq_enabled(preset, e1.include_path, False)
        files = {f.name: f for f in lib.list_eq_files(preset)}
        assert files["one"].enabled is False and files["two"].enabled is True

        lib.reorder_eq(preset, [e2.include_path, e1.include_path])
        assert [f.name for f in lib.list_eq_files(preset)] == ["two", "one"]

        lib.remove_eq(preset, e1.include_path)
        assert [f.name for f in lib.list_eq_files(preset)] == ["two"]
