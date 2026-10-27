"""Render EQ correction curves to portable convolution impulse responses.

The exported file is the whole EQ: any convolution engine (Equalizer APO's
``Convolution`` filter, CamillaDSP, Roon, foobar2000, phone players) applies it
by multiplying, so the correction travels without a per-app filter dialect.

Two design choices are deliberate:

- **Magnitude only comes from the curve.** Gains are interpolated linearly in
  log frequency with flat extrapolation, exactly like Equalizer APO's
  ``GraphicEQ``, so the impulse response and the APO text file describe the same
  correction.
- **Phase is a free parameter.** Minimum phase is the exact inverse of a
  minimum-phase resonance and keeps the energy at the start of the file (no
  look-ahead, nothing to trim). Linear phase is flat-phase but needs half the
  file length as delay and pre-rings.

Both channels are scaled by one shared factor so the exported pair keeps its
relative level; per-channel normalisation would shift the stereo image.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np

SAMPLE_RATES: tuple[int, ...] = (44100, 48000, 88200, 96000, 176400, 192000)
PHASES: tuple[str, ...] = ("minimum", "linear")
FORMATS: tuple[str, ...] = ("pcm24", "f32", "pcm16")
LAYOUTS: tuple[str, ...] = ("stereo", "mono_pair", "mono")

# Same length in time at every rate, so the resolution in Hz stays constant.
BASE_TAPS_AT_48K = 8192
# Matches the APO text export: the shared preamp leaves this much headroom.
PEAK_TARGET_DB = -1.0

_SIDE_ORDER = ("L", "R", "M")

_FORMAT_NAMES = {"pcm16": "16-bit PCM", "pcm24": "24-bit PCM", "f32": "32-bit float"}
_CHANNEL_NOTES = {
    "stereo": "left = channel 1, right = channel 2",
    "mono_pair": {"L": "left channel", "R": "right channel"},
    "mono": "apply the same file to both channels",
}


def taps_for(sample_rate: int, base_taps: int = BASE_TAPS_AT_48K) -> int:
    """Odd tap count giving the same duration at any sample rate."""
    taps = int(round(base_taps * float(sample_rate) / 48000.0))
    return taps if taps % 2 else taps + 1


def latency_samples(phase: str, taps: int) -> int:
    """Look-ahead a host must compensate; minimum phase needs none."""
    if phase not in PHASES:
        raise ValueError(f"Unknown phase type: {phase!r}")
    return 0 if phase == "minimum" else (taps - 1) // 2


def _ordered_sides(channels: Mapping[str, np.ndarray]) -> list[str]:
    return [side for side in _SIDE_ORDER if side in channels]


def _curve_magnitude(freqs, gain_db, sample_rate: int, n_fft: int) -> np.ndarray:
    """Target magnitude on the positive FFT grid, flat outside the curve."""
    f = np.asarray(freqs, dtype=float)
    g = np.asarray(gain_db, dtype=float)
    if f.ndim != 1 or g.shape != f.shape or f.size < 2:
        raise ValueError("Correction curve needs matching, one-dimensional data.")
    order = np.argsort(f)
    f, g = f[order], g[order]
    keep = np.r_[True, np.diff(f) > 0]
    f, g = f[keep], g[keep]
    if not (np.isfinite(f).all() and np.isfinite(g).all()) or np.any(f <= 0):
        raise ValueError("Correction curve needs finite, positive frequencies.")
    grid = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    db = np.interp(np.log(np.maximum(grid, f[0])), np.log(f), g)
    return 10.0 ** (db / 20.0)


def _linear_phase(magnitude: np.ndarray, taps: int) -> np.ndarray:
    """Zero-phase inverse FFT, centred — a symmetric (Type I) FIR."""
    n_fft = 2 * (magnitude.size - 1)
    zero_phase = np.fft.irfft(magnitude, n=n_fft)
    half = taps // 2
    # Samples -half..+half; the negative half lives at the end of the buffer.
    return np.concatenate((zero_phase[-half:], zero_phase[:half + 1]))


def _minimum_phase(magnitude: np.ndarray, taps: int) -> np.ndarray:
    """Homomorphic (cepstral) minimum-phase transform of the same magnitude."""
    n_fft = 2 * (magnitude.size - 1)
    log_magnitude = np.log(np.maximum(magnitude, 1e-12))
    cepstrum = np.fft.irfft(log_magnitude, n=n_fft)
    fold = np.zeros(n_fft)
    fold[0] = 1.0
    fold[1:n_fft // 2] = 2.0
    fold[n_fft // 2] = 1.0
    causal = np.fft.irfft(np.exp(np.fft.rfft(cepstrum * fold)), n=n_fft)
    return causal[:taps].copy()


def _peak_magnitude(impulse: np.ndarray) -> float:
    """Largest |H(f)| of the finished (truncated) filter, densely sampled."""
    n_fft = 1 << max(12, int(np.ceil(np.log2(4 * impulse.size))))
    return float(np.abs(np.fft.rfft(impulse, n_fft)).max())


def design_channel(
    freqs,
    gain_db,
    sample_rate: int,
    phase: str = "minimum",
    *,
    base_taps: int = BASE_TAPS_AT_48K,
) -> np.ndarray:
    """One channel's impulse response, before the shared gain is applied."""
    if phase not in PHASES:
        raise ValueError(f"Unknown phase type: {phase!r}")
    if sample_rate not in SAMPLE_RATES:
        raise ValueError(f"Unsupported sample rate: {sample_rate}")
    taps = taps_for(sample_rate, base_taps)
    n_fft = 1 << max(15, int(np.ceil(np.log2(8 * taps))))
    magnitude = _curve_magnitude(freqs, gain_db, sample_rate, n_fft)
    return _linear_phase(magnitude, taps) if phase == "linear" else _minimum_phase(magnitude, taps)


def render_channels(
    curves: Mapping[str, tuple],
    sample_rate: int,
    phase: str = "minimum",
    *,
    base_taps: int = BASE_TAPS_AT_48K,
) -> dict[str, np.ndarray]:
    """Design every channel at one rate and scale them by a single factor.

    ``curves`` maps a channel side to ``(freqs, gain_db)``. The shared scalar
    sets the loudest channel's peak to :data:`PEAK_TARGET_DB`, so no preamp is
    needed in the host and the L/R balance of the correction survives.
    """
    if not curves:
        raise ValueError("No channels to render.")
    impulses = {
        side: design_channel(freqs, gain_db, sample_rate, phase, base_taps=base_taps)
        for side, (freqs, gain_db) in curves.items()
    }
    scale = 10.0 ** (PEAK_TARGET_DB / 20.0) / max(_peak_magnitude(ir) for ir in impulses.values())
    return {side: (ir * scale).astype(np.float64) for side, ir in impulses.items()}


def package(irs: Mapping[str, np.ndarray], layout: str = "stereo") -> list[tuple[str, np.ndarray]]:
    """Group rendered channels into ``(label, samples[n, channels])`` outputs.

    ``stereo`` interleaves L then R into one file (Equalizer APO maps those to
    the left and right channels). ``mono_pair`` writes one file per side for
    players that take a single impulse response. ``mono`` writes one channel,
    which such a player then applies to both sides.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown channel layout: {layout!r}")
    sides = _ordered_sides(irs)
    if len(sides) != len(irs):
        raise ValueError(f"Unknown channel side(s): {sorted(set(irs) - set(sides))}")
    if layout == "stereo":
        if len(sides) == 1:
            return [("", np.column_stack((irs[sides[0]], irs[sides[0]])))]
        if len(sides) != 2:
            raise ValueError("Stereo output needs one or two channels.")
        return [("", np.column_stack((irs["L"], irs["R"])))]
    if layout == "mono_pair":
        return [(side, irs[side][:, None]) for side in sides]
    if len(sides) != 1:
        raise ValueError("A single mono file cannot hold different L/R corrections.")
    return [("", irs[sides[0]][:, None])]


def write_wav(path: Path, samples: np.ndarray, sample_rate: int, fmt: str = "pcm24") -> None:
    """Write interleaved samples as a WAV file (PCM 16/24-bit or 32-bit float).

    ``scipy.io.wavfile`` cannot write 24-bit PCM, and 24-bit is the format the
    widest range of hardware and phone players accepts, so the RIFF header is
    built here for all three. Samples are expected in ``[-1, 1]``; integer
    formats are clipped at full scale.
    """
    if fmt not in FORMATS:
        raise ValueError(f"Unknown sample format: {fmt!r}")
    data = np.asarray(samples, dtype=float)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] not in (1, 2) or data.shape[0] == 0:
        raise ValueError("Samples must be a non-empty mono or stereo array.")
    channels = int(data.shape[1])

    if fmt == "f32":
        format_code, bits = 3, 32
        payload = np.asarray(data, dtype="<f4").tobytes()
    else:
        bits = 16 if fmt == "pcm16" else 24
        format_code = 1
        full = 2 ** (bits - 1)
        ints = np.clip(np.rint(data * full), -full, full - 1).astype(np.int32)
        if bits == 16:
            payload = ints.astype("<i2").tobytes()
        else:
            flat = ints.reshape(-1)
            raw = np.empty((flat.size, 3), dtype=np.uint8)
            raw[:, 0] = flat & 0xFF
            raw[:, 1] = (flat >> 8) & 0xFF
            raw[:, 2] = (flat >> 16) & 0xFF
            payload = raw.tobytes()

    block_align = channels * bits // 8
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH", 16, format_code, channels, sample_rate,
            sample_rate * block_align, block_align, bits,
        )
        + b"data"
        + struct.pack("<I", len(payload))
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + payload)


def file_suffix(sample_rate: int, label: str = "") -> str:
    """``48kHz`` / ``44.1kHz`` plus an optional side label, for file names."""
    khz = sample_rate / 1000.0
    text = f"{khz:g}kHz"
    return f"{text} {label}".strip()


def file_name(base_name: str, sample_rate: int, label: str = "") -> str:
    """Name of one impulse response inside an export folder."""
    return f"{base_name} {file_suffix(sample_rate, label)}.wav"


def readme_path(folder: Path, base_name: str) -> Path:
    """The note that travels with an exported set."""
    return Path(folder) / f"{base_name} README.txt"


def write_readme(
    folder: Path,
    base_name: str,
    entries: Iterable[tuple[int, str, str]],
    *,
    phase: str,
    fmt: str,
    layout: str = "stereo",
) -> Path:
    """Document an exported set: rates, phase, gain and how to load it.

    ``entries`` are ``(sample_rate, file_name, channel_note)``. The note is the
    part a recipient cannot infer from the file, so it states which channel of
    a stereo file belongs to which ear and which files belong to one ear.
    """
    entries = list(entries)
    if not entries:
        raise ValueError("No files to document.")
    if phase not in PHASES:
        raise ValueError(f"Unknown phase type: {phase!r}")
    if fmt not in FORMATS:
        raise ValueError(f"Unknown sample format: {fmt!r}")
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown channel layout: {layout!r}")

    grouped: dict[int, list[tuple[str, str]]] = {}
    for rate, name, note in entries:
        grouped.setdefault(int(rate), []).append((name, note))
    rates = sorted(grouped)
    taps = ", ".join(f"{taps_for(rate)} at {rate / 1000:g} kHz" for rate in rates)

    lines = [
        "Resonance correction - convolution impulse responses",
        "",
        "Each .wav listed below is a finite impulse response that applies this",
        "correction. Load one file in the convolution engine of your player or",
        "DSP; no other setup is needed.",
        "",
        "Files",
    ]
    for rate in rates:
        lines.append(f"  {rate / 1000:g} kHz, {_FORMAT_NAMES[fmt]}")
        for name, note in grouped[rate]:
            lines.append(f"    {name}")
            if note:
                lines.append(f"      {note}")

    lines += ["", "Phase"]
    if phase == "minimum":
        lines += [
            "  Minimum phase.",
            f"  Taps: {taps}.",
            "  The impulse begins at sample 0 with no look-ahead, so nothing has to",
            "  be delayed or trimmed. Because the correction is the inverse of the",
            "  measured resonances, it cancels their peaks and the group delay that",
            "  comes with them.",
        ]
    else:
        delay_ms = latency_samples("linear", taps_for(rates[-1])) / rates[-1] * 1000.0
        lines += [
            "  Linear phase.",
            f"  Taps: {taps}.",
            "  Every frequency is delayed equally, but a symmetric filter works half",
            f"  its length ahead: {delay_ms:.1f} ms of latency. Compensate that in the",
            "  player's delay or lip-sync setting; for video it is usually too much.",
        ]

    lines += [
        "",
        "Level",
        f"  One gain for the whole set, peaking at {PEAK_TARGET_DB:g} dB; the left/right",
        "  balance is unchanged. Do not peak-normalise the files separately, and no",
        "  preamp is needed.",
        "",
        "Sample rate",
        "  A file only works at the rate in its name; convolvers do not resample.",
        "  Match the rate your device runs at and switch files when it changes.",
        "",
        "Using the files",
    ]
    if layout == "mono_pair":
        lines += [
            "  Equalizer APO   Channel: L",
            "                  Convolution: <left file>.wav",
            "                  Channel: R",
            "                  Convolution: <right file>.wav",
            "                  One block per file. A relative path is resolved next",
            "                  to the configuration file.",
        ]
    elif layout == "mono":
        lines += [
            "  Equalizer APO   Convolution: <file>.wav",
            "                  One line and no Channel: selector, so the filter is",
            "                  applied to both outputs. A relative path is resolved",
            "                  next to the configuration file.",
        ]
    else:
        lines += [
            "  Equalizer APO   Convolution: <file>.wav",
            "                  One line and no Channel: selector; channel 1 reaches",
            "                  the left output and channel 2 the right. A relative",
            "                  path is resolved next to the configuration file.",
        ]
    lines += [
        "  CamillaDSP      Conv filter reading the .wav directly",
        "  foobar2000      Convolver component",
        "  Other players   any FIR convolver that accepts WAV impulse responses",
        "",
        "Regenerating",
        "  Exporting again with the same name overwrites these files.",
    ]

    target = readme_path(folder, base_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def render_set(
    curves: Mapping[str, tuple],
    sample_rates: Iterable[int],
    layout: str,
    phase: str,
    fmt: str,
    folder: Path,
    base_name: str,
) -> list[Path]:
    """Design, package and write every requested rate; returns the files made.

    Each rate's pair is normalised to the same peak, so changing a player's
    output rate does not change the listening level. A README describing the
    set is written beside the impulse responses.
    """
    rates = list(dict.fromkeys(int(rate) for rate in sample_rates))
    if not rates:
        raise ValueError("Select at least one sample rate.")
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"Output folder not found: {folder}")

    rendered = {rate: render_channels(curves, rate, phase) for rate in rates}
    written: list[Path] = []
    entries: list[tuple[int, str, str]] = []
    for rate in rates:
        for label, samples in package(rendered[rate], layout):
            name = file_name(base_name, rate, label)
            path = folder / name
            write_wav(path, samples, rate, fmt)
            written.append(path)
            note = _CHANNEL_NOTES[layout]
            if isinstance(note, dict):
                note = note.get(label, "")
            elif len(curves) == 1:
                note = "both channels"
            entries.append((rate, name, note))
    write_readme(folder, base_name, entries, phase=phase, fmt=fmt, layout=layout)
    return written
