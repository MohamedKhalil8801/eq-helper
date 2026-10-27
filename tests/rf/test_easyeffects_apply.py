"""Apply to EasyEffects (Linux): a Convolver preset + impulse response where every EasyEffects version looks."""

import json
import subprocess

import numpy as np
import pytest
import soundfile as sf

from resonancefit.easyeffects_apply import FLATPAK_ID, EasyEffectsApplier, preset_name
from resonancefit.settings import Settings

from .test_apo_apply import _eq


class Runner:
    def __init__(self, running=True, load_ok=True):
        self.running, self.load_ok, self.calls = running, load_ok, []

    def __call__(self, cmd, **_kw):
        self.calls.append(list(cmd))
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 0 if self.running else 1)
        return subprocess.CompletedProcess(cmd, 0 if self.load_ok else 1)


def _applier(tmp_path, which=("easyeffects",), runner=None, env=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    tools = set(which)
    return EasyEffectsApplier("linux", Settings(tmp_path / "s.json"), home=home, env=env or {},
                              which=lambda name: f"/usr/bin/{name}" if name in tools else None,
                              run=runner or Runner())


def test_only_on_linux_with_easyeffects(tmp_path):
    assert not _applier(tmp_path, which=()).available()
    assert _applier(tmp_path).available()
    assert not EasyEffectsApplier("windows", Settings(tmp_path / "w.json"), home=tmp_path).available()


def test_apply_writes_convolver_preset_and_stereo_ir_and_loads_it(tmp_path):
    runner = Runner()
    applier = _applier(tmp_path, runner=runner)
    preset = applier.apply(_eq(), "Truthear Nova", "Left 7700 Hz, Right 7700 Hz")
    root = tmp_path / "home" / ".local" / "share" / "easyeffects"     # EasyEffects 8
    name = preset_name("Truthear Nova")
    assert preset == root / "output" / f"{name}.json"
    data = json.loads(preset.read_text(encoding="utf-8"))["output"]
    assert data["plugins_order"] == ["convolver#0"]
    conv = data["convolver#0"]
    assert conv["kernel-name"] == name and not conv["bypass"] and conv["autogain"] is False
    ir, rate = sf.read(root / "irs" / f"{name}.irs")
    assert rate == 48000 and ir.ndim == 2 and ir.shape[1] == 2 and np.isfinite(ir).all()
    assert ["easyeffects", "-l", name] in runner.calls
    assert applier.is_on() and applier.loaded
    assert not list((root / "irs").glob(".*")) and not list((root / "output").glob(".*"))  # no temp files left


def test_easyeffects_7_and_flatpak_locations_too(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "easyeffects" / "output").mkdir(parents=True)                  # 7.x presets present
    (home / ".var" / "app" / FLATPAK_ID / "config" / "easyeffects" / "output").mkdir(parents=True)
    runner = Runner()
    applier = _applier(tmp_path, which=("easyeffects", "flatpak"), runner=runner)
    applier.apply(_eq(), "Nova", "x")
    name = preset_name("Nova")
    for root in (home / ".local/share/easyeffects", home / ".config/easyeffects",
                 home / ".var/app" / FLATPAK_ID / "data/easyeffects",
                 home / ".var/app" / FLATPAK_ID / "config/easyeffects"):
        assert (root / "output" / f"{name}.json").exists() and (root / "irs" / f"{name}.irs").exists()


def test_xdg_data_home_is_respected(tmp_path):
    applier = _applier(tmp_path, env={"XDG_DATA_HOME": str(tmp_path / "data")})
    path = applier.apply(_eq(), "Nova", "x")
    assert path.parent == tmp_path / "data" / "easyeffects" / "output"


def test_not_running_only_writes_and_says_so(tmp_path):
    runner = Runner(running=False)
    applier = _applier(tmp_path, runner=runner)
    applier.apply(_eq(), "Nova", "x")
    assert not applier.loaded and not applier.is_on()
    assert not any("-l" in call for call in runner.calls)   # "-l" on a stopped EasyEffects would load and quit


def test_turn_off_bypasses_our_convolver(tmp_path):
    applier = _applier(tmp_path)
    path = applier.apply(_eq(), "Nova", "x")
    applier.turn_off()
    assert json.loads(path.read_text(encoding="utf-8"))["output"]["convolver#0"]["bypass"] is True
    assert not applier.is_on()


def test_unwritable_folder_explained(tmp_path):
    from resonancefit.easyeffects_apply import EasyEffectsProblem
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "easyeffects").write_text("not a folder")
    with pytest.raises(EasyEffectsProblem):
        _applier(tmp_path).apply(_eq(), "Nova", "x")


# ── UI ──────────────────────────────────────────────────────────────────────

def test_linux_window_applies_to_easyeffects(qtbot, tmp_path):
    from eq_helper.calibration.rescal import write_rescal
    from resonancefit.context import AppContext
    from resonancefit.paths import AppPaths
    from resonancefit.ui.main_window import MainWindow

    from .conftest import make_calibration

    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration())
    ctx = AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app"))
    applier = EasyEffectsApplier("linux", ctx.settings, home=tmp_path / "home", env={},
                                 which=lambda n: "/usr/bin/easyeffects" if n == "easyeffects" else None,
                                 run=Runner())
    (tmp_path / "home").mkdir()
    w = MainWindow(ctx, start_audio=False, apo=applier)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None and w.tuning.channel_eq is not None, timeout=20000)
    assert w.listen.apply_button.isVisibleTo(w) and w.listen.apply_button.text() == "Apply to EasyEffects"
    assert not w.settings_page.apo_change.isVisibleTo(w)        # no output to choose
    w.listen.apply_button.click()
    entry = w.history.entries()[0]
    assert entry.reason == "apply" and entry.app == "easyeffects"
    assert "Applied in EasyEffects" in w.listen.status.text()
    assert "twice" in w.tuning.other_eq.text() and "EasyEffects" in w.tuning.other_eq_off.text()
    w.tuning.other_eq_off.click()
    assert not applier.is_on()
