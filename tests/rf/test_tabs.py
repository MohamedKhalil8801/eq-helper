"""Secondary tabs: test songs, sound tools, settings, about."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtGui import QDesktopServices

from eq_helper.audio.shared_state import ChannelMode, Mode
from eq_helper.calibration.rescal import Operator, SongLink, write_rescal
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.ui.main_window import MainWindow

from .conftest import make_calibration


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: urls.append(url) or True))
    return urls


@pytest.fixture
def win(qtbot, tmp_path, opened):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    cal = make_calibration(customer="Ada")
    cal.manifest.operator = Operator("Mohamed")
    cal.manifest.test_song_links = [SongLink("Treble check", "https://example.com/treble")]
    write_rescal(folder / "a.rescal", cal)
    w = MainWindow(AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app")), start_audio=False)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None and w.tuning.channel_eq is not None, timeout=20000)
    return w


def _song(path, rate=44100):
    t = np.arange(rate) / rate
    sf.write(path, np.c_[0.3 * np.sin(2 * np.pi * 440 * t), 0.3 * np.sin(2 * np.pi * 660 * t)], rate)
    return path


def test_tabs_present(win):
    names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert names == ["Tune", "Test audio", "Sound tools", "Where can I use my EQ?", "Settings", "About"]


def test_empty_test_audio_shows_large_centred_buttons(qtbot, tmp_path, opened):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration(customer="Ada"))  # no suggested links
    w = MainWindow(AppContext.create(AppPaths("linux", tmp_path / "data", tmp_path / "app")), start_audio=False)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.current is not None, timeout=20000)
    page = w.songs_page
    assert page.list.count() == 0 and not page.list.isVisibleTo(page)
    assert page.empty_title.isVisibleTo(page) and not page.remove_button.isVisibleTo(page)
    for button in (page.add_files_button, page.add_link_button):
        assert button.isVisibleTo(page) and button.property("large") is True and button.minimumHeight() >= 46
    assert page.add_files_button.text() == "+ Add audio files…"
    page.add_link("Reference", "https://example.com/r")
    assert page.list.isVisibleTo(page) and not page.empty_title.isVisibleTo(page)
    assert page.remove_button.isVisibleTo(page)


def test_operator_links_listed_and_open_in_browser(win, opened):
    songs = win.songs_page
    labels = songs.labels()
    assert any("Treble check" in label and "Mohamed" in label for label in labels)
    songs.list.setCurrentRow(0)
    songs.activate_selected()
    assert opened and opened[-1].toString() == "https://example.com/treble"
    assert not songs.remove_button.isEnabled()  # operator's links can't be removed


def test_customer_song_plays_in_app_with_eq_ab(qtbot, win, tmp_path):
    songs = win.songs_page
    song = _song(tmp_path / "Song.wav")
    songs.add_files([str(song)])
    assert any("Song" in label for label in songs.labels())
    assert str(song) in [i["path"] for i in win.ctx.settings.get("songs")]
    win.tuning.audio.route("L")
    songs.list.setCurrentRow(songs.list.count() - 1)
    songs.activate_selected()
    qtbot.waitUntil(lambda: songs.player.playing, timeout=10000)
    engine = win.audio.engine
    assert engine.state.snapshot().mode == Mode.MUSIC
    assert engine.state.snapshot().channel_mode == ChannelMode.BOTH
    songs.eq_toggle.click()
    assert win.audio.eq_enabled and win.listen.eq_toggle.isChecked()
    songs.eq_toggle.click()
    assert not win.audio.eq_enabled and not win.listen.eq_toggle.isChecked()
    win.audio.stop_all()
    assert not songs.player.playing
    assert engine.state.snapshot().mode == Mode.IDLE


def test_unsupported_format_opens_system_player(win, tmp_path):
    songs = win.songs_page
    calls = []
    songs.player.opener = calls.append
    m4a = tmp_path / "x.m4a"
    m4a.write_bytes(b"\0")
    songs.add_files([str(m4a)])
    songs.list.setCurrentRow(songs.list.count() - 1)
    songs.activate_selected()
    assert calls == [m4a]


def test_sound_tools(win):
    tools = win.sound_tools
    engine = win.audio.engine
    tools.route_buttons["R"].click()
    assert engine.state.snapshot().channel_mode == ChannelMode.RIGHT
    tools.freq_spin.setValue(3000)
    tools.sine_button.click()
    snap = engine.state.snapshot()
    assert snap.mode == Mode.SINE and snap.freq == pytest.approx(3000)
    tools.focus_button.click()
    assert engine.state.snapshot().mode == Mode.SWEEP
    tools.sweep_button.click()
    assert engine.state.snapshot().mode == Mode.SWEEP
    tools.pink_button.click()
    assert engine.state.snapshot().mode == Mode.PINK
    assert tools.pink_button.isChecked() and tools.pink_button.text() == "■ Stop noise"
    assert not tools.sweep_button.isChecked() and tools.sweep_button.text().startswith("〰 Full sweep")
    tools.volume.setValue(-20)
    assert engine.state.snapshot().amp == pytest.approx(0.1, rel=1e-3)
    tools.grey_button.click()  # another sound replaces it
    assert engine.state.snapshot().mode == Mode.GREY and win.audio.playing == "grey"
    assert tools.grey_button.isChecked() and not tools.pink_button.isChecked()
    tools.grey_button.click()  # pressing again stops: there's no separate Stop button
    assert engine.state.snapshot().mode == Mode.IDLE and not tools.grey_button.isChecked()
    assert not hasattr(tools, "stop_button")
    tools.sine_button.click()
    win.audio.stop_all()  # Space / Stop all sound elsewhere: the button follows
    assert not tools.sine_button.isChecked() and tools.sine_button.text() == "▶ Tone"


def test_settings_page(win, opened):
    page = win.settings_page
    assert page.device_combo.count() >= 1
    assert page.device_combo.itemText(0) == "System default"
    status = page.apo_status.text()
    assert "isn't available" in status or "isn't installed" in status
    page.open_calibrations.click()
    assert Path(opened[-1].toLocalFile()) == win.ctx.paths.calibrations_folder
    assert win.ctx.paths.calibrations_folder.is_dir()
    assert page.version_label.text()


def test_about_page_branding_licences_and_sheet(win, qtbot, tmp_path, opened):
    about = win.about_page
    assert win.ctx.branding.product_name in about.title.text()
    assert win.ctx.branding.byline in about.title.text()
    assert "Apache" in about.licences.toPlainText()
    sheet = tmp_path / "app" / "Ada – Truthear Nova calibration sheet.pdf"
    sheet.write_bytes(b"%PDF-1.4\n")
    about.refresh_sheets()
    assert about.sheet_combo.count() == 1
    about.open_sheet.click()
    assert Path(opened[-1].toLocalFile()) == sheet


def test_the_side_panels_are_hidden_while_the_guided_test_runs(win):
    assert win.sidebar.isVisibleTo(win)
    win.tuning.start_guided_test()
    assert win.tuning.guided_active and not win.sidebar.isVisibleTo(win)  # no EQ switch, saving or history
    win.tuning.guided.leave_for_manual()
    assert not win.tuning.guided_active and win.sidebar.isVisibleTo(win)
