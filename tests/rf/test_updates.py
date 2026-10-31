"""Update check: on launch, once a day after a success; retried after a failure; manual in Settings."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from eq_helper.calibration.rescal import write_rescal
from resonancefit.context import AppContext
from resonancefit.paths import AppPaths
from resonancefit.settings import Settings
from resonancefit.ui.main_window import MainWindow
from resonancefit.updates import UpdateChecker, newer, parse_version

from .conftest import make_calibration

API = "https://api.github.com/repos/owner/releases/releases/latest"
RELEASE = {"tag_name": "rf-v0.3.0", "html_url": "https://github.com/owner/releases/releases/tag/rf-v0.3.0",
           "assets": [{"name": f"ResonanceFit-0.3.0-{s}.{e}", "browser_download_url": f"https://dl/{s}"}
                      for s, e in (("windows", "zip"), ("macos", "dmg"), ("linux", "AppImage"))]
           + [{"name": "SHA256SUMS", "browser_download_url": "https://dl/sums"}]}


class FakeHttp:
    def __init__(self, data=RELEASE, fail=False):
        self.data, self.fail, self.calls = data, fail, 0

    def get_json(self, url, headers=None):
        self.calls += 1
        assert url == API and "Authorization" not in (headers or {})
        if self.fail:
            raise OSError("no network")
        return self.data


class Clock:
    def __init__(self, now=datetime(2026, 10, 27, 9, 0)):
        self.now = now

    def __call__(self):
        return self.now


def _checker(tmp_path, http, clock, version="0.2.0", platform="windows"):
    return UpdateChecker(Settings(tmp_path / "s.json"), version, platform, repo="owner/releases", http=http,
                         now=clock)


def test_versions():
    assert parse_version("rf-v0.10.2") == (0, 10, 2) and parse_version("0.2.0") == (0, 2, 0)
    assert newer("rf-v0.3.0", "0.2.9") and not newer("rf-v0.2.0", "0.2.0") and not newer("rf-v0.1.9", "0.2.0")
    assert parse_version("dev") is None and not newer("rf-v9.9.9", "dev")


def test_first_launch_checks_and_finds_update_for_this_system(tmp_path):
    http, clock = FakeHttp(), Clock()
    c = _checker(tmp_path, http, clock, platform="macos")
    assert c.due()
    result = c.check()
    assert result.ok and result.available and result.latest == "0.3.0"
    assert result.download_url == "https://dl/macos" and result.page_url.endswith("rf-v0.3.0")
    assert c.last_success == clock.now and not c.due()


def test_once_a_day_after_success_retry_after_failure(tmp_path):
    http, clock = FakeHttp(), Clock()
    c = _checker(tmp_path, http, clock)
    c.check()
    clock.now += timedelta(hours=10)                 # same day, reopened
    assert not c.due()
    clock.now = datetime(2026, 10, 28, 0, 5)         # next day
    assert c.due()
    http.fail = True
    result = c.check()
    assert not result.ok and "no network" in result.error
    assert c.last_success == datetime(2026, 10, 27, 9, 0)  # failure doesn't count as a check
    assert c.due()                                   # reopened later the same day: try again
    http.fail = False
    clock.now += timedelta(hours=1)
    assert c.check().ok and not c.due()


def test_state_survives_restart_and_up_to_date(tmp_path):
    http, clock = FakeHttp({**RELEASE, "tag_name": "rf-v0.2.0"}), Clock()
    first = _checker(tmp_path, http, clock)
    assert first.check().available is False
    again = _checker(tmp_path, http, clock)
    assert not again.due() and again.last_success == clock.now
    assert again.last_result().ok and not again.last_result().available


def test_unexpected_answer_is_a_failure(tmp_path):
    c = _checker(tmp_path, FakeHttp({"message": "Not Found"}), Clock())
    result = c.check()
    assert not result.ok and c.last_success is None and c.due()


def test_source_runs_never_claim_an_update(tmp_path):
    c = _checker(tmp_path, FakeHttp(), Clock(), version="dev")
    result = c.check()
    assert result.ok and not result.available


# ── UI ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def make_win(qtbot, tmp_path):
    folder = tmp_path / "app" / "Calibrations"
    folder.mkdir(parents=True)
    write_rescal(folder / "a.rescal", make_calibration())

    def make(http, clock=None, release=True, updater=None):
        ctx = AppContext.create(AppPaths("windows", tmp_path / "data", tmp_path / "app"))
        updates = UpdateChecker(ctx.settings, "0.2.0", "windows", repo="owner/releases", http=http,
                                now=clock or Clock(), release=release, updater=updater)
        w = MainWindow(ctx, start_audio=False, updates=updates)
        w.opened_urls = []
        w.open_url = w.opened_urls.append
        qtbot.addWidget(w)
        return w
    return make


def test_launch_check_shows_banner_and_settings(qtbot, make_win):
    http = FakeHttp()
    w = make_win(http)
    qtbot.waitUntil(lambda: w.update_banner.isVisibleTo(w), timeout=10000)
    assert "0.3.0" in w.update_banner.text.text()
    page = w.settings_page
    assert "0.2.0" in page.version_label.text()
    assert "27 Oct 2026" in page.update_last_checked.text()
    assert "0.3.0" in page.update_status.text()
    w.update_banner.download.click()
    assert w.opened_urls == ["https://dl/windows"]
    w.update_banner.dismiss.click()
    assert not w.update_banner.isVisibleTo(w)
    assert http.calls == 1


def test_no_second_check_the_same_day_and_dismissed_stays_hidden(qtbot, make_win):
    clock = Clock()
    first = make_win(FakeHttp(), clock)
    qtbot.waitUntil(lambda: first.update_banner.isVisibleTo(first), timeout=10000)
    first.update_banner.dismiss.click()
    http = FakeHttp()
    second = make_win(http, clock)
    qtbot.wait(300)
    assert http.calls == 0                                  # checked successfully today already
    assert not second.update_banner.isVisibleTo(second)     # dismissed for this version
    assert "0.3.0" in second.settings_page.update_status.text()  # still visible in Settings


def test_failed_launch_check_then_manual_check(qtbot, make_win):
    http = FakeHttp(fail=True)
    w = make_win(http)
    page = w.settings_page
    qtbot.waitUntil(lambda: "Couldn't check" in page.update_status.text(), timeout=10000)
    assert "Never" in page.update_last_checked.text()
    assert not w.update_banner.isVisibleTo(w)
    http.fail = False
    page.check_updates_button.click()
    qtbot.waitUntil(lambda: "0.3.0" in page.update_status.text(), timeout=10000)
    assert "27 Oct 2026" in page.update_last_checked.text()
    assert http.calls == 2


def test_automatic_checks_can_be_turned_off(qtbot, make_win):
    http = FakeHttp()
    w = make_win(http)
    qtbot.waitUntil(lambda: http.calls == 1, timeout=10000)
    w.settings_page.auto_update_check.setChecked(False)
    assert w.ctx.settings.get("update_check_automatic") is False
    http2 = FakeHttp()
    clock = Clock(datetime(2026, 10, 30, 9, 0))
    w2 = make_win(http2, clock)
    qtbot.wait(300)
    assert http2.calls == 0
    w2.settings_page.check_updates_button.click()           # manual checks still work
    qtbot.waitUntil(lambda: http2.calls == 1, timeout=10000)


def test_development_builds_never_check(qtbot, make_win):
    http = FakeHttp()
    w = make_win(http, release=False)
    qtbot.wait(300)
    assert http.calls == 0 and not w.update_banner.isVisibleTo(w)
    page = w.settings_page
    assert page.update_dev_note.isVisibleTo(page) and not page.check_updates_button.isVisibleTo(page)
    page.updateCheckRequested.emit()
    qtbot.wait(100)
    assert http.calls == 0


def test_dev_builds_are_any_version_but_plain_x_y_z(tmp_path):
    def enabled(version):
        return UpdateChecker(Settings(tmp_path / "s.json"), version, "windows").enabled
    assert not enabled("0.1.3-dev") and not enabled("dev")
    assert not enabled("0.3.0")  # a source run (not frozen) is development too


class FakeUpdater:
    """Stands in for updater.Updater: downloads "instantly", records installs."""

    def __init__(self, can_install=True, fail=None, ready=None):
        self.can_install, self.fail, self._ready = can_install, fail, ready
        self.downloads, self.installs, self.tidied = [], [], 0
        self.failure = ""

    def problem(self):
        return "" if self.can_install else "read-only"

    def tidy(self):
        self.tidied += 1

    def ready(self):
        return self._ready

    def last_failure(self):
        return self.failure

    def download(self, result, progress=None, cancelled=None):
        from resonancefit.updater import Staged, UpdateError
        self.downloads.append(result.latest)
        if progress:
            progress(50, 100)
            progress(100, 100)
        if self.fail:
            raise UpdateError(self.fail)
        self._ready = Staged(result.latest, Path("staged"))
        return self._ready

    def install(self, relaunch):
        self.installs.append(relaunch)
        return self._ready is not None


def test_new_version_downloads_by_itself_then_install_and_restart(qtbot, make_win):
    updater = FakeUpdater()
    w = make_win(FakeHttp(), updater=updater)
    banner = w.update_banner
    qtbot.waitUntil(lambda: banner.state == "ready", timeout=10000)
    assert updater.downloads == ["0.3.0"] and updater.tidied == 1
    assert banner.isVisibleTo(w) and banner.install.isVisibleTo(banner) and not banner.download.isVisibleTo(banner)
    assert "0.3.0 is ready" in banner.text.text()
    page = w.settings_page
    assert page.update_install.isVisibleTo(page) and not page.update_download.isVisibleTo(page)
    assert "downloaded" in page.update_status.text()
    banner.install.click()
    assert updater.installs == [True]   # the new build restarts the app once it has replaced it
    assert not w.isVisible()


def test_closing_installs_a_downloaded_update_without_restarting(qtbot, make_win):
    updater = FakeUpdater()
    w = make_win(FakeHttp(), updater=updater)
    qtbot.waitUntil(lambda: w.update_banner.state == "ready", timeout=10000)
    w.update_banner.dismiss.click()     # "Not now" only hides it
    assert not w.update_banner.isVisibleTo(w)
    w.close()
    assert updater.installs == [False]


def test_closing_mid_download_installs_nothing(qtbot, make_win):
    updater = FakeUpdater()
    w = make_win(FakeHttp(fail=True), updater=updater)
    qtbot.wait(300)
    w.close()
    assert updater.installs == []


def test_a_finished_download_is_offered_at_launch_without_checking(qtbot, make_win):
    from resonancefit.updater import Staged
    clock = Clock()
    make_win(FakeHttp(), clock).close()        # checked today already
    updater = FakeUpdater(ready=Staged("0.3.0", Path("staged")))
    http = FakeHttp()
    w = make_win(http, clock, updater=updater)
    qtbot.waitUntil(lambda: w.update_banner.state == "ready", timeout=10000)
    assert http.calls == 0 and updater.downloads == []
    assert w.update_banner.details.isVisibleTo(w.update_banner)  # What's new from the last check


def test_failed_download_offers_retry_and_the_link(qtbot, make_win):
    updater = FakeUpdater(fail="no network")
    w = make_win(FakeHttp(), updater=updater)
    banner = w.update_banner
    qtbot.waitUntil(lambda: banner.state == "failed", timeout=10000)
    assert "no network" in banner.text.text()
    assert banner.retry.isVisibleTo(banner) and banner.download.text() == "Download it yourself"
    banner.download.click()
    assert w.opened_urls == ["https://dl/windows"]
    updater.fail = None
    banner.retry.click()
    qtbot.waitUntil(lambda: banner.state == "ready", timeout=10000)
    assert updater.downloads == ["0.3.0", "0.3.0"]


def test_a_failed_install_is_shown_with_the_link(qtbot, make_win):
    from resonancefit.updater import Staged
    updater = FakeUpdater(ready=Staged("0.3.0", Path("staged")))
    updater.failure = "access denied"
    w = make_win(FakeHttp(), updater=updater)
    banner = w.update_banner
    qtbot.waitUntil(lambda: banner.state == "ready", timeout=10000)
    assert "access denied" in banner.text.text() and banner.download.isVisibleTo(banner)


def test_where_the_app_cant_replace_itself_it_offers_the_link(qtbot, make_win):
    updater = FakeUpdater(can_install=False)
    w = make_win(FakeHttp(), updater=updater)
    banner = w.update_banner
    qtbot.waitUntil(lambda: banner.isVisibleTo(w), timeout=10000)
    assert banner.state == "manual" and updater.downloads == [] and banner.download.text() == "Download"
    assert w.settings_page.update_download.isVisibleTo(w.settings_page)
    w.close()
    assert updater.installs == []
