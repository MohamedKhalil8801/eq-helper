"""Convolution impulse responses, packaged the way each kind of host wants.

Packaging variants (chosen per catalog entry):

* ``stereo_single_rate`` — one stereo WAV at the device rate (Equalizer APO,
  EasyEffects): channel 1 = left, channel 2 = right.
* ``per_rate_named`` — one stereo WAV per rate named ``<base> <rate>.wav`` so a
  CamillaDSP ``$samplerate$`` filename picks the right one; config snippet in
  the README.
* ``apo_rates`` — one stereo WAV per common rate plus ``<base> convolution.txt``,
  an Equalizer APO include file whose ``If: sampleRate == …`` blocks load the
  WAV matching the device rate (APO refuses a convolution file at another rate,
  so this keeps working when the device's rate changes).
* ``roon_zip`` — upload-ready zip: per-rate stereo WAVs plus one ``.cfg`` per
  rate, relative paths only.

Every rate is scaled by one shared gain rule (loudest channel peaks at
:data:`~eq_helper.dsp.convolution.PEAK_TARGET_DB`), so L/R balance and level
are the same at every rate.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from eq_helper.dsp import convolution as conv
from eq_helper.dsp.resonance import ChannelEQ

from .base import ExportMeta, prepend_readme, register

PACKAGINGS = ("stereo_single_rate", "per_rate_named", "apo_rates", "roon_zip")
APO_RATES = (44100, 48000, 88200, 96000, 176400, 192000)
CAMILLA_RATES = (44100, 48000, 88200, 96000)
ROON_RATES = (44100, 48000, 88200, 96000, 176400, 192000)


def _curves(channel_eq: ChannelEQ) -> dict:
    return {side: (ch.freqs, ch.correction_db) for side, ch in channel_eq.channels.items()}


def _check_rates(rates) -> list[int]:
    rates = list(dict.fromkeys(int(r) for r in rates))
    bad = [r for r in rates if r not in conv.SAMPLE_RATES]
    if bad or not rates:
        raise ValueError(f"Unsupported sample rate(s): {bad or 'none'}; choose from {conv.SAMPLE_RATES}.")
    return rates


def _render_named(curves, rates, phase, fmt, folder: Path, name_for) -> tuple[list[Path], list]:
    written, entries = [], []
    for rate in rates:
        for label, samples in conv.package(conv.render_channels(curves, rate, phase), "stereo"):
            name = name_for(rate)
            conv.write_wav(folder / name, samples, rate, fmt)
            written.append(folder / name)
            note = "both channels" if len(curves) == 1 else "left = channel 1, right = channel 2"
            entries.append((rate, name, note))
    return written, entries


def camilla_snippet(base_name: str, folder: Path) -> list[str]:
    path = (folder / f"{base_name} $samplerate$.wav").as_posix()
    return [
        "CamillaDSP (v2 or later) configuration snippet — the $samplerate$ token picks the file",
        "matching the running rate:",
        "",
        "filters:",
        "  resonance_left:",
        "    type: Conv",
        "    parameters:",
        "      type: Wav",
        f'      filename: "{path}"',
        "      channel: 0",
        "  resonance_right:",
        "    type: Conv",
        "    parameters:",
        "      type: Wav",
        f'      filename: "{path}"',
        "      channel: 1",
        "pipeline:",
        "  - type: Filter",
        "    channels: [0]",
        "    names: [resonance_left]",
        "  - type: Filter",
        "    channels: [1]",
        "    names: [resonance_right]",
    ]


def apo_include(base_name: str, rates, meta: ExportMeta) -> str:
    """Equalizer APO config that convolves with the file matching the running sample rate."""
    lines = [f"# {line}" for line in meta.header_lines()]
    lines += ["# Convolution: the file matching the device's sample rate is used.",
              "# Equalizer APO can't resample a convolution file, so each rate has its own."]
    for i, rate in enumerate(rates):
        lines += [f"{'If' if i == 0 else 'ElseIf'}: sampleRate == {rate}", f"Convolution: {base_name} {rate}.wav"]
    lines += ["EndIf:"]
    return "\n".join(lines) + "\n"


def roon_cfg(rate: int, wav_name: str) -> str:
    """Roon convolution config: stereo in/out, file channel 0 -> L, 1 -> R."""
    return "\n".join([f"{rate} 2 2 0", "0 0", "0 0",
                      wav_name, "0", "0.0", "0.0",
                      wav_name, "1", "1.0", "1.0"]) + "\n"


class ConvolutionFormat:
    id = "convolution"
    options_schema = {
        "packaging": {"choices": PACKAGINGS, "default": "stereo_single_rate"},
        "sample_rate": {"choices": conv.SAMPLE_RATES, "default": 48000},
        "sample_rates": {"choices": conv.SAMPLE_RATES},
        "phase": {"choices": conv.PHASES, "default": "minimum"},
        "format": {"choices": conv.FORMATS, "default": "pcm24"},
    }

    def export(self, channel_eq: ChannelEQ, options: dict, folder: Path, base_name: str,
               meta: ExportMeta) -> list[Path]:
        packaging = options.get("packaging", "stereo_single_rate")
        if packaging not in PACKAGINGS:
            raise ValueError(f"Unknown convolution packaging {packaging!r}.")
        phase = options.get("phase", "minimum")
        fmt = options.get("format", "pcm24")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        curves = _curves(channel_eq)

        if packaging == "stereo_single_rate":
            rates = _check_rates([options.get("sample_rate", 48000)])
            written = conv.render_set(curves, rates, "stereo", phase, fmt, folder, base_name)
            readme = conv.readme_path(folder, base_name)
            prepend_readme(readme, meta)
            return written + [readme]

        if packaging == "per_rate_named":
            rates = _check_rates(options.get("sample_rates", CAMILLA_RATES))
            written, entries = _render_named(curves, rates, phase, fmt, folder,
                                             lambda rate: f"{base_name} {rate}.wav")
            readme = conv.write_readme(folder, base_name, entries, phase=phase, fmt=fmt, layout="stereo")
            prepend_readme(readme, meta, camilla_snippet(base_name, folder))
            return written + [readme]

        if packaging == "apo_rates":
            rates = _check_rates(options.get("sample_rates", APO_RATES))
            written, entries = _render_named(curves, rates, phase, fmt, folder,
                                             lambda rate: f"{base_name} {rate}.wav")
            include = folder / f"{base_name} convolution.txt"
            include.write_text(apo_include(base_name, rates, meta), encoding="utf-8")
            readme = conv.write_readme(folder, base_name, entries, phase=phase, fmt=fmt, layout="stereo")
            prepend_readme(readme, meta, [
                "Equalizer APO: copy every file into Equalizer APO's config folder and add",
                f"“Include: {include.name}” to config.txt. It picks the file for the current sample rate."])
            return [include, *written, readme]

        # roon_zip: build in memory so no loose files are left beside the zip.
        rates = _check_rates(options.get("sample_rates", ROON_RATES))
        staging = folder / f".{base_name} roon-staging"
        staging.mkdir(parents=True, exist_ok=True)
        try:
            written, entries = _render_named(curves, rates, phase, fmt, staging,
                                             lambda rate: f"{base_name} {rate}.wav")
            readme = conv.write_readme(staging, base_name, entries, phase=phase, fmt=fmt, layout="stereo")
            prepend_readme(readme, meta, ["Roon: Settings > DSP Engine > Convolution > upload this zip as is."])
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
                for rate, wav in zip(rates, written):
                    z.write(wav, wav.name)
                    z.writestr(f"{base_name} {rate}.cfg", roon_cfg(rate, wav.name))
                z.write(readme, readme.name)
            target = folder / f"{base_name} Roon.zip"
            target.write_bytes(buffer.getvalue())
        finally:
            for p in staging.iterdir():
                p.unlink()
            staging.rmdir()
        return [target]


register(ConvolutionFormat())
