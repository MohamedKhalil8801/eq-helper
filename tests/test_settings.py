"""Tests for JSON-backed settings persistence."""

from __future__ import annotations

from pathlib import Path

from eq_helper.storage.settings import DEFAULTS, load_settings, save_settings


def test_load_defaults_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "nope.json"
    s = load_settings(target)
    assert s == DEFAULTS
    # Calling load with a non-existent file must NOT create it
    assert not target.exists()


def test_round_trip_preserves_fields(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    settings = dict(DEFAULTS)
    settings["sine_freq_hz"] = 432.5
    settings["volume"] = 0.25
    settings["sweep_duration_s"] = 20.0
    settings["channel_mode"] = "LEFT"
    settings["profiles"] = [{"name": "HD600", "path": "C:/x/HD600.txt"}]
    save_settings(settings, target)
    loaded = load_settings(target)
    for k, v in settings.items():
        assert loaded[k] == v, k


def test_corrupt_json_falls_back_to_defaults(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{not: valid json}", encoding="utf-8")
    s = load_settings(target)
    # load_settings re-writes a clean file on corruption.
    assert s == DEFAULTS
    # The rewritten file is now valid JSON.
    second = load_settings(target)
    assert second == DEFAULTS


def test_partial_settings_fill_in_defaults(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"sine_freq_hz": 250.0}', encoding="utf-8")
    s = load_settings(target)
    assert s["sine_freq_hz"] == 250.0
    # Defaults preserved for absent keys.
    assert s["sweep_duration_s"] == DEFAULTS["sweep_duration_s"]
    assert s["volume"] == DEFAULTS["volume"]
