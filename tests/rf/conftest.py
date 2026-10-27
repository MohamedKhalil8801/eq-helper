"""Shared fixtures for the customer app (ResonanceFit) tests."""

import numpy as np
import pytest

from eq_helper.calibration.rescal import Calibration, Customer, Iem, Manifest, write_rescal
from eq_helper.dsp.resonance import Fit


def make_calibration(customer="Ada", model="Nova", created="2026-10-27", calibration_id=None, shift=0.0):
    f = np.unique(np.r_[np.geomspace(20, 20000, 400), 1000])
    fits = [Fit(s, f"{s}{i}", f, 75 + o + shift + 9 * np.exp(-0.5 * (np.log(f / p) / .075) ** 2))
            for s, o in (("L", 0), ("R", 2)) for i, p in enumerate([6500, 7300, 8100, 8900])]
    manifest = Manifest(customer=Customer(customer), iem=Iem("Truthear", model), created=created)
    if calibration_id:
        manifest.calibration_id = calibration_id
    return Calibration(manifest, fits)


@pytest.fixture
def write_calibration():
    def write(path, **kwargs):
        return write_rescal(path, make_calibration(**kwargs))
    return write


@pytest.fixture(autouse=True)
def _no_modal_first_launch(monkeypatch):
    """The walkthrough, "start quiet" and file-association prompts are modal; answer them in tests."""
    from resonancefit.ui import main_window, walkthrough
    monkeypatch.setattr(walkthrough.Walkthrough, "exec", lambda self: 1)
    monkeypatch.setattr(main_window.MainWindow, "confirm_quiet_start", lambda self: True)
    monkeypatch.setattr(main_window.MainWindow, "ask_file_association", lambda self: False)


@pytest.fixture(autouse=True)
def _no_real_system_eq(monkeypatch, tmp_path):
    """Windows built in tests never find (or write to) a real EasyEffects in the user's home."""
    from resonancefit.easyeffects_apply import EasyEffectsApplier
    from resonancefit.ui import main_window
    real = main_window.system_eq_for

    def fake(platform, settings):
        if platform == "linux":
            return EasyEffectsApplier(platform, settings, home=tmp_path / "ee-home", env={}, which=lambda _n: None)
        return real(platform, settings)
    monkeypatch.setattr(main_window, "system_eq_for", fake)


@pytest.fixture(autouse=True)
def _gc_on_gui_thread(qapp):
    """As in the app: no garbage collection in worker threads during a test.

    Windows are closed (joining their workers) and garbage is collected on
    the GUI thread before automatic collection is turned back on.
    """
    import gc
    gc.disable()
    yield
    for widget in qapp.topLevelWidgets():
        widget.close()
    qapp.processEvents()
    gc.collect()
    gc.enable()
