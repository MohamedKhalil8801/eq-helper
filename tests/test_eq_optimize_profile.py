"""optimize_profile: a multi-filter stack collapses to verified GraphicEQ(s)."""

import numpy as np

from eq_helper.dsp.eq_optimize import optimize_profile, realized_response_db
from eq_helper.dsp.eq_response import load_profile_eq_response


def _single_geq(groups):
    """Assert the result is one bare all-channel group and return its GraphicEQ."""
    assert len(groups) == 1
    assert groups[0].channels is None
    return groups[0].geq


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
    groups, max_dev = optimize_profile(p, 48000)
    geq = _single_geq(groups)
    freqs = np.geomspace(30, 16000, 800)
    target = load_profile_eq_response(p, freqs)
    realized = realized_response_db(geq.bands, freqs, 48000)
    assert np.max(np.abs(realized - target)) < 0.1
    assert max_dev < 0.1
    assert len(geq.bands) >= 2


def test_optimize_high_q_subgrid_filter_accurate_and_honest(tmp_path):
    # A high-Q peak whose Fc deliberately lands between coarse grid samples.
    # The optimized EQ must be genuinely accurate AND max_dev must not
    # under-report the true error (the feature-aware grid + independent
    # verification grid fix).
    fc, q, g = 5117.3, 120, 9
    p = tmp_path / "hq.txt"
    p.write_text(f"Filter 1: ON PK Fc {fc} Hz Gain {g} dB Q {q}\n", encoding="utf-8")
    groups, max_dev = optimize_profile(p, 48000)
    geq = _single_geq(groups)

    truth = np.unique(np.concatenate([np.geomspace(20, 20000, 200000), [fc]]))
    target = load_profile_eq_response(p, truth)
    realized = realized_response_db(geq.bands, truth, 48000)
    true_max = float(np.max(np.abs(realized - target)))

    assert true_max < 0.1  # genuinely accurate (was ~0.35 dB before the fix)
    assert max_dev >= true_max - 0.03  # honest: no under-report (was ~6x under)


def test_optimize_handles_degenerate_q(tmp_path):
    p = tmp_path / "bad.txt"
    p.write_text("Filter 1: ON PK Fc 1000 Hz Gain 6 dB Q 0\n", encoding="utf-8")
    groups, max_dev = optimize_profile(p, 48000)  # must not raise
    geq = _single_geq(groups)
    assert len(geq.bands) >= 2


def test_optimize_includes_graphic_eq_source(tmp_path):
    # A profile whose EQ is itself a GraphicEQ should round-trip near-exactly.
    p = tmp_path / "prof.txt"
    p.write_text("GraphicEQ: 100 0; 1000 5; 4000 -3; 12000 2\n", encoding="utf-8")
    groups, max_dev = optimize_profile(p, 48000)
    _single_geq(groups)
    assert max_dev < 0.1
