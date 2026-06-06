"""End-to-end APO integration test.

Simulates the full user workflow: scan config.txt for includes, bookmark two
profiles, activate profile A, switch to B, deactivate all — and verifies that
config.txt is edited correctly at each step.
"""

from __future__ import annotations

from pathlib import Path

from eq_helper.apo.scanner import scan_config_includes
from eq_helper.apo.writer import toggle_includes

CONFIG = """\
Device: Headset
Preamp: -1.9 dB
# Include: SAMSUNG EO-IA500 Filters.txt
Include: SAMSUNG EO-IA500 diamond b target.txt
# Include: D:\\Projects\\Sennheiser-HD58X\\HD58X-flat-target.txt
Include: D:\\Projects\\Sennheiser-HD58X\\HD58X-in-room-target.txt
Include: Unrelated-profile.txt
"""


def _setup(tmp_path: Path) -> Path:
    config = tmp_path / "config.txt"
    config.write_text(CONFIG, encoding="utf-8")
    return config


def test_end_to_end_discover_activate_switch_deactivate(tmp_path: Path) -> None:
    config = _setup(tmp_path)

    # 1. Discover all includes.
    entries = scan_config_includes(config)
    assert len(entries) == 5
    names = [e.name for e in entries]
    assert "SAMSUNG EO-IA500 Filters" in names
    assert "HD58X-flat-target" in names

    # 2. User bookmarks two profiles (by their include_path).
    managed = {
        "SAMSUNG EO-IA500 Filters.txt",
        "SAMSUNG EO-IA500 diamond b target.txt",
    }

    # 3. Activate "SAMSUNG EO-IA500 Filters.txt" (was commented).
    toggle_includes(config, managed, active_path="SAMSUNG EO-IA500 Filters.txt")
    text = config.read_text(encoding="utf-8")
    assert "Include: SAMSUNG EO-IA500 Filters.txt" in text.splitlines()
    assert "# Include: SAMSUNG EO-IA500 diamond b target.txt" in text.splitlines()
    # Non-managed lines unchanged.
    assert "Include: D:\\Projects\\Sennheiser-HD58X\\HD58X-in-room-target.txt" in text
    assert "Include: Unrelated-profile.txt" in text

    # 4. Switch to "SAMSUNG EO-IA500 diamond b target.txt".
    toggle_includes(
        config, managed,
        active_path="SAMSUNG EO-IA500 diamond b target.txt",
    )
    text = config.read_text(encoding="utf-8")
    assert "# Include: SAMSUNG EO-IA500 Filters.txt" in text.splitlines()
    assert "Include: SAMSUNG EO-IA500 diamond b target.txt" in text.splitlines()

    # 5. Deactivate all managed profiles.
    toggle_includes(config, managed, active_path=None)
    text = config.read_text(encoding="utf-8")
    assert "# Include: SAMSUNG EO-IA500 Filters.txt" in text.splitlines()
    assert "# Include: SAMSUNG EO-IA500 diamond b target.txt" in text.splitlines()
    # Non-managed still active.
    assert "Include: D:\\Projects\\Sennheiser-HD58X\\HD58X-in-room-target.txt" in text
    assert "Include: Unrelated-profile.txt" in text


def test_backup_preserves_original_state(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    original = config.read_bytes()

    managed = {"SAMSUNG EO-IA500 Filters.txt"}
    toggle_includes(config, managed, active_path="SAMSUNG EO-IA500 Filters.txt")

    backup = config.with_suffix(".txt.eqhelper.bak")
    assert backup.exists()
    assert backup.read_bytes() == original


def test_rescan_after_toggle_reflects_new_state(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    managed = {"SAMSUNG EO-IA500 Filters.txt", "SAMSUNG EO-IA500 diamond b target.txt"}

    toggle_includes(config, managed, active_path="SAMSUNG EO-IA500 Filters.txt")

    # Rescan should now show the toggled state.
    entries = scan_config_includes(config)
    filters_entry = next(e for e in entries if e.name == "SAMSUNG EO-IA500 Filters")
    diamond_entry = next(
        e for e in entries if e.name == "SAMSUNG EO-IA500 diamond b target"
    )
    assert filters_entry.is_active is True
    assert diamond_entry.is_active is False
