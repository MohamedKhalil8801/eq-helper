"""Apply tunings to Equalizer APO (Windows) — over a temp fake APO config dir."""

from pathlib import Path

import pytest

from eq_helper.apo.errors import ApoWriteError
from eq_helper.apo.presets import PresetLibrary
from eq_helper.calibration.rescal import write_rescal
from resonancefit.apo_apply import ApoApplier, ApoUnwritable, entry_name
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.settings import Settings
from resonancefit.ui.main_window import MainWindow

from .conftest import make_calibration

HP = "Device: Headphones DAWN {0123abcd-0000-0000-0000-000000000000}"
SPK = "Device: Speakers {0123abcd-1111-1111-1111-111111111111}"


def _apo(tmp_path, text=None):
    folder = tmp_path / "apo"
    folder.mkdir()
    (folder / "config.txt").write_text(text or f"Preamp: 0 dB\n{HP}\nInclude: mine.txt\n{SPK}\nInclude: spk.txt\n",
                                       encoding="utf-8")
    return folder


def _eq():
    from eq_helper.calibration.tuning import TuningSession
    import numpy as np
    s = TuningSession(make_calibration().measurements())
    f = np.geomspace(20, 20000, 200)
    s.set_target(f, np.zeros_like(f), "Flat")
    return s.channel_eq()


def test_hidden_off_windows_or_without_apo(tmp_path):
    assert not ApoApplier("linux", Settings(tmp_path / "s.json"), _apo(tmp_path)).available()
    assert not ApoApplier("windows", Settings(tmp_path / "s.json"), tmp_path / "missing").available()
    assert ApoApplier("windows", Settings(tmp_path / "s2.json"), tmp_path / "apo").available()


def test_apply_creates_one_managed_entry_and_swaps_contents(tmp_path):
    apo = _apo(tmp_path)
    settings = Settings(tmp_path / "s.json")
    applier = ApoApplier("windows", settings, apo)
    assert applier.devices() == ["Headphones DAWN", "Speakers"]
    applier.set_device("Headphones DAWN")
    eq = _eq()
    first = applier.apply(eq, "Truthear Nova", "Left 7700 Hz, Right 7700 Hz")
    lib = PresetLibrary(apo)
    presets = lib.list_presets("Headphones DAWN")
    names = [p.name for p in presets]
    assert names.count(entry_name("Truthear Nova")) == 1 and "mine" in names
    active = lib.active_preset("Headphones DAWN")
    assert active.name == entry_name("Truthear Nova")
    text = first.read_text(encoding="utf-8")
    assert "Channel: L" in text and "GraphicEQ:" in text and "Left 7700 Hz" in text
    # other device untouched
    assert lib.active_preset("Speakers").name == "spk"
    # apply again: same files, new contents, no duplicate entry
    second = applier.apply(eq, "Truthear Nova", "Left 8000 Hz, Right 8000 Hz")
    assert second == first
    assert "Left 8000 Hz" in second.read_text(encoding="utf-8")
    assert [p.name for p in lib.list_presets("Headphones DAWN")].count(entry_name("Truthear Nova")) == 1
    # device remembered
    assert ApoApplier("windows", Settings(tmp_path / "s.json"), apo).device == "Headphones DAWN"


def test_turn_off_disables_managed_entry_only(tmp_path):
    apo = _apo(tmp_path)
    applier = ApoApplier("windows", Settings(tmp_path / "s.json"), apo)
    applier.set_device("Headphones DAWN")
    applier.apply(_eq(), "Nova", "x")
    assert applier.is_on()
    applier.turn_off()
    assert not applier.is_on()
    assert PresetLibrary(apo).active_preset("Headphones DAWN") is None


def test_no_device_configured_explains(tmp_path):
    apo = _apo(tmp_path, "Preamp: 0 dB\n")
    applier = ApoApplier("windows", Settings(tmp_path / "s.json"), apo)
    assert applier.devices() == []
    assert "Configurator" in applier.no_devices_help()


def test_unwritable_config_raises_explained_fix(tmp_path, monkeypatch):
    apo = _apo(tmp_path)
    applier = ApoApplier("windows", Settings(tmp_path / "s.json"), apo)
    applier.set_device("Headphones DAWN")
    import resonancefit.apo_apply as module

    def deny(*_a, **_k):
        raise ApoWriteError("Access is denied")
    monkeypatch.setattr(module, "atomic_write_with_backup", deny)
    with pytest.raises(ApoUnwritable) as info:
        applier.apply(_eq(), "Nova", "x")
    assert str(apo) in info.value.fix and "permission" in info.value.fix.lower()


# ── UI ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def win(qtbot, tmp_path):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration())
    ctx = AppContext.create(AppPaths("windows", tmp_path / "data", tmp_path / "app"))
    applier = ApoApplier("windows", ctx.settings, _apo(tmp_path))
    w = MainWindow(ctx, start_audio=False, apo=applier)
    w.choose_apo_device = lambda devices: devices[0]
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None and w.tuning.channel_eq is not None, timeout=20000)
    return w


def test_apply_button_applies_and_records_history(win):
    assert win.listen.apply_button.isVisibleTo(win)
    win.listen.apply_button.click()
    entry = win.history.entries()[0]
    assert entry.reason == "apply" and entry.app == "equalizer-apo"
    assert win.apo.is_on()
    assert "Headphones DAWN" in win.listen.status.text()


def test_switch_between_saved_tunings_applies_them(win, qtbot):
    t = win.tuning
    t.set_primary_hz(7200)
    t.regenerate()
    win.listen.save_button.click()
    t.set_primary_hz(8300)
    t.regenerate()
    win.listen.save_button.click()
    combo = win.listen.switch_combo
    assert combo.isVisibleTo(win) and combo.count() >= 2
    older = next(i for i in range(combo.count()) if "7200" in combo.itemText(i))
    combo.setCurrentIndex(older)
    combo.activated.emit(older)
    assert t.session.primary[t.side] == 7200
    eq_file = Path(win.apo.eq_path)
    assert "7200" in eq_file.read_text(encoding="utf-8")


def test_unwritable_apo_shows_explanation(win, monkeypatch):
    shown = []
    monkeypatch.setattr(win, "show_apo_problem", lambda problem: shown.append(problem))

    def fail(*_a, **_k):
        raise ApoUnwritable("denied", "Give Users permission")
    monkeypatch.setattr(win.apo, "apply", fail)
    win.listen.apply_button.click()
    assert shown and shown[0].fix == "Give Users permission"
    assert win.history.entries() == []
