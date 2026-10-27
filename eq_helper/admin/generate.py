"""What "Generate customer app…" does, without Qt widgets.

1. Read the measurement sets (one folder with L/R detection, or separate
   L and R folders) and keep the fits the model can use.
2. Write the ``.rescal`` calibration file.
3. Draw the calibration sheet PDF.
4. Package the prebuilt app for each chosen OS — or only the calibration
   file (recalibration).
5. Save the record in the customer library (remembering operator accessories).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import numpy as np

from eq_helper.calibration.rescal import Calibration, Manifest, write_rescal
from eq_helper.dsp.resonance import Fit, Measurements, read_fits

from .calibration_sheet import sheet_file_name, write_calibration_sheet
from .customer_library import CalibrationRecord, CustomerLibrary, MeasurementSource
from .packager import calibration_only, package, rescal_name

MIN_FITS_PER_SIDE = 3
# Not coupler targets (no ear gain): useful in EQ Helper as diagnostics, but a trap for a
# customer — against raw IEC-711 predictions a flat target cuts ~10 dB around 3 kHz.
DIAGNOSTIC_TARGETS = ("Flat Line.txt",)
SIDE_NAMES = {"L": "Left", "R": "Right", "M": "Both ears"}


def load_measurement_sets(source: MeasurementSource) -> tuple[list[Fit], list[str]]:
    """Fits from the operator's folders, with L/R decided by file/folder names
    (one folder) or by which folder they're in (separate L and R folders)."""
    if source.mode == "folder":
        return read_fits(source.folder)
    fits, warnings = [], []
    for side, folder in (("L", source.left), ("R", source.right)):
        side_fits, side_warnings = read_fits(folder)
        fits += [replace(f, side=side) for f in side_fits]
        warnings += [f"{SIDE_NAMES[side]}: {w}" for w in side_warnings]
    return fits, warnings


@dataclass
class Preview:
    measurements: Measurements
    fits: list[Fit]
    sides: dict[str, int]
    peak_range: dict[str, tuple[float, float]]
    warnings: list[str]

    def warnings_below_three(self) -> list[str]:
        return [f"{SIDE_NAMES[s]} has only {n} fit{'s' if n != 1 else ''}; measure at least "
                f"{MIN_FITS_PER_SIDE} for a reliable model." for s, n in self.sides.items() if n < MIN_FITS_PER_SIDE]

    def summary(self) -> str:
        parts = []
        for side, count in self.sides.items():
            lo, hi = self.peak_range[side]
            parts.append(f"{SIDE_NAMES[side]}: {count} fits, primary resonance {lo:,.0f}–{hi:,.0f} Hz")
        return "; ".join(parts)


def preview(source: MeasurementSource, name: str = "preview") -> Preview:
    """Load and check the measurement sets; raises ValueError with a plain reason."""
    fits, warnings = load_measurement_sets(source)
    measurements = Measurements.from_fits(fits, name)
    used = {(side, src) for side, model in measurements.models.items() for src in model.sources}
    kept = [f for f in fits if ((f.side or "M"), f.name) in used]
    sides = {side: sum(1 for f in kept if (f.side or "M") == side) for side in measurements.models}
    ranges = {side: (float(np.min(m.peaks)), float(np.max(m.peaks))) for side, m in measurements.models.items()}
    return Preview(measurements, kept, sides, ranges, list(warnings) + list(measurements.warnings))


@dataclass
class GenerateRequest:
    manifest: Manifest
    source: MeasurementSource
    targets: list[Path]
    systems: list[str]              # [] = calibration file only
    output_dir: Path
    record_id: str | None = None    # set when regenerating an existing record


@dataclass
class GenerateResult:
    outputs: list[Path] = field(default_factory=list)
    record: CalibrationRecord | None = None
    warnings: list[str] = field(default_factory=list)


def generate(request: GenerateRequest, library: CustomerLibrary, cache, branding,
             progress: Callable[[str], None] | None = None) -> GenerateResult:
    say = progress or (lambda _msg: None)
    m = request.manifest
    targets = [Path(t) for t in request.targets]
    if m.default_target not in {t.name for t in targets}:
        raise ValueError(f"The default target {m.default_target!r} must be one of the targets included.")
    say("Reading measurements…")
    checked = preview(request.source, m.iem.display_name)
    calibration = Calibration(m, checked.fits)
    out = Path(request.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = GenerateResult(warnings=checked.warnings_below_three() + checked.warnings)

    with tempfile.TemporaryDirectory(prefix="rescal-") as tmp:
        tmp = Path(tmp)
        say("Writing the calibration file…")
        rescal = write_rescal(tmp / rescal_name(m), calibration)
        say("Drawing the calibration sheet…")
        sheet = write_calibration_sheet(tmp / sheet_file_name(m, branding), calibration, branding,
                                        checked.measurements)
        result.outputs += calibration_only(rescal, sheet, out, branding, m)
        for system in request.systems:
            say(f"Getting the {system} app…")
            build = cache.resolve(system)
            say(f"Packaging for {system}…")
            result.outputs.append(package(system, build, rescal, sheet, targets, out, branding, m))

    say("Saving to the customer library…")
    result.record = library.save(m, request.source, result.outputs, [str(t) for t in targets],
                                 request.systems, record_id=request.record_id)
    say(f"Done: {len(result.outputs)} files in {out}")
    return result
