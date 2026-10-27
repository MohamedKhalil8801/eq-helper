"""Apply tunings to Equalizer APO (Windows only; hidden elsewhere — ADR 0002).

The customer picks their output device once. The app keeps exactly one
entry on that device, "Resonance EQ – <IEM>": a preset (managed by
:class:`~eq_helper.apo.presets.PresetLibrary`) holding one EQ file. Applying
a tuning rewrites that EQ file and makes the entry the device's active
preset; nothing else in ``config.txt`` changes. When Windows won't let the
app write APO's config folder, :class:`ApoUnwritable` carries a plain fix
instead of failing silently.
"""

from __future__ import annotations

from pathlib import Path

from eq_helper.apo.errors import ApoError, ApoNotFoundError
from eq_helper.apo.include_file import set_include_enabled
from eq_helper.apo.paths import MAIN_CONFIG_NAME, resolve_apo_config_dir
from eq_helper.apo.presets import PresetLibrary
from eq_helper.apo.writer import atomic_write_with_backup, edit_include_file, poke

SETTINGS_KEY = "apo"


def entry_name(iem_name: str) -> str:
    return f"Resonance EQ – {iem_name}"


class ApoUnwritable(Exception):
    def __init__(self, message: str, fix: str):
        super().__init__(message)
        self.fix = fix


def unwritable_fix(config_dir: Path) -> str:
    return (
        f"Windows didn't let this app change Equalizer APO's settings in:\n{config_dir}\n\n"
        "To fix it once, give “Users” permission to modify that folder: open it in File Explorer, "
        "right-click it → Properties → Security → Edit…, select “Users”, tick “Modify”, and press OK. "
        "Then press Apply again.\n\n"
        "Or: close this app, right-click it and choose “Run as administrator” once, then apply."
    )


class ApoApplier:
    def __init__(self, platform: str, settings, config_dir: Path | None = None):
        self.platform = platform
        self.settings = settings
        self._config_dir = Path(config_dir) if config_dir is not None else None
        state = settings.get(SETTINGS_KEY) or {}
        self.device: str | None = state.get("device")
        self.preset_path: str | None = state.get("preset_path")
        self.eq_path: str | None = state.get("eq_path")

    # ── availability ───────────────────────────────────────────────────────
    @property
    def config_dir(self) -> Path | None:
        if self._config_dir is not None:
            return self._config_dir if self._config_dir.is_dir() else None
        try:
            return resolve_apo_config_dir()
        except ApoNotFoundError:
            return None

    def available(self) -> bool:
        return self.platform == "windows" and self.config_dir is not None \
            and (self.config_dir / MAIN_CONFIG_NAME).exists()

    def _library(self) -> PresetLibrary:
        if not self.available():
            raise ApoNotFoundError("Equalizer APO isn't installed on this computer.")
        return PresetLibrary(self.config_dir)

    # ── device ─────────────────────────────────────────────────────────────
    def devices(self) -> list[str]:
        return self._library().list_devices()

    @staticmethod
    def no_devices_help() -> str:
        return ("Equalizer APO isn't set up for any output yet. Open “Equalizer APO Configurator” from the "
                "Start menu, tick your headphones' output, press OK and restart Windows. Then try again.")

    def set_device(self, device: str) -> None:
        if device != self.device:
            self.device = device
            self.preset_path = self.eq_path = None
            self._save()

    def _save(self) -> None:
        self.settings.set(SETTINGS_KEY, {"device": self.device, "preset_path": self.preset_path,
                                         "eq_path": self.eq_path})

    # ── applying ───────────────────────────────────────────────────────────
    def _managed(self, lib: PresetLibrary, iem_name: str):
        """Our preset on the chosen device (creating it and its EQ file once)."""
        presets = lib.list_presets(self.device)
        preset = next((p for p in presets if self.preset_path and Path(p.path) == Path(self.preset_path)), None)
        if preset is None:
            preset = next((p for p in presets if p.name == entry_name(iem_name)), None)
        if preset is None:
            preset = lib.create_preset(self.device, entry_name(iem_name))
        eq_files = lib.list_eq_files(preset)
        eq_file = next((e for e in eq_files if self.eq_path and Path(e.path) == Path(self.eq_path)), None)
        if eq_file is None:
            eq_file = eq_files[0] if eq_files else lib.create_eq_file(preset, entry_name(iem_name))
        self.preset_path, self.eq_path = preset.path, eq_file.path
        self._save()
        return preset, Path(eq_file.path)

    def apply(self, channel_eq, iem_name: str, tuning_text: str) -> Path:
        """Write ``channel_eq`` into the managed entry and make it active. Returns the EQ file."""
        if not self.device:
            raise ValueError("Choose the output device first.")
        lib = self._library()
        try:
            preset, eq_path = self._managed(lib, iem_name)
            text = (f"# {entry_name(iem_name)}\n# {tuning_text}\n"
                    "# Managed by the customer app: Apply replaces this file.\n" + channel_eq.apo_text())
            atomic_write_with_backup(eq_path, text)
            lib.activate(preset)
            poke(self.config_dir / MAIN_CONFIG_NAME)
        except (ApoError, OSError) as exc:
            raise ApoUnwritable(str(exc), unwritable_fix(self.config_dir)) from exc
        return eq_path

    def is_on(self) -> bool:
        if not (self.available() and self.device and self.preset_path):
            return False
        active = self._library().active_preset(self.device)
        return active is not None and Path(active.path) == Path(self.preset_path)

    def turn_off(self) -> None:
        """Disable the managed entry (the customer's sound goes back to no EQ)."""
        if not self.is_on():
            return
        lib = self._library()
        preset = next(p for p in lib.list_presets(self.device) if Path(p.path) == Path(self.preset_path))
        try:
            edit_include_file(self.config_dir / MAIN_CONFIG_NAME,
                              lambda t: set_include_enabled(t, preset.include_path, False))
            poke(self.config_dir / MAIN_CONFIG_NAME)
        except (ApoError, OSError) as exc:
            raise ApoUnwritable(str(exc), unwritable_fix(self.config_dir)) from exc
