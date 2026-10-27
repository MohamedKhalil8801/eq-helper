"""Prebuilt customer apps: fetched from the public releases repo, verified, cached."""

import hashlib

import pytest

from eq_helper.admin.build_cache import BuildCache, BuildCacheError, asset_os, version_key

REPO = "owner/releases"


class FakeHttp:
    def __init__(self, releases, files):
        self.releases, self.files = releases, files
        self.downloads, self.headers = [], []
        self.offline = False

    def get_json(self, url, headers=None):
        self.headers.append(headers or {})
        if self.offline:
            raise OSError("no network")
        return self.releases[url]

    def download(self, url, dest, progress=None):
        if self.offline:
            raise OSError("no network")
        self.downloads.append(url)
        dest.write_bytes(self.files[url])
        if progress:
            progress(len(self.files[url]), len(self.files[url]))


def _release(tag, payloads, tamper=None):
    assets, files, sums = [], {}, []
    for name, data in payloads.items():
        url = f"https://dl/{tag}/{name}"
        assets.append({"name": name, "browser_download_url": url, "size": len(data)})
        files[url] = data
        digest = hashlib.sha256(data if name != tamper else b"other").hexdigest()
        sums.append(f"{digest}  {name}")
    sums_url = f"https://dl/{tag}/SHA256SUMS"
    assets.append({"name": "SHA256SUMS", "browser_download_url": sums_url, "size": 1})
    files[sums_url] = ("\n".join(sums) + "\n").encode()
    return {"tag_name": tag, "assets": assets}, files


def _http(*releases):
    rel, files = {}, {}
    latest = None
    for tag, payloads, tamper in releases:
        data, f = _release(tag, payloads, tamper)
        files.update(f)
        latest = data
    rel[f"https://api.github.com/repos/{REPO}/releases/latest"] = latest
    return FakeHttp(rel, files)


PAYLOADS = {"ResonanceFit-1.0.0-windows.zip": b"win" * 100, "ResonanceFit-1.0.0-macos.dmg": b"mac" * 100,
            "ResonanceFit-1.0.0-linux.AppImage": b"lin" * 100}


def test_asset_names_and_versions():
    assert asset_os("ResonanceFit-1.0.0-windows.zip") == "windows"
    assert asset_os("X-2-macos.dmg") == "macos" and asset_os("X-linux.AppImage") == "linux"
    assert asset_os("SHA256SUMS") is None
    assert version_key("rf-v1.10.0") > version_key("rf-v1.9.3") > version_key("1.9")


def test_check_latest_lists_assets_without_a_token(tmp_path):
    http = _http(("rf-v1.0.0", PAYLOADS, None))
    release = BuildCache(tmp_path, REPO, http).check_latest()
    assert release.version == "rf-v1.0.0"
    assert set(release.assets) == {"windows", "macos", "linux"}
    assert all("Authorization" not in h for h in http.headers)


def test_fetch_verifies_and_caches_per_version_and_os(tmp_path):
    http = _http(("rf-v1.0.0", PAYLOADS, None))
    cache = BuildCache(tmp_path, REPO, http)
    path = cache.fetch("windows")
    assert path == tmp_path / "rf-v1.0.0" / "windows" / "ResonanceFit-1.0.0-windows.zip"
    assert path.read_bytes() == PAYLOADS["ResonanceFit-1.0.0-windows.zip"]
    count = len(http.downloads)
    assert cache.fetch("windows") == path and len(http.downloads) == count  # cached, no re-download
    assert cache.cached("windows") == path and cache.cached("linux") is None


def test_checksum_mismatch_rejected_and_nothing_cached(tmp_path):
    http = _http(("rf-v1.0.0", PAYLOADS, "ResonanceFit-1.0.0-linux.AppImage"))
    cache = BuildCache(tmp_path, REPO, http)
    with pytest.raises(BuildCacheError, match="checksum"):
        cache.fetch("linux")
    assert cache.cached("linux") is None
    assert not list((tmp_path / "rf-v1.0.0").rglob("*.AppImage*"))


def test_missing_os_or_checksum_is_a_clear_error(tmp_path):
    http = _http(("rf-v1.0.0", {"ResonanceFit-1.0.0-windows.zip": b"w"}, None))
    with pytest.raises(BuildCacheError, match="macOS"):
        BuildCache(tmp_path, REPO, http).fetch("macos")


def test_newer_release_downloaded_alongside_and_preferred(tmp_path):
    cache = BuildCache(tmp_path, REPO, _http(("rf-v1.0.0", PAYLOADS, None)))
    old = cache.fetch("windows")
    newer = {k.replace("1.0.0", "1.1.0"): v + b"!" for k, v in PAYLOADS.items()}
    cache.http = _http(("rf-v1.1.0", newer, None))
    new = cache.fetch("windows")
    assert old.exists() and new != old and "rf-v1.1.0" in str(new)
    assert cache.cached("windows") == new


def test_offline_uses_cache_then_local_folder(tmp_path):
    http = _http(("rf-v1.0.0", PAYLOADS, None))
    cache = BuildCache(tmp_path / "cache", REPO, http)
    win = cache.fetch("windows")
    http.offline = True
    assert cache.resolve("windows") == win
    local = tmp_path / "local"
    local.mkdir()
    (local / "ResonanceFit-0.9.0-macos.dmg").write_bytes(b"m")
    cache.local_folder = local
    assert cache.resolve("macos") == local / "ResonanceFit-0.9.0-macos.dmg"
    with pytest.raises(BuildCacheError, match="offline|reach"):
        cache.resolve("linux")
