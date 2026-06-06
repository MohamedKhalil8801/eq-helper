"""Tests for the APO config.txt Include-line scanner."""

from __future__ import annotations

from pathlib import Path

from eq_helper.apo.scanner import group_by_device, scan_config_includes

# A realistic config.txt (trimmed from the user's real file).
SAMPLE_CONFIG = """\
Device: Headset USB-C to 3.5mm Headphone Jack Adapter
Preamp: -1.9 dB
# Include: SAMSUNG EO-IA500 (Spinfit W1) Filters.txt
Include: SAMSUNG EO-IA500 (Spinfit W1) diamond b target.txt
Channel: L
Include: Samsung new R side.txt
# Include: D:\\Projects\\Truthear-Nova\\PEQdB-Your-EQ-Target.txt
# Include: D:\\Projects\\Truthear-Nova\\Truthear Nova main.txt
Device: Speakers DAWN PRO2
Preamp: -16.3 dB
Include: D:\\Projects\\Sennheiser-HD58X\\HD58X-flat-neutral-target-1-2026.txt
# Include: D:\\Projects\\Sennheiser-HD58X\\HD58X-in-room-target-10.2025.txt
"""


def _write_config(tmp_path: Path, text: str = SAMPLE_CONFIG) -> Path:
    config_path = tmp_path / "config.txt"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def test_scan_finds_all_includes(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    assert len(entries) == 7


def test_scan_active_vs_commented(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    active = [e for e in entries if e.is_active]
    commented = [e for e in entries if not e.is_active]
    assert len(active) == 3
    assert len(commented) == 4


def test_scan_extracts_names(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    names = [e.name for e in entries]
    assert "SAMSUNG EO-IA500 (Spinfit W1) Filters" in names
    assert "SAMSUNG EO-IA500 (Spinfit W1) diamond b target" in names
    assert "HD58X-flat-neutral-target-1-2026" in names


def test_scan_resolves_relative_paths(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    # The relative include "Samsung new R side.txt" should resolve against tmp_path.
    rel_entry = next(e for e in entries if e.name == "Samsung new R side")
    assert rel_entry.resolved_path == tmp_path / "Samsung new R side.txt"


def test_scan_preserves_absolute_paths(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    abs_entry = next(e for e in entries if "HD58X-flat-neutral-target-1-2026" in e.name)
    assert abs_entry.resolved_path.is_absolute()
    assert str(abs_entry.resolved_path).startswith("D:")


def test_scan_records_line_numbers(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    # Each entry should have a distinct line number.
    line_numbers = [e.line_number for e in entries]
    assert len(set(line_numbers)) == len(line_numbers)
    # First include is on line 2 (0-based).
    assert entries[0].line_number == 2


def test_scan_preserves_raw_include_path(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    # Check that include_path is the raw string from config.txt.
    first = entries[0]
    assert first.include_path == "SAMSUNG EO-IA500 (Spinfit W1) Filters.txt"


def test_scan_empty_config(tmp_path: Path) -> None:
    config = _write_config(tmp_path, "Preamp: 0 dB\n")
    entries = scan_config_includes(config)
    assert entries == []


def test_scan_missing_config(tmp_path: Path) -> None:
    entries = scan_config_includes(tmp_path / "nonexistent.txt")
    assert entries == []


# ---------------------------------------------------------- device grouping


def test_scan_assigns_device_to_includes(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    # All entries in the sample config are under "Headset USB-C..." or "Speakers DAWN PRO2".
    devices = {e.device for e in entries}
    assert "Headset USB-C to 3.5mm Headphone Jack Adapter" in devices
    assert "Speakers DAWN PRO2" in devices


def test_group_by_device_separates_correctly(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    entries = scan_config_includes(config)
    groups = group_by_device(entries)
    assert len(groups) >= 2
    headset_key = [k for k in groups if "Headset" in k][0]
    speakers_key = [k for k in groups if "DAWN" in k][0]
    # Headset device has Samsung-related includes.
    headset_names = [e.name for e in groups[headset_key]]
    assert any("SAMSUNG" in n or "Samsung" in n for n in headset_names)
    # Speakers device has HD58X-related includes.
    speaker_names = [e.name for e in groups[speakers_key]]
    assert any("HD58X" in n for n in speaker_names)


MULTI_DEVICE_CONFIG = """\
# Include: global_profile.txt
Device: Headphones DAC1 {aaaa-bbbb}
Include: headphone_eq.txt
# Include: headphone_alt.txt
Device: Speakers DAC2 {cccc-dddd}
Include: speaker_eq.txt
"""


def test_global_scope_before_any_device(tmp_path: Path) -> None:
    config = _write_config(tmp_path, MULTI_DEVICE_CONFIG)
    entries = scan_config_includes(config)
    groups = group_by_device(entries)
    assert "(Global)" in groups
    assert len(groups["(Global)"]) == 1
    assert groups["(Global)"][0].name == "global_profile"


def test_switching_one_device_does_not_affect_another(tmp_path: Path) -> None:
    """Toggling a profile under one device should not touch another device's includes."""
    from eq_helper.apo.writer import toggle_includes
    config = _write_config(tmp_path, MULTI_DEVICE_CONFIG)

    # Manage only headphone profiles.
    managed = {"headphone_eq.txt", "headphone_alt.txt"}
    toggle_includes(config, managed, active_path="headphone_alt.txt")

    text = config.read_text(encoding="utf-8")
    # headphone_alt is now active, headphone_eq is commented.
    assert "Include: headphone_alt.txt" in text.splitlines()
    assert "# Include: headphone_eq.txt" in text.splitlines()
    # speaker_eq is completely untouched (still active).
    assert "Include: speaker_eq.txt" in text.splitlines()
