"""EQ Helper's "Generate customer app…" dialog."""

import gc
from pathlib import Path

import pytest
from PySide6.QtCore import QDate

from eq_helper.admin.customer_library import CustomerLibrary, MeasurementSource
from eq_helper.calibration.rescal import Accessory, Customer, Iem, Manifest, SongLink, read_rescal
from eq_helper.ui.generate_customer_dialog import GenerateCustomerDialog
from tests.test_generate import FakeCache, write_fits


@pytest.fixture(autouse=True)
def _gc_on_gui_thread(qapp):
    gc.disable()
    yield
    for widget in qapp.topLevelWidgets():
        widget.close()
    qapp.processEvents()
    gc.collect()
    gc.enable()


@pytest.fixture
def env(tmp_path):
    return {"library": CustomerLibrary(tmp_path / "lib"), "cache": FakeCache(tmp_path / "builds"),
            "settings": {}, "opened": []}


def _dialog(qtbot, env):
    d = GenerateCustomerDialog(settings=env["settings"], library=env["library"], cache=env["cache"],
                               opener=env["opened"].append)
    qtbot.addWidget(d)
    return d


def _fill(d, tmp_path, name="Ada"):
    write_fits(tmp_path / "m")
    d.customer_name.setCurrentText(name)
    d.iem_brand.setText("Truthear")
    d.iem_model.setText("Nova")
    d.folder_edit.setText(str(tmp_path / "m"))
    d.folder_edit.editingFinished.emit()
    d.output_edit.setText(str(tmp_path / "out"))


def test_defaults_diamond_coupler_today_and_all_targets(qtbot, env):
    d = _dialog(qtbot, env)
    assert d.default_target.currentText() == "PEQdB Diamond.txt"
    assert d.coupler.text() == "IEC-711 clone"
    assert d.date_edit.date() == QDate.currentDate()
    checked = [p.name for p in d.chosen_targets()]
    assert len(checked) == d.target_list.count() - 1 >= 10
    assert "Flat Line.txt" not in checked  # diagnostic, not a coupler target
    assert d.system_checks["windows"].isChecked()


def test_preview_shows_fits_and_peak_range_per_side_and_warns(qtbot, env, tmp_path):
    d = _dialog(qtbot, env)
    write_fits(tmp_path / "few", peaks=(7000, 8000))
    d.folder_edit.setText(str(tmp_path / "few"))
    d.folder_edit.editingFinished.emit()
    qtbot.waitUntil(lambda: "Left: 2 fits" in d.preview_label.text(), timeout=20000)
    assert "Hz" in d.preview_label.text()
    assert "at least 3" in d.preview_warning.text()
    d.sides_radio.setChecked(True)
    write_fits(tmp_path / "lr", per_side_folder=True)
    d.left_edit.setText(str(tmp_path / "lr" / "L"))
    d.right_edit.setText(str(tmp_path / "lr" / "R"))
    d.right_edit.editingFinished.emit()
    qtbot.waitUntil(lambda: "Left: 4 fits" in d.preview_label.text(), timeout=20000)
    assert not d.preview_warning.text()


def test_validation_messages(qtbot, env):
    d = _dialog(qtbot, env)
    with pytest.raises(ValueError, match="customer"):
        d.request()
    d.customer_name.setCurrentText("Ada")
    with pytest.raises(ValueError, match="IEM"):
        d.request()
    d.iem_brand.setText("Truthear")
    d.iem_model.setText("Nova")
    with pytest.raises(ValueError, match="measurement"):
        d.request()


def test_request_collects_every_field(qtbot, env, tmp_path):
    d = _dialog(qtbot, env)
    _fill(d, tmp_path)
    d.customer_contact.setText("ada@example.com")
    d.iem_serial.setText("SN1")
    tips, whose = d.accessories["tips"]
    tips.setCurrentText("Spinfit W1 M")
    whose.setCurrentIndex(whose.findData("customer"))
    dac, dac_whose = d.accessories["dac"]
    dac.setCurrentText("Apple dongle")
    dac_whose.setCurrentIndex(dac_whose.findData("operator"))
    d.notes.setPlainText("deep fit")
    d.operator_name.setText("Mohamed")
    d.links_edit.setPlainText("Treble check | https://example.com/t\n\nhttps://example.com/only-url")
    d.system_checks["linux"].setChecked(True)
    req = d.request()
    m = req.manifest
    assert m.customer == Customer("Ada", "ada@example.com") and m.iem == Iem("Truthear", "Nova", "SN1")
    assert m.accessories == [Accessory("tips", "Spinfit W1 M", "customer"), Accessory("dac", "Apple dongle", "operator")]
    assert m.notes == "deep fit" and m.operator.name == "Mohamed" and m.coupler == "IEC-711 clone"
    assert m.test_song_links == [SongLink("Treble check", "https://example.com/t"),
                                 SongLink("https://example.com/only-url", "https://example.com/only-url")]
    assert m.default_target == "PEQdB Diamond.txt"
    assert req.systems == ["windows", "linux"]
    assert req.source == MeasurementSource.from_folder(tmp_path / "m")
    d.file_only.setChecked(True)
    assert d.request().systems == []
    assert not d.system_checks["windows"].isEnabled()


def test_generate_end_to_end_saves_record_and_opens_folder(qtbot, env, tmp_path):
    d = _dialog(qtbot, env)
    _fill(d, tmp_path)
    dac, whose = d.accessories["dac"]
    dac.setCurrentText("Apple dongle")
    whose.setCurrentIndex(whose.findData("operator"))
    d.generate_button.click()
    qtbot.waitUntil(lambda: d.result is not None, timeout=30000)
    outputs = d.result.outputs
    assert any(p.name.endswith("(Windows).zip") for p in outputs)
    rescal = read_rescal(next(p for p in outputs if p.suffix == ".rescal"))
    assert rescal.manifest.customer.name == "Ada"
    assert env["opened"] == [tmp_path / "out"]
    assert env["settings"]["customer_output_dir"] == str(tmp_path / "out")
    # the next dialog remembers the operator's accessories and prefills from the last record
    d2 = _dialog(qtbot, env)
    combo, whose2 = d2.accessories["dac"]
    assert combo.currentText() == "Apple dongle" and whose2.currentData() == "operator"
    assert d2.iem_model.text() == "Nova" and d2.customer_name.currentText() == ""
    assert d2.output_edit.text() == str(tmp_path / "out")


def test_start_from_saved_calibration_fills_everything(qtbot, env, tmp_path):
    write_fits(tmp_path / "m")
    env["library"].save(Manifest(customer=Customer("Bob", "b@x"), iem=Iem("Moondrop", "Aria"), notes="n",
                                 created="2026-01-01"),
                        MeasurementSource.from_folder(tmp_path / "m"), targets=["Flat Line.txt"], systems=["linux"])
    d = _dialog(qtbot, env)
    index = d.library_combo.findText("Bob", flags=__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.MatchContains)
    d.library_combo.setCurrentIndex(index)
    assert d.customer_name.currentText() == "Bob" and d.iem_model.text() == "Aria"
    assert d.folder_edit.text() == str(tmp_path / "m")
    assert [Path(t).name for t in d.chosen_targets()] == ["Flat Line.txt"]
    assert d.system_checks["linux"].isChecked() and not d.system_checks["windows"].isChecked()
    assert d.date_edit.date() == QDate.currentDate()


def test_menu_entry_in_eq_helper(qtbot, monkeypatch):
    from eq_helper.ui import main_window as module
    win = module.MainWindow()
    qtbot.addWidget(win)
    names = [a.text() for m in win.menuBar().actions() for a in m.menu().actions()]
    assert "Generate customer app…" in names
