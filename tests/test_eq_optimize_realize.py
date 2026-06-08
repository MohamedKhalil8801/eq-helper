"""APO min-phase-FIR realization simulation (verification path)."""

import numpy as np

from eq_helper.dsp.eq_optimize import realized_response_db
from eq_helper.dsp.graphic_eq import GraphicEQBand, graphic_eq_response


def test_realized_matches_smooth_target():
    freqs = np.geomspace(50, 18000, 400)
    bands = [
        GraphicEQBand(50, 0),
        GraphicEQBand(1000, 6),
        GraphicEQBand(4000, -4),
        GraphicEQBand(18000, 0),
    ]
    target = graphic_eq_response(bands, freqs)
    realized = realized_response_db(bands, freqs, 48000)
    mid = (freqs > 100) & (freqs < 12000)
    assert np.max(np.abs(realized[mid] - target[mid])) < 0.1


def test_realized_flat_is_flat():
    freqs = np.geomspace(50, 18000, 200)
    bands = [GraphicEQBand(20, 0), GraphicEQBand(20000, 0)]
    realized = realized_response_db(bands, freqs, 48000)
    assert np.max(np.abs(realized)) < 0.05
