"""optimize_profile: a multi-filter stack collapses to one verified GraphicEQ."""

import numpy as np

from eq_helper.dsp.eq_optimize import optimize_profile, realized_response_db
from eq_helper.dsp.eq_response import load_profile_eq_response


def test_optimize_reproduces_combined_response(tmp_path):
    p = tmp_path / "prof.txt"
    p.write_text(
        "Preamp: -6 dB\n"
        "Filter 1: ON PK Fc 100 Hz Gain 6 dB Q 1.0\n"
        "Filter 2: ON PK Fc 1000 Hz Gain -4 dB Q 2.0\n"
        "Filter 3: ON PK Fc 5000 Hz Gain 5 dB Q 1.5\n"
        "Filter 4: ON HS Fc 8000 Hz Gain 3 dB Q 0.7\n",
        encoding="utf-8",
    )
    geq, max_dev = optimize_profile(p, 48000)
    freqs = np.geomspace(30, 16000, 800)
    target = load_profile_eq_response(p, freqs)
    realized = realized_response_db(geq.bands, freqs, 48000)
    assert np.max(np.abs(realized - target)) < 0.1
    assert max_dev < 0.1
    assert len(geq.bands) >= 2


def test_optimize_includes_graphic_eq_source(tmp_path):
    # A profile whose EQ is itself a GraphicEQ should round-trip near-exactly.
    p = tmp_path / "prof.txt"
    p.write_text("GraphicEQ: 100 0; 1000 5; 4000 -3; 12000 2\n", encoding="utf-8")
    geq, max_dev = optimize_profile(p, 48000)
    assert max_dev < 0.1
