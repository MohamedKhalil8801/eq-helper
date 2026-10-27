"""Learn insertion-response families from measurements, then match an APO target.

No IEM-specific peak coordinates, response shapes, or fitted coefficients live
here. Folder and bundled inputs take the same peak-detection/training path.
"""

from dataclasses import dataclass, replace
from pathlib import Path
import re

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.signal import find_peaks

from eq_helper.dsp.graphic_eq import GraphicEQ, GraphicEQBand, graphic_eq_to_apo_text
from eq_helper.dsp.overlay import load_frequency_response

PEAK_RANGE = (5000.0, 11000.0)
CONTROL_RANGE = (6000, 9000)
PERSONAL_TREBLE_RANGE = (10500, 18000)
PERSONAL_TREBLE_MAX_WARP = 1.4
# Local shoulders around the source/destination anchors, not the whole treble.
PERSONAL_TREBLE_SHOULDER_OCTAVES = 1 / 6


def interp(freqs, db, grid):
    return np.interp(np.log(grid), np.log(freqs), db)


def relative(freqs, db):
    """Normalize targets only; measurements share one reference across ears."""
    return db - float(interp(freqs, db, np.array([1000.0]))[0])


def _validate(freqs, db, name):
    f, y = np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)
    if (f.ndim != 1 or y.shape != f.shape or len(f) < 2
            or not np.isfinite(f).all() or not np.isfinite(y).all()
            or np.any(f <= 0) or np.any(np.diff(f) <= 0)):
        raise ValueError(f"{name}: needs finite levels and strictly increasing positive frequencies.")
    return f, y


def detect_high_treble_peak(freqs, db, primary_peak_hz):
    """Find the strongest resolved resonance above the primary treble peak."""
    f, y = np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)
    if (f.ndim != 1 or y.shape != f.shape or len(f) < 3
            or not np.isfinite(f).all() or not np.isfinite(y).all()
            or np.any(f <= 0) or np.any(np.diff(f) <= 0)):
        raise ValueError("A valid, increasing coupler curve is required for peak detection.")
    try:
        primary = float(primary_peak_hz)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Primary resonance must be a valid frequency.") from exc
    if not np.isfinite(primary) or primary <= 0:
        raise ValueError("Primary resonance must be a valid frequency.")
    lower = max(float(PERSONAL_TREBLE_RANGE[0]), primary * 1.3)
    mask = (f >= lower) & (f <= min(float(f[-1]), 20000.0))
    if np.count_nonzero(mask) < 3:
        return None
    indices, _ = find_peaks(y[mask], prominence=0.5)
    if not len(indices):
        return None
    candidates = np.flatnonzero(mask)[indices]
    index = candidates[np.argmax(y[candidates])]
    return float(f[index])


def detect_peak(freqs, db):
    """Find the largest resolved treble maximum, not the largest edge sample.

    Detection tolerates the rounded SPL plateaus in REW exports. Magnitudes
    used for prediction/EQ are never smoothed.
    """
    mask = (freqs >= PEAK_RANGE[0]) & (freqs <= PEAK_RANGE[1])
    f, y = freqs[mask], db[mask]
    indices, _ = find_peaks(y, prominence=0.5)
    if not len(indices):
        raise ValueError("No resolved resonance peak in 5–11 kHz (at least 0.5 dB prominence).")
    i = indices[np.argmax(y[indices])]
    return float(f[i])


def _side_token(text):
    hits = set(re.findall(r"(?:^|[^a-z])(left|right|l|r)(?=$|[^a-z])", text.lower()))
    sides = {"L" if h in ("l", "left") else "R" for h in hits}
    if len(sides) > 1:
        raise ValueError(f"Ambiguous side label: {text}")
    return next(iter(sides), None)


def _file_side(path, root):
    side = _side_token(path.stem)
    if side:
        return side
    # REW's Source input-channel field is NOT the measured ear. Use its name.
    with path.open(encoding="utf-8", errors="replace") as stream:
        header = stream.read(4096)
    match = re.search(r"^\*\s*Measurement:\s*(.+)$", header, re.MULTILINE)
    if match:
        side = _side_token(match.group(1))
        if side:
            return side
    for parent in [path.parent, *path.parent.parents]:
        side = _side_token(parent.name)
        if side:
            return side
        if parent == root:
            break
    return None


class ResonanceModel:
    """One ear's response family, trained and validated at construction."""

    def __init__(self, freqs, curves, peaks, sources=()):
        self.freqs = np.asarray(freqs, dtype=float)
        order = np.argsort(peaks)
        peaks, curves = np.asarray(peaks)[order], np.asarray(curves)[order]
        self.sources = tuple(sources[i] for i in order) if len(sources) else ()
        self.sample_count = len(peaks)
        # Average repeated fits with indistinguishable peaks, avoiding zero
        # interpolation denominators and learning noise as a depth trend.
        groups = []
        for i, peak in enumerate(peaks):
            if groups and np.log(peak / peaks[groups[-1][0]]) < 0.0005:
                groups[-1].append(i)
            else:
                groups.append([i])
        self.peaks = np.array([np.exp(np.mean(np.log(peaks[g]))) for g in groups])
        self.curves = np.array([np.mean(curves[g], axis=0) for g in groups])
        self.alignment = 0.0
        self.edge_trend = 0.0
        self.validation = {}
        self._train()

    @property
    def method(self):
        return {0.0: "local blend", 0.5: "part-aligned blend", 1.0: "peak-aligned blend"}[self.alignment]

    def _warp(self, curve, source_peak, target_peak):
        # A monotone, smooth log-frequency map avoids folds for these ranges.
        # Bass/mids below 3 kHz remain in place; above the peak use scaling.
        start = min(3000.0, source_peak / 2, target_peak / 2)
        x = np.log(self.freqs)
        a, b, c = np.log([start, target_peak, source_peak])
        ramp = np.clip((x - a) / (b - a), 0, 1)
        ramp = ramp * ramp * (3 - 2 * ramp)
        query = x + ramp * (c - b)
        return interp(self.freqs, curve, np.exp(query))

    def _position_peak(self, curve, peak):
        for _ in range(2):
            try:
                anchor = detect_peak(self.freqs, curve)
            except ValueError:
                region = (self.freqs >= peak * 0.9) & (self.freqs <= peak * 1.1)
                anchor = self.freqs[np.flatnonzero(region)[np.argmax(curve[region])]]
            curve = self._warp(curve, anchor, peak)
        return curve

    def _predict(self, peak, peaks, curves, alignment, edge_trend):
        exact = np.flatnonzero(np.isclose(peaks, peak, rtol=1e-10))
        if len(exact):
            return curves[exact[0]].copy()
        if len(peaks) == 1:
            return self._warp(curves[0], peaks[0], peak)
        if peak < peaks[0] or peak > peaks[-1]:
            index = 0 if peak < peaks[0] else len(peaks) - 1
            result = self._warp(curves[index], peaks[index], peak)
            if edge_trend:
                indices = np.arange(min(3, len(peaks))) if index == 0 else np.arange(max(0, len(peaks) - 3), len(peaks))
                x = np.log(peaks[indices])
                x -= np.mean(x)
                aligned = np.array([self._warp(curves[i], peaks[i], peak) for i in indices])
                slope = np.sum(x[:, None] * aligned, axis=0) / np.sum(x * x)
                span = np.log(peaks[-1] / peaks[0])
                distance = span * np.tanh(np.log(peak / peaks[index]) / span)
                result += edge_trend * slope * distance
            return self._position_peak(result, peak)
        hi = int(np.searchsorted(peaks, peak))
        lo = hi - 1
        t = np.log(peak / peaks[lo]) / np.log(peaks[hi] / peaks[lo])
        aligned = []
        for i in (lo, hi):
            anchor = np.exp((1 - alignment) * np.log(peaks[i]) + alignment * np.log(peak))
            aligned.append(self._warp(curves[i], peaks[i], anchor))
        return self._position_peak((1 - t) * aligned[0] + t * aligned[1], peak)

    def _error(self, predicted, actual):
        # Equal log-frequency weighting within each band, with extra weight
        # for the moving resonance. No smoothing of the response itself.
        f = np.geomspace(self.freqs[0], self.freqs[-1], 2048)
        residual = interp(self.freqs, predicted - actual, f)
        errors = []
        for lo, hi in [(20, 3000), (3000, 6000), (6000, 10000), (10000, 20001)]:
            mask = (f >= lo) & (f < hi)
            errors.append(float(np.mean(residual[mask] ** 2)) if mask.any() else 0.0)
        return float(np.sqrt(np.average(errors, weights=[1, 1, 2, 1]))), float(np.sqrt(errors[2]))

    def _train(self):
        n = len(self.peaks)
        if n < 3:
            return
        def evaluate(indices, alignment, trend):
            errors = []
            for i in indices:
                keep = np.arange(n) != i
                prediction = self._predict(self.peaks[i], self.peaks[keep], self.curves[keep], alignment, trend)
                errors.append(self._error(prediction, self.curves[i]))
            return np.sqrt(np.mean(np.square(errors), axis=0))
        # A single interior holdout is insufficient to choose model complexity.
        candidates = (0.0, 0.5, 1.0) if n >= 4 else (0.0,)
        scores = {a: evaluate(range(1, n - 1), a, 0.0) for a in candidates}
        self.alignment = min(scores, key=lambda a: scores[a][0])
        self.validation["interpolation_rms_db"] = float(scores[self.alignment][0])
        self.validation["interpolation_treble_rms_db"] = float(scores[self.alignment][1])
        edges = {t: evaluate([0, n - 1], self.alignment, t) for t in (0.0, 0.5, 1.0)}
        best = min(edges, key=lambda t: edges[t][0])
        # Prefer nearest-fit warping unless an edge trend demonstrably helps.
        self.edge_trend = best if edges[best][0] < edges[0.0][0] * 0.95 else 0.0
        self.validation["edge_rms_db"] = float(edges[self.edge_trend][0])
        self.validation["candidates_rms_db"] = {str(a): float(v[0]) for a, v in scores.items()}

    def predict(self, peak_hz):
        p = float(peak_hz)
        if not np.isfinite(p) or not PEAK_RANGE[0] <= p <= PEAK_RANGE[1]:
            raise ValueError("Resonance must be between 5,000 and 11,000 Hz.")
        return self._predict(p, self.peaks, self.curves, self.alignment, self.edge_trend)

    def describe(self, peak_hz):
        if len(self.peaks) == 1:
            state = "single fit; frequency-warp estimate"
        elif self.peaks[0] <= peak_hz <= self.peaks[-1]:
            state = "interpolated"
        else:
            state = "EXTRAPOLATED"
        return f"{state} · measured {self.peaks[0]:.0f}–{self.peaks[-1]:.0f} Hz"


@dataclass(frozen=True)
class Fit:
    """One coupler measurement at one insertion depth, held in memory.

    ``side`` is ``"L"``, ``"R"`` or ``None`` (unlabelled). Levels are raw SPL;
    a shared 1 kHz reference across all fits is applied when models are built.
    """

    side: str | None
    name: str
    freqs: np.ndarray
    db: np.ndarray

    def __post_init__(self):
        if self.side not in ("L", "R", None):
            raise ValueError(f"Fit side must be 'L', 'R' or None, not {self.side!r}.")


def read_fits(folder):
    """Read every TXT/CSV fit below ``folder`` with its L/R label.

    Returns ``(fits, warnings)``; unreadable files become warnings. Values are
    not validated here, so in-memory and on-disk fits share one validation path.
    """
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError(f"Measurement folder not found: {root}")
    fits, warnings = [], []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".txt", ".csv"):
            continue
        name = str(path.relative_to(root))
        try:
            side = _file_side(path, root)
            freqs, db = load_frequency_response(path)
            fits.append(Fit(side, name, np.asarray(freqs, dtype=float), np.asarray(db, dtype=float)))
        except (OSError, ValueError) as exc:
            warnings.append(f"Skipped {name}: {exc}")
    return fits, warnings


@dataclass
class Measurements:
    models: dict[str, ResonanceModel]
    name: str
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_folder(cls, folder):
        fits, warnings = read_fits(folder)
        return cls._build(fits, str(Path(folder).resolve()), warnings, "in this folder")

    @classmethod
    def from_fits(cls, fits, name):
        """Build models from in-memory fits (e.g. from a calibration file)."""
        return cls._build(fits, name, [], "in this calibration")

    @classmethod
    def _build(cls, fits, name, warnings, where):
        records, warnings = [], list(warnings)
        for fit in fits:
            try:
                f, y = _validate(fit.freqs, fit.db, fit.name)
                if f[0] > 1000 or f[-1] < PEAK_RANGE[1]:
                    raise ValueError("Needs coverage from 1 kHz through 11 kHz.")
                peak = detect_peak(f, y)
                records.append((fit.side, fit.name, f, y, peak))
            except ValueError as exc:
                warnings.append(f"Skipped {fit.name}: {exc}")
        if not records:
            raise ValueError(f"No usable measurements {where}.\n" + "\n".join(warnings[:8]))
        labelled = any(r[0] is not None for r in records)
        if labelled:
            warnings.extend(f"Skipped {r[1]}: no L/R label. Use L/R filenames or Left/Right subfolders."
                            for r in records if r[0] is None)
            records = [r for r in records if r[0] is not None]
        else:
            warnings.append("No L/R labels: treated as one measurement set; its EQ applies to both channels.")
        reference = float(np.median([interp(r[2], r[3], [1000])[0] for r in records]))
        models = {}
        for side in ("L", "R", None):
            samples = [r for r in records if r[0] == side]
            if not samples:
                continue
            lo = max(20.0, max(r[2][0] for r in samples))
            hi = min(20000.0, min(r[2][-1] for r in samples))
            densest = max(samples, key=lambda r: np.count_nonzero((r[2] >= lo) & (r[2] <= hi)))[2]
            # Keep the densest native grid rather than decimating high-res data
            # or multiplying grid size when sample rates differ.
            grid = np.unique(np.r_[densest[(densest >= lo) & (densest <= hi)], lo, hi, 1000.0])
            curves = np.array([interp(r[2], r[3], grid) - reference for r in samples])
            models[side or "M"] = ResonanceModel(grid, curves, [r[4] for r in samples], [r[1] for r in samples])
        return cls(models, name, tuple(warnings))

    @classmethod
    def bundled(cls):
        with np.load(Path(__file__).with_name("nova_w1.npz"), allow_pickle=False) as data:
            models = {}
            for side in sorted(set(data["sides"].tolist())):
                mask = data["sides"] == side
                curves = data["curves"][mask]
                peaks = [detect_peak(data["freqs"], curve) for curve in curves]
                models[side] = ResonanceModel(data["freqs"], curves, peaks,
                                               data["sources"][mask].tolist())
        return cls(models, "Bundled Nova / W1 measurements")


@dataclass
class ResonanceEQ:
    peak_hz: float
    target_name: str
    freqs: np.ndarray
    predicted_db: np.ndarray
    target_db: np.ndarray
    correction_db: np.ndarray
    preamp_db: float
    prediction_note: str = ""
    personal_peak_hz: float | None = None
    coupler_treble_peak_hz: float | None = None
    applied_personal_peak_hz: float | None = None
    personal_treble_band_hz: tuple[float, float] | None = None

    def graphic_text(self):
        geq = GraphicEQ(bands=[GraphicEQBand(float(f), float(g))
                              for f, g in zip(self.freqs, self.correction_db)])
        return graphic_eq_to_apo_text(geq) + "\n"

    def apo_text(self, enabled=True):
        return ChannelEQ({"M": self}, self.target_name, self.preamp_db,
                         self.personal_peak_hz is not None).apo_text(enabled)


@dataclass
class ChannelEQ:
    channels: dict[str, ResonanceEQ]
    target_name: str
    preamp_db: float
    personal_treble_enabled: bool = False

    def apo_text(self, enabled=True):
        target = self.target_name.replace("\n", " ").replace("\r", " ")
        header = (f"# Measurement-trained resonance EQ; target: {target}\n"
                  "# Unsmoothed target minus prediction; shared measurement level reference.\n"
                  "Channel: ALL\n"
                  f"Preamp: {self.preamp_db:.6f} dB\n")
        if self.personal_treble_enabled:
            header += "# Personal higher-treble fit is optional and affects EQ correction only.\n"
        if not enabled:
            return header + "# EQ bypassed; same shared preamp for comparison.\n"
        stereo = len(self.channels) == 2
        for side, eq in self.channels.items():
            channel = side if stereo else "ALL"
            header += f"# {side}: estimated resonance {eq.peak_hz:.0f} Hz; {eq.prediction_note}\n"
            if eq.personal_peak_hz is not None:
                header += ("# Personal treble correction alignment: coupler peak "
                           f"{eq.coupler_treble_peak_hz:.0f} Hz -> correction anchor "
                           f"{eq.applied_personal_peak_hz:.0f} Hz; requested "
                           f"{eq.personal_peak_hz:.0f} Hz (prediction unchanged).\n")
                if eq.personal_treble_band_hz is not None:
                    low, high = eq.personal_treble_band_hz
                    header += (f"# Local correction warp only in {low:.0f}-{high:.0f} Hz; "
                               "outside this band the original EQ is unchanged.\n")
                if eq.applied_personal_peak_hz != eq.personal_peak_hz:
                    header += "# Alignment limited by supported frequency range / maximum warp.\n"
            header += f"Channel: {channel}\n"
            header += eq.graphic_text()
        return header + "Channel: ALL\n"


def match_target(model, peak_hz, target_freqs, target_db, target_name):
    target_freqs, target_db = _validate(target_freqs, target_db, "Target")
    lo = max(float(model.freqs[0]), float(target_freqs[0]), 20.0)
    hi = min(float(model.freqs[-1]), float(target_freqs[-1]), 20000.0)
    if not lo <= 1000 <= hi:
        raise ValueError("Target and measurements must overlap at 1 kHz so levels can be aligned.")
    grid = np.unique(np.concatenate([
        model.freqs[(model.freqs >= lo) & (model.freqs <= hi)],
        target_freqs[(target_freqs >= lo) & (target_freqs <= hi)],
        [lo, hi, 1000.0],
    ]))
    prediction = interp(model.freqs, model.predict(peak_hz), grid)
    target = interp(target_freqs, relative(target_freqs, target_db), grid)
    gain = target - prediction
    return ResonanceEQ(float(peak_hz), target_name, grid, prediction, target, gain,
                       -max(0.0, float(np.max(gain))) - 1.0, model.describe(peak_hz))


def adjust_personal_treble(eq, primary_peak_hz, personal_peak_hz):
    """Optionally shift a personal high-treble EQ feature, not the model fit.

    A monotone log-frequency map moves the correction anchor between local
    shoulders surrounding the detected and heard higher peak. Everything
    outside that band, including the primary resonance, stays exactly unchanged.
    Magnitudes are resampled from native data, never fitted or smoothed.
    """
    if personal_peak_hz is None:
        return eq
    try:
        primary, personal = float(primary_peak_hz), float(personal_peak_hz)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Personal treble peak must be a valid frequency.") from exc
    lo, hi = PERSONAL_TREBLE_RANGE
    if (not np.isfinite(primary) or primary <= 0 or not np.isfinite(personal)
            or not lo <= personal <= hi):
        raise ValueError(f"Personal treble peak must be between {lo:,} and {hi:,} Hz.")
    x = np.log(eq.freqs)
    coverage_start = max(primary, float(eq.freqs[0]))
    coverage_end = min(float(eq.freqs[-1]), 20000.0)
    # Locate the coupler's own higher resonance; the personal coordinate is
    # the destination, not a replacement for the prediction or source curve.
    if not coverage_start < coverage_end:
        return eq
    feature = detect_high_treble_peak(eq.freqs, eq.predicted_db, primary)
    if feature is None or feature >= coverage_end:
        return eq
    # Map destination -> source exactly at the resonance. The old ramp plus
    # crossfade left a residual notch at the old location and missed the chosen
    # frequency. A monotone coordinate map avoids folds, gain overshoot and
    # out-of-band extrapolation. Only coordinates use cubic interpolation;
    # correction levels retain native-sample log-linear interpolation.
    applied = float(np.clip(personal, feature / PERSONAL_TREBLE_MAX_WARP,
                            min(feature * PERSONAL_TREBLE_MAX_WARP, coverage_end)))
    if not coverage_start < applied < coverage_end:
        return eq  # Destination outside supported interior: do not invent data.
    shoulder = 2 ** PERSONAL_TREBLE_SHOULDER_OCTAVES
    start = max(coverage_start, min(feature, applied) / shoulder)
    end = min(coverage_end, max(feature, applied) * shoulder)
    correction = eq.correction_db.copy()
    if applied != feature:
        region = (eq.freqs > start) & (eq.freqs < end)
        mapping = PchipInterpolator(np.log([start, applied, end]),
                                    np.log([start, feature, end]), extrapolate=False)
        correction[region] = np.interp(mapping(x[region]), x, eq.correction_db)
    return replace(eq, correction_db=correction, personal_peak_hz=personal,
                   coupler_treble_peak_hz=feature, applied_personal_peak_hz=applied,
                   personal_treble_band_hz=(start, end))


def match_channels(measurements, peaks, target_freqs, target_db, target_name,
                   personal_treble=None):
    channels = {
        side: adjust_personal_treble(
            match_target(model, peaks[side], target_freqs, target_db, target_name),
            peaks[side], (personal_treble or {}).get(side),
        )
        for side, model in measurements.models.items()
    }
    # Resampling cannot exceed original gains, but compute shared headroom
    # from both actual corrections as well as the baseline for a stable level.
    preamp = min(min(eq.preamp_db, -max(0.0, float(np.max(eq.correction_db))) - 1)
                 for eq in channels.values())
    return ChannelEQ(channels, target_name, preamp, personal_treble is not None)
