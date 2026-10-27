"""Is a newer version of the app published? Notify only — nothing is installed.

When it checks: only when the app opens (no background service), at most once
a day after a *successful* check. If the last attempt failed (offline, GitHub
unreachable…), the next launch tries again, even on the same day. Settings has
"Check for updates now" and shows when the last successful check happened.

What it sends: one anonymous request to GitHub's public releases API for the
releases repo; nothing about the customer or their calibration.

State lives in the app's settings: ``update_last_success`` (ISO time of the
last successful check), ``update_last_attempt``, ``update_last_error``,
``update_latest`` (the latest version, page and download links).
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

DEFAULT_RELEASES_REPO = "MohamedKhalil8801/resonancefit-releases"
API = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT_S = 10
_ASSET = re.compile(r"-(windows|macos|linux)\.(zip|dmg|appimage)$", re.IGNORECASE)


def parse_version(text: str) -> tuple[int, ...] | None:
    numbers = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in numbers) if numbers else None


def newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


class UrllibJson:
    def get_json(self, url, headers=None):
        request = urllib.request.Request(url, headers={"User-Agent": "update-check", **(headers or {})})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    available: bool = False
    latest: str = ""
    page_url: str = ""
    download_url: str = ""
    error: str = ""
    checked_at: datetime | None = None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_time(text) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


class UpdateChecker:
    def __init__(self, settings, current_version: str, platform: str, repo: str = DEFAULT_RELEASES_REPO,
                 http=None, now: Callable[[], datetime] | None = None):
        self.settings = settings
        self.current_version = current_version
        self.platform = platform
        self.repo = repo
        self.http = http or UrllibJson()
        self.now = now or datetime.now

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def last_success(self) -> datetime | None:
        return _parse_time(self.settings.get("update_last_success"))

    @property
    def automatic(self) -> bool:
        return self.settings.get("update_check_automatic", True) is not False

    def due(self) -> bool:
        """Check at launch unless a check already succeeded today."""
        last = self.last_success
        return last is None or last.date() != self.now().date()

    def last_result(self) -> UpdateResult | None:
        """What Settings shows: the outcome of the most recent attempt."""
        attempt = _parse_time(self.settings.get("update_last_attempt"))
        if attempt is None:
            return None
        error = self.settings.get("update_last_error")
        if error:
            return UpdateResult(False, error=str(error), checked_at=attempt)
        latest = self.settings.get("update_latest") or {}
        version = str(latest.get("version", ""))
        return UpdateResult(True, newer(version, self.current_version), version, str(latest.get("page", "")),
                            str(latest.get("download", "")), checked_at=self.last_success)

    # ── checking ───────────────────────────────────────────────────────────
    def check(self) -> UpdateResult:
        """Ask GitHub for the latest release. Never raises; safe on a worker thread."""
        now = self.now()
        try:
            data = self.http.get_json(API.format(repo=self.repo), {"Accept": "application/vnd.github+json"})
            tag = str(data["tag_name"])
            if parse_version(tag) is None:
                raise ValueError(f"unexpected release tag {tag!r}")
            page = str(data.get("html_url") or "")
            download = next((str(a.get("browser_download_url", "")) for a in data.get("assets") or []
                             if (m := _ASSET.search(str(a.get("name", "")))) and m.group(1).lower() == self.platform),
                            "")
        except Exception as exc:  # noqa: BLE001 - offline, GitHub down, odd answer: all "couldn't check"
            self.settings.update(update_last_attempt=_iso(now), update_last_error=str(exc) or type(exc).__name__)
            return UpdateResult(False, error=str(exc) or type(exc).__name__, checked_at=now)
        version = ".".join(str(n) for n in parse_version(tag))
        self.settings.update(update_last_attempt=_iso(now), update_last_success=_iso(now), update_last_error=None,
                             update_latest={"version": version, "page": page, "download": download})
        return UpdateResult(True, newer(version, self.current_version), version, page, download, checked_at=now)

    # ── the launch banner ──────────────────────────────────────────────────
    def dismissed(self, version: str) -> bool:
        return self.settings.get("update_dismissed") == version

    def dismiss(self, version: str) -> None:
        self.settings.set("update_dismissed", version)
