"""Load/save user settings as JSON under ``%USERPROFILE%\\.eq_helper``."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    "apo_config_dir_override": None,
    "sine_freq_hz": 1000.0,
    "volume": 0.08,
    "channel_mode": "BOTH",  # LEFT / BOTH / RIGHT
    "force_mono": True,
    "sweep_f_start_hz": 20.0,
    "sweep_f_end_hz": 20000.0,
    "sweep_duration_s": 10.0,
    "sweep_loop": True,
    "dual_output": False,
    "device_a_id": None,
    "device_b_id": None,
    "dual_routing": "MIRROR",
    "band_fc_hz": 1000.0,
    "band_bw_hz": 500.0,
    "band_enabled": False,
    "overlay_csv_path": None,
    "overlay2_csv_path": None,
    "eq2_profile_path": None,
    "overlay_normalize_1khz": True,
    "profiles": [],  # legacy flat format (migrated on load)
    "active_profile_path": None,  # legacy (migrated on load)
    "profiles_by_device": {},  # {"device": [{"name", "include_path"}]}
    "active_profile_by_device": {},  # {"device": "include_path"}
    "measurements_by_device": {},  # {"device": "csv_path"}

    "test_songs": [],  # list of {"label": str, "path_or_url": str, "type": "file"|"link"}
    "mic_monitoring": False,
    "resonance_personal_treble_enabled": False,
    "resonance_personal_treble_peaks_hz": {},
    "resonance_measurements_dir": None,
    "resonance_target_path": None,
    "resonance_peaks_hz": {},
    "resonance_export_layout": None,
    "resonance_export_phase": "minimum",
    "resonance_export_format": "pcm24",
    "resonance_export_sample_rates": [44100, 48000],
    "resonance_export_dir": None,
}


def default_settings_path() -> Path:
    """Return ``%USERPROFILE%\\.eq_helper\\settings.json`` (or POSIX ~/.eq_helper)."""
    home = Path(os.path.expanduser("~"))
    return home / ".eq_helper" / "settings.json"


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load settings, merging with defaults. Corrupt files fall back cleanly."""
    target = path or default_settings_path()
    base = copy.deepcopy(DEFAULTS)
    if not target.exists():
        return base
    try:
        with target.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            base.update(data)
    except (OSError, json.JSONDecodeError):
        # Rewrite a fresh defaults file so the next load is clean.
        save_settings(base, target)
    return base


def save_settings(settings: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Atomically save settings to JSON and return the path written."""
    target = path or default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, sort_keys=True)
        os.replace(tmp_path, target)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target
