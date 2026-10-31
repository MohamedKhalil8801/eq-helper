"""Export flow: where will you listen → app cards → files, history, setup steps."""

import pytest

from eq_helper.calibration.rescal import write_rescal
from eq_helper.exports.catalog import load_catalog
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.ui.app_cards import AppCard
from resonancefit.ui.export_flow import ExportFlow, ExportJob, listening_label
from resonancefit.ui.main_window import MainWindow

from .conftest import make_calibration


@pytest.fixture
def win(qtbot, tmp_path):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration(customer="Ada"))
    w = MainWindow(AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app")), start_audio=False)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None and w.tuning.channel_eq is not None, timeout=20000)
    return w


def _flow(qtbot, win, tmp_path, installed=frozenset(), device_rate=44100):
    flow = ExportFlow(win.export_job(), load_catalog(), platform="linux", installed=set(installed),
                      device_rate=device_rate, folder_provider=lambda: str(tmp_path / "out"))
    qtbot.addWidget(flow)
    return flow


def test_job_describes_customer_iem_and_tuning(win):
    job = win.export_job()
    assert job.meta.customer == "Ada" and job.meta.iem == "Truthear Nova"
    assert "PEQdB Diamond" in job.meta.tuning and "7700" in job.meta.tuning
    assert "Truthear Nova" in job.base_name and "/" not in job.base_name


def test_every_catalog_app_exports_files_and_records_history(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path)
    count = 0
    for app in load_catalog().apps:
        for index, fmt in enumerate(app.formats):
            if app.advanced and fmt.format == "convolution":
                continue  # opens the full dialog (tested below)
            out = tmp_path / "out" / f"{app.id}-{index}"
            result = flow.export_app(app, index, out)
            assert result.files, (app.id, fmt.format)
            assert all(p.exists() and p.stat().st_size > 0 for p in result.files)
            count += 1
    entries = win.history.entries()
    assert count > 10 and len(entries) == 1  # one tuning, exported for every app: kept once
    entry = entries[0]
    assert entry.reason == "export" and len(entry.files) >= count  # every export's files are listed


def test_listening_choices_filter_and_installed_first(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path, installed={"roon"})
    flow.choose("this_computer")
    ids = flow.card_ids()
    assert ids[0] == "roon" and "easyeffects" in ids and ids[-1] == "other"
    assert flow.cards["roon"].installed_badge.isVisibleTo(flow)
    assert "Linux" in listening_label("this_computer", "linux")
    flow.choose("phone", "android")
    assert "wavelet" in flow.card_ids() and "easyeffects" not in flow.card_ids()
    flow.choose("phone", "ios")
    assert "neutron" in flow.card_ids() and "wavelet" not in flow.card_ids()
    flow.choose("hardware")
    assert "qudelix-5k" in flow.card_ids()
    flow.choose("other_computer", "windows")
    assert flow.card_ids()[0] == "equalizer-apo" or "equalizer-apo" in flow.card_ids()
    # full L/R apps come before shared-only ones
    flow.choose("other_computer", "macos")
    lr = [flow.catalog.get(i).lr for i in flow.card_ids() if i != "other"]
    assert lr == sorted(lr, key=lambda v: v != "full")


def test_card_labels_and_shared_only_compromise(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path)
    flow.choose("other_computer", "macos")
    card = flow.cards["soundsource"]
    text = card.text_summary()
    assert "Left/right correction: shared only" in text
    assert "balance" in text.lower() and "dB" in text
    assert "free" in text.lower() or "paid" in text.lower()
    full = flow.cards["camilladsp"].text_summary()
    assert "Left/right correction: full" in full


def test_parametric_accuracy_line_appears(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path)
    flow.choose("other_computer", "macos")
    qtbot.waitUntil(lambda: "within" in flow.cards["eqmac"].accuracy.text(), timeout=20000)


def test_sample_rate_is_the_only_convolution_setting_prefilled_from_device(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path, device_rate=44100)
    flow.choose("this_computer")
    card = flow.cards["easyeffects"]
    assert card.rate_combo is not None and card.rate_combo.currentData() == 44100
    result = flow.export_app(flow.catalog.get("easyeffects"), 0, tmp_path / "ee", rate=card.rate_combo.currentData())
    assert any("44.1kHz" in p.name for p in result.files)
    assert flow.cards["camilladsp"].rate_combo is None  # fixed set of rates


def test_phone_export_shows_import_steps(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path)
    flow.choose("phone", "android")
    flow.run_export("wavelet", 0)
    qtbot.waitUntil(lambda: flow.page == "done", timeout=20000)
    app = flow.catalog.get("wavelet")
    assert app.setup_steps[0] in flow.done_text()
    assert "phone" in flow.done_text().lower()


def test_predicted_response_export(qtbot, win, tmp_path):
    flow = _flow(qtbot, win, tmp_path)
    files = flow.export_predicted(tmp_path / "pred", with_eq=True)
    names = sorted(p.name for p in files)
    assert names == ["Truthear Nova L with EQ.txt", "Truthear Nova L.txt",
                     "Truthear Nova R with EQ.txt", "Truthear Nova R.txt"]
    assert win.history.entries()[0].app == "predicted-response"


def test_other_advanced_opens_full_convolution_dialog(qtbot, win, tmp_path, monkeypatch):
    from resonancefit.ui import export_flow as module
    opened = {}

    class Fake:
        def __init__(self, eq, settings, parent):
            opened["eq"] = eq
            self.saved_paths = [tmp_path / "x.wav"]
            (tmp_path / "x.wav").write_bytes(b"RIFF")

        def exec(self):
            return 1

        def options(self):
            return {"resonance_export_dir": str(tmp_path)}

    monkeypatch.setattr(module, "ConvolutionExportDialog", Fake)
    flow = _flow(qtbot, win, tmp_path)
    flow.choose("this_computer")
    other = flow.catalog.get("other")
    index = next(i for i, f in enumerate(other.formats) if f.format == "convolution")
    flow.run_export("other", index)
    assert opened["eq"] is win.export_job().channel_eq
    assert win.history.entries()[0].app == "other"


def _card(qtbot, app_id, installed=frozenset()):
    app = next(a for a in load_catalog().apps if a.id == app_id)
    card = AppCard(app, app_id in installed, can_export=False, others_installed=installed)
    qtbot.addWidget(card)
    return card


def test_installed_apps_show_website_instead_of_download(qtbot):
    installed = _card(qtbot, "equalizer-apo", {"equalizer-apo"})
    other = _card(qtbot, "foobar2000", {"equalizer-apo"})
    assert installed.download_button is None and installed.website_button is not None
    assert other.download_button is not None and other.website_button is None
    assert not other.export_buttons


def test_export_button_on_main_window_opens_flow(qtbot, win, monkeypatch):
    from resonancefit.ui import main_window as module
    shown = []
    monkeypatch.setattr(module.ExportFlow, "exec", lambda self: shown.append(self) or 0)
    win.tunings.export_button.click()
    assert shown and shown[0].job.channel_eq is win.tuning.channel_eq


def test_peace_is_not_offered_but_its_presence_is_flagged_on_the_apo_card(qtbot):
    catalog = load_catalog()
    assert "peace" not in [a.id for a in catalog.for_listening("this_computer", "windows")]
    card = _card(qtbot, "equalizer-apo", {"equalizer-apo", "peace"})
    assert any("Peace" in note for note in card.conflict_notes)
