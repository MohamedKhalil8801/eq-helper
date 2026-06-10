"""WMI health: bounded probe + repair semantics (no real UAC/service calls)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from eq_helper import wmi_health


def test_probe_true_when_query_succeeds(monkeypatch):
    monkeypatch.setattr(
        wmi_health.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    assert wmi_health.probe_wmi() is True


def test_probe_false_on_timeout(monkeypatch):
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=kwargs.get("timeout", 6))

    monkeypatch.setattr(wmi_health.subprocess, "run", _timeout)
    monkeypatch.setattr(sys, "platform", "win32")
    assert wmi_health.probe_wmi() is False


def test_probe_true_when_powershell_missing(monkeypatch):
    # Can't tell -> don't nag the user about repairs.
    def _missing(*args, **kwargs):
        raise FileNotFoundError("powershell")

    monkeypatch.setattr(wmi_health.subprocess, "run", _missing)
    monkeypatch.setattr(sys, "platform", "win32")
    assert wmi_health.probe_wmi() is True


def test_repair_verifies_with_probe(monkeypatch):
    calls = []

    def _run(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(wmi_health.subprocess, "run", _run)
    monkeypatch.setattr(sys, "platform", "win32")
    assert wmi_health.repair_wmi() is True
    # One elevated repair invocation + one verification probe.
    assert len(calls) == 2


def test_repair_false_when_repair_times_out(monkeypatch):
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=1)

    monkeypatch.setattr(wmi_health.subprocess, "run", _timeout)
    monkeypatch.setattr(sys, "platform", "win32")
    assert wmi_health.repair_wmi() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_live_probe_is_bounded():
    # Real probe against this machine: whatever the answer, it must return
    # within the timeout (wedged WMI == False, healthy == True; both fine).
    import time

    t0 = time.time()
    result = wmi_health.probe_wmi(timeout_s=6.0)
    assert isinstance(result, bool)
    assert time.time() - t0 < 12.0
