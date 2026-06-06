"""Tests for the APO config.txt Include-line toggling."""

from __future__ import annotations

from pathlib import Path

import pytest

from eq_helper.apo import writer as writer_mod
from eq_helper.apo.errors import ApoWriteError
from eq_helper.apo.writer import (
    atomic_write_with_backup,
    edit_include_file,
    poke,
    set_write_observer,
    toggle_includes,
)


def test_write_observer_fires_with_path_and_content(tmp_path: Path) -> None:
    calls = []
    set_write_observer(lambda p, c: calls.append((str(p), c)))
    try:
        f = tmp_path / "x.txt"
        atomic_write_with_backup(f, "hello\n")
    finally:
        set_write_observer(None)
    assert calls and calls[-1][1] == "hello\n"
    assert calls[-1][0] == str(f)


def test_poke_bumps_mtime_without_touching_content(tmp_path: Path) -> None:
    import os

    f = tmp_path / "config.txt"
    raw = b"Device: X\r\nInclude: a.txt\r\n"  # CRLF, like a real Windows config
    f.write_bytes(raw)
    past = f.stat().st_mtime - 100
    os.utime(f, (past, past))  # set mtime into the past so the bump is detectable
    poke(f)
    assert f.read_bytes() == raw  # content (incl. CRLF) untouched -> no corruption
    assert f.stat().st_mtime > past  # mtime bumped -> APO's watcher fires


def test_poke_missing_file_is_noop(tmp_path: Path) -> None:
    poke(tmp_path / "nope.txt")  # must not raise


def test_observer_exception_does_not_break_write(tmp_path: Path) -> None:
    set_write_observer(lambda p, c: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        f = tmp_path / "x.txt"
        atomic_write_with_backup(f, "data\n")  # observer raises, write must succeed
        assert f.read_text(encoding="utf-8") == "data\n"
    finally:
        set_write_observer(None)

SAMPLE_CONFIG = """\
Device: Headset
Preamp: -1.9 dB
# Include: ProfileA.txt
Include: ProfileB.txt
# Include: ProfileC.txt
Include: Unmanaged.txt
"""


def _write_config(tmp_path: Path, text: str = SAMPLE_CONFIG) -> Path:
    config = tmp_path / "config.txt"
    config.write_text(text, encoding="utf-8")
    return config


def test_activate_uncomments_target_and_comments_others(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileA.txt", "ProfileB.txt", "ProfileC.txt"}
    toggle_includes(config, managed, active_path="ProfileA.txt")

    lines = config.read_text(encoding="utf-8").splitlines()
    # ProfileA should now be active (uncommented).
    assert "Include: ProfileA.txt" in lines
    # ProfileB was active but is managed -> now commented.
    assert "# Include: ProfileB.txt" in lines
    # ProfileC stays commented.
    assert "# Include: ProfileC.txt" in lines
    # Unmanaged line is untouched.
    assert "Include: Unmanaged.txt" in lines


def test_deactivate_all_comments_everything_managed(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileA.txt", "ProfileB.txt"}
    toggle_includes(config, managed, active_path=None)

    lines = config.read_text(encoding="utf-8").splitlines()
    assert "# Include: ProfileA.txt" in lines
    assert "# Include: ProfileB.txt" in lines
    # Unmanaged still active.
    assert "Include: Unmanaged.txt" in lines


def test_switch_between_profiles(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileA.txt", "ProfileB.txt", "ProfileC.txt"}

    toggle_includes(config, managed, active_path="ProfileA.txt")
    text1 = config.read_text(encoding="utf-8")
    assert "Include: ProfileA.txt" in text1.splitlines()
    assert "# Include: ProfileB.txt" in text1.splitlines()

    toggle_includes(config, managed, active_path="ProfileC.txt")
    text2 = config.read_text(encoding="utf-8")
    assert "# Include: ProfileA.txt" in text2.splitlines()
    assert "Include: ProfileC.txt" in text2.splitlines()


def test_noop_when_already_correct(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileB.txt"}  # ProfileB is already active
    original = config.read_bytes()
    toggle_includes(config, managed, active_path="ProfileB.txt")
    assert config.read_bytes() == original


def test_unmanaged_lines_are_never_touched(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileA.txt"}
    toggle_includes(config, managed, active_path="ProfileA.txt")

    text = config.read_text(encoding="utf-8")
    # Non-include lines preserved exactly.
    assert "Device: Headset\n" in text
    assert "Preamp: -1.9 dB\n" in text


def test_backup_created_on_first_write(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileA.txt"}
    toggle_includes(config, managed, active_path="ProfileA.txt")

    backup = config.with_suffix(".txt.eqhelper.bak")
    assert backup.exists()
    # Backup should contain the original content.
    assert "# Include: ProfileA.txt" in backup.read_text(encoding="utf-8")


def test_backup_not_overwritten_on_subsequent_writes(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    managed = {"ProfileA.txt", "ProfileB.txt"}

    toggle_includes(config, managed, active_path="ProfileA.txt")
    backup = config.with_suffix(".txt.eqhelper.bak")
    first_backup = backup.read_bytes()

    toggle_includes(config, managed, active_path="ProfileB.txt")
    assert backup.read_bytes() == first_backup


def test_absolute_path_includes(tmp_path: Path) -> None:
    text = (
        "Include: D:\\Projects\\HD58X\\profile.txt\n"
        "# Include: D:\\Projects\\Nova\\profile.txt\n"
    )
    config = _write_config(tmp_path, text)
    managed = {
        "D:\\Projects\\HD58X\\profile.txt",
        "D:\\Projects\\Nova\\profile.txt",
    }
    toggle_includes(
        config, managed, active_path="D:\\Projects\\Nova\\profile.txt"
    )
    lines = config.read_text(encoding="utf-8").splitlines()
    assert "# Include: D:\\Projects\\HD58X\\profile.txt" in lines
    assert "Include: D:\\Projects\\Nova\\profile.txt" in lines


# ---------------------------------------------------- atomic_write_with_backup


def test_atomic_write_with_backup_writes_and_backs_up_once(tmp_path: Path) -> None:
    f = tmp_path / "eq.txt"
    f.write_text("original\n", encoding="utf-8")
    atomic_write_with_backup(f, "new content\n")
    assert f.read_text(encoding="utf-8") == "new content\n"
    backup = f.with_suffix(".txt.eqhelper.bak")
    assert backup.read_text(encoding="utf-8") == "original\n"  # original preserved
    # Second write does not overwrite the one-shot backup.
    atomic_write_with_backup(f, "newer\n")
    assert backup.read_text(encoding="utf-8") == "original\n"


def test_atomic_write_with_backup_creates_new_file_without_backup(tmp_path: Path) -> None:
    f = tmp_path / "new.txt"
    atomic_write_with_backup(f, "hello\n")
    assert f.read_text(encoding="utf-8") == "hello\n"
    assert not f.with_suffix(".txt.eqhelper.bak").exists()


# ------------------------------------------------------------ edit_include_file


def test_edit_include_file_applies_change_and_reports_changed(tmp_path: Path) -> None:
    f = tmp_path / "config.txt"
    f.write_text("Include: a.txt\n", encoding="utf-8")
    changed = edit_include_file(f, lambda t: t + "Include: b.txt\n")
    assert changed is True
    assert "Include: b.txt" in f.read_text(encoding="utf-8")


def test_edit_include_file_noop_does_not_write_or_backup(tmp_path: Path) -> None:
    f = tmp_path / "config.txt"
    f.write_text("Include: a.txt\n", encoding="utf-8")
    before = f.read_bytes()
    changed = edit_include_file(f, lambda t: t)  # identity
    assert changed is False
    assert f.read_bytes() == before
    assert not f.with_suffix(".txt.eqhelper.bak").exists()


def test_edit_include_file_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ApoWriteError):
        edit_include_file(tmp_path / "nope.txt", lambda t: t)
