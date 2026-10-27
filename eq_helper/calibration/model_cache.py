"""Cache trained resonance models so a calibration trains only once.

One ``.npz`` per calibration id in the app's data folder, tagged with a hash
of the fits and :data:`eq_helper.dsp.resonance.MODEL_VERSION`. A tag mismatch
or any read problem simply retrains; a cache that can't be written is ignored.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from eq_helper.dsp import resonance
from eq_helper.dsp.resonance import Measurements, ResonanceModel

from .rescal import Calibration


def content_hash(calibration: Calibration) -> str:
    h = hashlib.sha256()
    for fit in calibration.fits:
        h.update(json.dumps([fit.side, fit.name]).encode())
        h.update(np.ascontiguousarray(fit.freqs, dtype="<f8").tobytes())
        h.update(np.ascontiguousarray(fit.db, dtype="<f8").tobytes())
    return h.hexdigest()


def _tag(calibration: Calibration) -> str:
    return f"{content_hash(calibration)}:{resonance.MODEL_VERSION}"


def cache_path(calibration: Calibration, cache_dir) -> Path:
    return Path(cache_dir) / f"{calibration.manifest.calibration_id}.npz"


def _save(path: Path, tag: str, measurements: Measurements) -> None:
    arrays = {}
    meta = {"tag": tag, "name": measurements.name, "warnings": list(measurements.warnings), "models": {}}
    for side, model in measurements.models.items():
        state = model.state()
        for key in ("freqs", "peaks", "curves"):
            arrays[f"{side}_{key}"] = state[key]
        meta["models"][side] = {k: (list(v) if k == "sources" else v) for k, v in state.items()
                                if k not in ("freqs", "peaks", "curves")}
    arrays["meta"] = np.array(json.dumps(meta))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    tmp.replace(path)


def _load(path: Path, tag: str) -> Measurements | None:
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("tag") != tag:
                return None
            models = {}
            for side, state in meta["models"].items():
                state = dict(state)
                for key in ("freqs", "peaks", "curves"):
                    state[key] = data[f"{side}_{key}"]
                models[side] = ResonanceModel.from_state(state)
        return Measurements(models, meta["name"], tuple(meta["warnings"]))
    except Exception:  # noqa: BLE001 - any unreadable cache means "retrain"
        return None


def load_measurements(calibration: Calibration, cache_dir) -> Measurements:
    """Return trained models for ``calibration``, training only on a cache miss."""
    path, tag = cache_path(calibration, cache_dir), _tag(calibration)
    cached = _load(path, tag) if path.exists() else None
    if cached is not None:
        return cached
    measurements = calibration.measurements()
    try:
        _save(path, tag, measurements)
    except OSError:
        pass
    return measurements
