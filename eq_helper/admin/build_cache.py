"""Prebuilt customer apps, fetched from the public releases-only repo (ADR 0003).

CI publishes one build per OS on each tagged release, named
``<Product>-<version>-windows.zip``, ``-macos.dmg`` and ``-linux.AppImage``,
plus a ``SHA256SUMS`` file. Here they are found through the GitHub REST API
(no token), verified against the checksums and cached per version and OS in
``~/.eq_helper/build_cache/``. Without a network the newest cached build is
used, then a local folder chosen in settings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SYSTEMS = ("windows", "macos", "linux")
SYSTEM_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
DEFAULT_RELEASES_REPO = "MohamedKhalil8801/resonancefit-releases"
CHECKSUMS_NAME = "SHA256SUMS"
_ASSET_RE = re.compile(r"-(windows|macos|linux)\.(zip|dmg|appimage)$", re.IGNORECASE)
_API = "https://api.github.com/repos/{repo}/releases/latest"


class BuildCacheError(Exception):
    """Plain-language problem getting a prebuilt app."""


def default_cache_root() -> Path:
    return Path(os.path.expanduser("~")) / ".eq_helper" / "build_cache"


def asset_os(name: str) -> str | None:
    match = _ASSET_RE.search(name)
    return match.group(1).lower() if match else None


def version_key(tag: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", tag))


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class Release:
    version: str
    assets: dict[str, Asset] = field(default_factory=dict)   # os -> asset
    checksums: Asset | None = None


class UrllibHttp:
    """Minimal HTTP for the GitHub API and asset downloads (no token)."""

    timeout = 20

    def _request(self, url, headers=None):
        return urllib.request.Request(url, headers={"User-Agent": "eq-helper", **(headers or {})})

    def get_json(self, url, headers=None):
        with urllib.request.urlopen(self._request(url, headers), timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def download(self, url, dest: Path, progress: Callable[[int, int], None] | None = None):
        with urllib.request.urlopen(self._request(url), timeout=self.timeout) as response, dest.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1 << 16):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BuildCache:
    def __init__(self, root=None, repo: str = DEFAULT_RELEASES_REPO, http=None, local_folder=None):
        self.root = Path(root) if root is not None else default_cache_root()
        self.repo = repo
        self.http = http or UrllibHttp()
        self.local_folder = Path(local_folder) if local_folder else None

    # ── online ─────────────────────────────────────────────────────────────
    def check_latest(self) -> Release:
        """Ask the releases repo for its latest release ("Check for new build")."""
        try:
            data = self.http.get_json(_API.format(repo=self.repo), {"Accept": "application/vnd.github+json"})
        except Exception as exc:  # noqa: BLE001 - network/HTTP errors become one plain message
            raise BuildCacheError(f"Couldn't reach the releases page ({exc}). You may be offline.") from exc
        try:
            tag = str(data["tag_name"])
            assets, checksums = {}, None
            for raw in data.get("assets", []):
                asset = Asset(str(raw["name"]), str(raw["browser_download_url"]), int(raw.get("size") or 0))
                if asset.name == CHECKSUMS_NAME:
                    checksums = asset
                elif (system := asset_os(asset.name)) is not None:
                    assets[system] = asset
        except (KeyError, TypeError, ValueError) as exc:
            raise BuildCacheError("The releases page answered with something unexpected.") from exc
        return Release(tag, assets, checksums)

    def _checksums(self, release: Release, folder: Path) -> dict[str, str]:
        if release.checksums is None:
            raise BuildCacheError(f"Release {release.version} has no {CHECKSUMS_NAME}; refusing an unverified build.")
        path = folder / CHECKSUMS_NAME
        self.http.download(release.checksums.url, path)
        sums = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0].lower()
        return sums

    def fetch(self, system: str, release: Release | None = None,
              progress: Callable[[int, int], None] | None = None) -> Path:
        """Download (if not cached) and verify the build for ``system``."""
        release = release or self.check_latest()
        asset = release.assets.get(system)
        if asset is None:
            raise BuildCacheError(f"Release {release.version} has no {SYSTEM_NAMES.get(system, system)} build.")
        folder = self.root / release.version / system
        target = folder / asset.name
        if target.exists():
            return target
        folder.mkdir(parents=True, exist_ok=True)
        expected = self._checksums(release, folder).get(asset.name)
        if expected is None:
            raise BuildCacheError(f"{asset.name} isn't listed in {CHECKSUMS_NAME}; refusing an unverified build.")
        partial = folder / (asset.name + ".part")
        try:
            self.http.download(asset.url, partial, progress)
            if _sha256(partial) != expected:
                raise BuildCacheError(f"{asset.name} failed its checksum check; it was not used. Try again.")
            os.replace(partial, target)
        except BuildCacheError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            partial.unlink(missing_ok=True)
            raise BuildCacheError(f"Downloading {asset.name} failed ({exc}).") from exc
        return target

    # ── offline ────────────────────────────────────────────────────────────
    def cached(self, system: str) -> Path | None:
        """The newest verified build in the cache for ``system``."""
        if not self.root.is_dir():
            return None
        found = [p for p in self.root.glob(f"*/{system}/*") if p.is_file() and asset_os(p.name) == system]
        if not found:
            return None
        return max(found, key=lambda p: version_key(p.parent.parent.name))

    def find_local(self, system: str) -> Path | None:
        folder = self.local_folder
        if folder is None or not folder.is_dir():
            return None
        found = [p for p in folder.iterdir() if p.is_file() and asset_os(p.name) == system]
        return max(found, key=lambda p: version_key(p.name)) if found else None

    def resolve(self, system: str, check_online: bool = True) -> Path:
        """Latest build: online if reachable, else the cache, else the local folder."""
        problem = None
        if check_online:
            try:
                return self.fetch(system)
            except BuildCacheError as exc:
                problem = exc
        for path in (self.cached(system), self.find_local(system)):
            if path is not None:
                return path
        raise BuildCacheError(
            f"No {SYSTEM_NAMES.get(system, system)} build is available offline. "
            + (str(problem) + " " if problem else "")
            + "Connect to the internet, or choose a local folder with prebuilt apps in Settings.")
