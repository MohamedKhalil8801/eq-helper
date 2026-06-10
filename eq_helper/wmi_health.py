"""Detect and repair a wedged Windows WMI service.

On some machines, changing audio devices wedges the WMI service (``Winmgmt`` /
its ``WmiPrvSE`` provider hosts): every WMI query then blocks forever. That
state froze this app at startup (Python's ``platform`` module queries WMI on
import of numpy/scipy/sounddevice — see :mod:`eq_helper.winquirks`), and it
also breaks other software until the PC is rebooted.

The app itself no longer touches WMI, but at launch it offers to repair the
system state:

- :func:`probe_wmi` — run a tiny WMI query in a subprocess with a timeout.
  A healthy WMI answers in well under a second; a wedged one never answers.
- :func:`repair_wmi` — restart ``Winmgmt`` via an elevated PowerShell (one UAC
  prompt), killing stuck provider hosts and restarting dependent services.
- Verify by re-probing.

Everything is Qt-free and subprocess-based so a wedged WMI can never block the
caller beyond the given timeouts.
"""

from __future__ import annotations

import subprocess
import sys

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Quick query against a class that's always present. -NoProfile keeps startup
# fast; output is discarded — only "did it answer in time" matters.
_PROBE_COMMAND = (
    "Get-CimInstance -ClassName Win32_OperatingSystem "
    "-OperationTimeoutSec 4 | Out-Null"
)

# Runs ELEVATED. Stops the WMI service (forcefully if needed), kills any stuck
# provider hosts, starts it again, and restarts the dependent services that the
# forced stop took down. Bounded: sc.exe + taskkill don't hang on a wedged
# service the way a plain Stop-Service can.
_REPAIR_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$deps = (Get-Service -Name Winmgmt).DependentServices |
    Where-Object { $_.Status -eq 'Running' } | ForEach-Object { $_.Name }
sc.exe stop Winmgmt | Out-Null
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Service -Name Winmgmt).Status -ne 'Stopped' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
}
if ((Get-Service -Name Winmgmt).Status -ne 'Stopped') {
    # Service is stuck mid-stop: kill its host process hard. PID via sc.exe —
    # deliberately NOT a WMI query, which could itself hang here.
    $m = (sc.exe queryex Winmgmt | Select-String 'PID\s*:\s*(\d+)')
    if ($m) {
        $svcPid = [int]$m.Matches[0].Groups[1].Value
        if ($svcPid -gt 0) { taskkill /F /PID $svcPid | Out-Null }
    }
    Start-Sleep -Seconds 1
}
Get-Process -Name WmiPrvSE -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Service -Name Winmgmt
foreach ($d in $deps) { Start-Service -Name $d }
"""


def probe_wmi(timeout_s: float = 6.0) -> bool:
    """Return ``True`` if WMI answers a trivial query within ``timeout_s``.

    Runs in a subprocess so a wedged WMI can never block the caller. Returns
    ``True`` (healthy / nothing to repair) on non-Windows platforms.
    """
    if sys.platform != "win32":
        return True
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PROBE_COMMAND],
            capture_output=True,
            timeout=timeout_s,
            creationflags=_CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        # PowerShell missing/broken: can't tell — don't nag about repairs.
        return True


def repair_wmi(timeout_s: float = 90.0) -> bool:
    """Restart the WMI service (elevated; shows ONE UAC prompt). Returns
    ``True`` when WMI answers the probe afterwards.

    The elevated child runs :data:`_REPAIR_SCRIPT`; the wait is bounded by
    ``timeout_s`` so a stuck repair can never hang the caller.
    """
    if sys.platform != "win32":
        return True
    # -Verb RunAs (UAC) requires launching via Start-Process from a
    # non-elevated parent; -Wait makes the outer powershell block until the
    # elevated child finishes, keeping "repair finished" observable here.
    outer = (
        "$p = Start-Process -FilePath 'powershell' -Verb RunAs -WindowStyle Hidden "
        "-PassThru -Wait -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', "
        "'-EncodedCommand', '{encoded}'; exit $p.ExitCode"
    )
    import base64

    encoded = base64.b64encode(_REPAIR_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                outer.format(encoded=encoded),
            ],
            capture_output=True,
            timeout=timeout_s,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
    # The only verdict that matters: does WMI answer now?
    return probe_wmi()
