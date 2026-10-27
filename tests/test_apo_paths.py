"""Finding Equalizer APO's config folder."""

import pytest

from eq_helper.apo import paths
from eq_helper.apo.errors import ApoNotFoundError


def test_override_wins(tmp_path):
    assert paths.resolve_apo_config_dir(tmp_path, registry=lambda: "/nope") == tmp_path


def test_custom_install_found_through_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_APO_CONFIG_DIR", tmp_path / "missing")
    monkeypatch.setattr(paths, "ALT_APO_CONFIG_DIRS", ())
    custom = tmp_path / "D-Audio" / "EqualizerAPO" / "config"
    custom.mkdir(parents=True)
    assert paths.resolve_apo_config_dir(registry=lambda: str(custom)) == custom


def test_stale_registry_falls_back_and_nothing_found_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_APO_CONFIG_DIR", tmp_path / "missing")
    monkeypatch.setattr(paths, "ALT_APO_CONFIG_DIRS", ())
    with pytest.raises(ApoNotFoundError):
        paths.resolve_apo_config_dir(registry=lambda: str(tmp_path / "gone"))
    monkeypatch.setattr(paths, "DEFAULT_APO_CONFIG_DIR", tmp_path)
    assert paths.resolve_apo_config_dir(registry=lambda: None) == tmp_path


def test_registry_reader_is_harmless_off_windows(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert paths.registry_config_path() is None
