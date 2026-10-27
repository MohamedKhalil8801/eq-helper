"""App shell: import screen, header card, calibration switching, file opening."""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from eq_helper.calibration.branding import load_branding
from eq_helper.calibration.rescal import Accessory, Operator, write_rescal
from resonancefit.app import calibration_args
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.ui.main_window import MainWindow

from .conftest import make_calibration


def _ctx(tmp_path, platform="linux"):
    return AppContext.create(AppPaths(platform, tmp_path / "data", tmp_path / "app"))


def _window(qtbot, ctx):
    win = MainWindow(ctx, start_audio=False)
    qtbot.addWidget(win)
    return win


def _wait_loaded(qtbot, win):
    qtbot.waitUntil(lambda: win.current is not None and not win.loading, timeout=20000)


def test_no_calibration_shows_import_screen(qtbot, tmp_path):
    win = _window(qtbot, _ctx(tmp_path))
    assert win.showing_import_screen
    assert "Open the calibration file you received" in win.import_screen.headline.text()
    assert win.import_screen.acceptDrops() or win.acceptDrops()


def test_title_uses_branding(qtbot, tmp_path):
    win = _window(qtbot, _ctx(tmp_path))
    assert load_branding().product_name in win.windowTitle()


def test_opening_a_file_imports_it_and_shows_header(qtbot, tmp_path):
    cal = make_calibration(customer="Ada Lovelace", model="Nova")
    cal.manifest.accessories = [Accessory("tips", "Spinfit W1 M", "customer"),
                                Accessory("dac", "Apple dongle", "operator")]
    cal.manifest.operator = Operator("Mohamed")
    src = write_rescal(tmp_path / "Ada.rescal", cal)
    win = _window(qtbot, _ctx(tmp_path))
    win.open_calibration_file(src)
    _wait_loaded(qtbot, win)
    assert not win.showing_import_screen
    text = win.header.text_summary()
    assert "Ada Lovelace" in text and "Truthear Nova" in text and "2026-10-27" in text
    assert "Spinfit W1 M" in text and "yours" in text
    assert "Apple dongle" in text and "provided by Mohamed" in text
    assert win.current.measurements.models.keys() == {"L", "R"}


def test_sibling_calibrations_load_at_launch_newest_first_and_switch(qtbot, tmp_path):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "old.rescal", make_calibration(customer="Old", created="2026-01-01"))
    write_rescal(folder / "new.rescal", make_calibration(customer="New", created="2026-10-01"))
    ctx = _ctx(tmp_path)
    win = _window(qtbot, ctx)
    _wait_loaded(qtbot, win)
    assert win.current.manifest.customer.name == "New"
    switcher = win.header.switcher
    assert switcher.count() == 2
    switcher.setCurrentIndex(1)
    _wait_loaded(qtbot, win)
    qtbot.waitUntil(lambda: win.current.manifest.customer.name == "Old", timeout=20000)
    assert "Old" in win.header.text_summary()
    # the choice is remembered for the next launch
    win2 = _window(qtbot, _ctx(tmp_path))
    _wait_loaded(qtbot, win2)
    assert win2.current.manifest.customer.name == "Old"


def test_invalid_file_reports_error_and_keeps_running(qtbot, tmp_path):
    bad = tmp_path / "bad.rescal"
    bad.write_bytes(b"not a zip")
    win = _window(qtbot, _ctx(tmp_path))
    win.open_calibration_file(bad)
    assert win.showing_import_screen
    assert win.import_screen.error.text()
    assert "bad.rescal" in win.import_screen.error.text()


def test_drop_rescal_on_window_imports(qtbot, tmp_path):
    src = write_rescal(tmp_path / "x.rescal", make_calibration(customer="Dropped"))
    win = _window(qtbot, _ctx(tmp_path))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(src))])
    event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    win.dropEvent(event)
    _wait_loaded(qtbot, win)
    assert win.current.manifest.customer.name == "Dropped"


def test_macos_translocated_note_on_import_screen(qtbot, tmp_path):
    paths = AppPaths("macos", tmp_path / "data",
                     Path("/private/var/folders/x/AppTranslocation/ABC/d/App.app/Contents/MacOS"))
    win = _window(qtbot, AppContext.create(paths))
    assert win.showing_import_screen
    assert win.import_screen.note.isVisible() or win.import_screen.note.text()


def test_calibration_args_picks_rescal_paths(tmp_path):
    f = tmp_path / "a.rescal"
    f.write_bytes(b"")
    assert calibration_args(["app", str(f), "--self-test", str(tmp_path / "b.txt")]) == [f]
