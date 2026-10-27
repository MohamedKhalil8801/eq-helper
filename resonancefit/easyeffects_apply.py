"""Apply tunings to EasyEffects (Linux, PipeWire) — the Linux counterpart of Equalizer APO.

EasyEffects runs a chain of effects on everything the computer plays. Applying
writes one output preset, "Resonance EQ - <IEM>", holding a single Convolver
whose impulse response is the tuning (left/right as a stereo file), and loads
it with ``easyeffects -l``. EasyEffects resamples the impulse response to the
running rate, so one file covers every rate.

Where EasyEffects keeps things (checked against its source, 2026-10):

* EasyEffects 8 (Qt): ``$XDG_DATA_HOME/easyeffects/{output,irs}`` (default
  ``~/.local/share/easyeffects``); it moves 7's ``~/.config/easyeffects``
  presets there on start.
* EasyEffects 7 (GTK): ``~/.config/easyeffects/{output,irs}``.
* Flatpak: the same under ``~/.var/app/com.github.wwmm.easyeffects/{data,config}``.

The preset goes to every location that applies, so any version finds it. A
preset names its impulse response by ``kernel-name`` (file stem, ``.irs`` =
WAV); ``kernel-path`` is added for old 7.x releases.

Loading a preset replaces the whole EasyEffects output chain (that's how its
presets work), so the customer's own effects there are switched off until
they pick their preset again. ``easyeffects -l`` started without a running
EasyEffects would load and quit, so when it isn't running the preset is only
written and the customer is told to pick it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from eq_helper.exports.formats.convolution import render_rates

FLATPAK_ID = "com.github.wwmm.easyeffects"
SETTINGS_KEY = "easyeffects"
IR_RATE = 48000  # EasyEffects resamples to the running rate


def preset_name(iem_name: str) -> str:
    safe = "".join(c for c in iem_name if c.isalnum() or c in " -_.()").strip() or "IEM"
    return f"Resonance EQ - {safe}"


class EasyEffectsProblem(Exception):
    def __init__(self, message: str, fix: str):
        super().__init__(message)
        self.fix = fix


@dataclass(frozen=True)
class Install:
    kind: str                 # "native" | "flatpak"
    roots: tuple[Path, ...]   # folders holding output/ and irs/
    command: tuple[str, ...]  # how to run it


def preset_json(name: str, kernel_path: Path, bypass: bool = False) -> dict:
    return {"output": {
        "blocklist": [],
        "plugins_order": ["convolver#0"],
        "convolver#0": {
            "bypass": bypass, "input-gain": 0.0, "output-gain": 0.0,
            "kernel-name": name, "kernel-path": str(kernel_path),
            "ir-width": 100, "autogain": False,  # keep the tuning's own level and L/R balance
            "dry": -100.0, "wet": 0.0,
            "sofa": {"azimuth": 0.0, "elevation": 0.0, "radius": 1.0},
        },
    }}


def _atomic_write(path: Path, data: bytes) -> None:
    """EasyEffects watches preset files: never let it see a half-written one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class EasyEffectsApplier:
    name = "EasyEffects"
    app_id = "easyeffects"
    uses_devices = False   # EasyEffects follows the output chosen in the system/EasyEffects
    device = None

    def __init__(self, platform: str, settings, home: Path | None = None, env=None, which=shutil.which,
                 run=subprocess.run):
        self.platform = platform
        self.settings = settings
        self.home = Path(home) if home is not None else Path.home()
        self.env = dict(os.environ if env is None else env)
        self.which, self.run = which, run

    # ── where it is ────────────────────────────────────────────────────────
    def installs(self) -> list[Install]:
        if self.platform != "linux":
            return []
        found = []
        if self.which("easyeffects"):
            data = Path(self.env.get("XDG_DATA_HOME") or self.home / ".local" / "share") / "easyeffects"
            legacy = Path(self.env.get("XDG_CONFIG_HOME") or self.home / ".config") / "easyeffects"
            roots = (data,) + ((legacy,) if (legacy / "output").is_dir() else ())
            found.append(Install("native", roots, ("easyeffects",)))
        app = self.home / ".var" / "app" / FLATPAK_ID
        flatpak_installed = any((base / FLATPAK_ID).is_dir() for base in (
            Path("/var/lib/flatpak/app"), self.home / ".local" / "share" / "flatpak" / "app"))
        if self.which("flatpak") and (app.is_dir() or flatpak_installed):
            legacy = app / "config" / "easyeffects"
            roots = (app / "data" / "easyeffects",) + ((legacy,) if (legacy / "output").is_dir() else ())
            found.append(Install("flatpak", roots, ("flatpak", "run", FLATPAK_ID)))
        return found

    def available(self) -> bool:
        return bool(self.installs())

    def unavailable_text(self) -> str:
        if self.platform == "linux":
            return ("EasyEffects isn't installed. Install it (Flathub or your distribution) to apply your EQ to "
                    "everything you play, or export for another app.")
        return "Applying directly isn't available on this system yet. Use “Export for my EQ app…” instead."

    # (same interface as Equalizer APO's, which has outputs to choose from)
    def devices(self) -> list[str]:
        return []

    def set_device(self, device: str) -> None:
        pass

    @staticmethod
    def no_devices_help() -> str:
        return ""

    # ── state ──────────────────────────────────────────────────────────────
    def _state(self) -> dict:
        return dict(self.settings.get(SETTINGS_KEY) or {})

    def is_on(self) -> bool:
        return bool(self._state().get("on"))

    @property
    def preset(self) -> str | None:
        return self._state().get("preset")

    def running(self) -> bool:
        try:
            result = self.run(["pgrep", "-x", "easyeffects"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    # ── applying ───────────────────────────────────────────────────────────
    def _write(self, name: str, channel_eq=None, bypass: bool = False) -> list[Path]:
        written = []
        installs = self.installs()
        if not installs:
            raise EasyEffectsProblem("EasyEffects isn't installed.", self.unavailable_text())
        try:
            for install in installs:
                for root in install.roots:
                    kernel = root / "irs" / f"{name}.irs"
                    if channel_eq is not None:
                        kernel.parent.mkdir(parents=True, exist_ok=True)
                        (rendered,) = render_rates(channel_eq, [IR_RATE], kernel.parent, f".{name} tmp")
                        os.replace(rendered, kernel)  # an .irs is a WAV under another name
                    preset = root / "output" / f"{name}.json"
                    _atomic_write(preset, json.dumps(preset_json(name, kernel, bypass), indent=4).encode("utf-8"))
                    written.append(preset)
        except OSError as exc:
            raise EasyEffectsProblem(str(exc), f"Couldn't write the EasyEffects preset: {exc}") from exc
        return written

    def _load(self, name: str) -> bool:
        """Load the preset into a running EasyEffects. False when it isn't running or refused."""
        if not self.running():
            return False
        for install in self.installs():
            try:
                result = self.run([*install.command, "-l", name], capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return True
        return False

    def apply(self, channel_eq, iem_name: str, tuning_text: str) -> Path:
        """Write the preset (and impulse response) and load it. Returns the preset file.
        ``self.loaded`` tells whether EasyEffects took it now (else the customer picks it)."""
        name = preset_name(iem_name)
        written = self._write(name, channel_eq)
        self.loaded = self._load(name)
        self.settings.set(SETTINGS_KEY, {"preset": name, "on": self.loaded, "tuning": tuning_text})
        return written[0]

    def turn_off(self) -> None:
        """Bypass our Convolver (the rest of EasyEffects is untouched)."""
        name = self.preset
        if not name:
            return
        self._write(name, None, bypass=True)
        self._load(name)
        state = self._state()
        state["on"] = False
        self.settings.set(SETTINGS_KEY, state)
