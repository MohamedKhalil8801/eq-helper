"""Tests for ApoBridge: two-way live sync with Equalizer APO.

The watcher and timers are driven directly (not via real filesystem events or
the event loop) so the self-write-vs-external decision is tested deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from eq_helper.apo import writer
from eq_helper.ui.apo_bridge import ApoBridge


def test_self_write_is_ignored(qtbot, tmp_path: Path) -> None:
    cfg = tmp_path / "config.txt"
    cfg.write_text("Device: X\n", encoding="utf-8")
    eqf = tmp_path / "eq.txt"
    bridge = ApoBridge(lambda: cfg)
    try:
        emitted = []
        bridge.externalChange.connect(emitted.append)
        writer.atomic_write_with_backup(eqf, "Preamp: 0 dB\n")  # app write -> hash recorded
        bridge.set_watched([str(cfg), str(eqf)])
        bridge._on_file_changed(str(eqf))  # watcher echoes our own write
        bridge._flush()
        assert emitted == []
    finally:
        bridge.cleanup()


def test_external_write_emits_change(qtbot, tmp_path: Path) -> None:
    cfg = tmp_path / "config.txt"
    cfg.write_text("Device: X\n", encoding="utf-8")
    eqf = tmp_path / "eq.txt"
    eqf.write_text("Preamp: 0 dB\n", encoding="utf-8")
    bridge = ApoBridge(lambda: cfg)
    try:
        emitted = []
        bridge.externalChange.connect(emitted.append)
        bridge.set_watched([str(cfg), str(eqf)])
        # External edit (not through the app writer) -> hash won't match.
        eqf.write_text("Preamp: -3 dB\nFilter 1: ON PK Fc 100 Hz Gain 2 dB Q 1\n", encoding="utf-8")
        bridge._on_file_changed(str(eqf))
        bridge._flush()
        assert emitted == [str(eqf)]
    finally:
        bridge.cleanup()


def test_crlf_self_write_is_suppressed(qtbot, tmp_path: Path) -> None:
    # CRLF content must hash identically write-time vs read-back (byte hashing),
    # so the app's own CRLF write isn't mistaken for an external change.
    cfg = tmp_path / "config.txt"
    cfg.write_bytes(b"Device: X\r\n")
    eqf = tmp_path / "eq.txt"
    bridge = ApoBridge(lambda: cfg)
    try:
        emitted = []
        bridge.externalChange.connect(emitted.append)
        writer.atomic_write_with_backup(
            eqf, "Preamp: 0 dB\r\nFilter 1: ON PK Fc 1000 Hz Gain 1 dB Q 1\r\n"
        )
        bridge.set_watched([str(cfg), str(eqf)])
        bridge._on_file_changed(str(eqf))
        bridge._flush()
        assert emitted == []
    finally:
        bridge.cleanup()


def test_poke_skipped_when_config_change_pending(qtbot, tmp_path: Path) -> None:
    from eq_helper.ui.apo_bridge import _key

    cfg = tmp_path / "config.txt"
    cfg.write_text("Device: X\n", encoding="utf-8")
    bridge = ApoBridge(lambda: cfg)
    try:
        bridge._pending[_key(cfg)] = str(cfg)  # an external config change is pending
        before = cfg.stat().st_mtime_ns
        bridge._do_poke()
        # Poke deferred: config.txt untouched and its hash not (re)recorded,
        # so the pending external change won't be masked at flush.
        assert cfg.stat().st_mtime_ns == before
        assert _key(cfg) not in bridge._hashes
    finally:
        bridge.cleanup()


def test_noncfg_write_schedules_poke_cfg_write_does_not(qtbot, tmp_path: Path) -> None:
    cfg = tmp_path / "config.txt"
    cfg.write_text("Device: X\nInclude: eq.txt\n", encoding="utf-8")
    eqf = tmp_path / "eq.txt"
    bridge = ApoBridge(lambda: cfg)
    try:
        # Writing a non-config file schedules a poke of config.txt.
        writer.atomic_write_with_backup(eqf, "Preamp: 0 dB\n")
        assert bridge._poke_timer.isActive()
        # The poke actually re-saves config.txt (observer records its hash).
        bridge._do_poke()
        from eq_helper.ui.apo_bridge import _key
        assert _key(cfg) in bridge._hashes

        # Writing config.txt itself must NOT schedule a (redundant) poke.
        bridge._poke_timer.stop()
        writer.atomic_write_with_backup(cfg, "Device: X\nInclude: eq.txt\n")
        assert not bridge._poke_timer.isActive()
    finally:
        bridge.cleanup()
