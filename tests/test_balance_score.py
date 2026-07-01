"""Peak/dip alignment score + seal-loss detection."""

import numpy as np

from eq_helper.dsp.balance_score import (
    alignment_score,
    detect_features,
    detect_seal_loss,
    sub_bass_metric,
)


def _meas_with_features(peak_hz=None, dip_hz=None, gain=0.0, noise=0.0):
    f = np.geomspace(20, 20000, 3000)
    lf = np.log10(f)
    db = np.full_like(f, 80.0 + gain)
    if peak_hz:
        db += 6 * np.exp(-((lf - np.log10(peak_hz)) ** 2) / (2 * 0.02 ** 2))
    if dip_hz:
        db -= 5 * np.exp(-((lf - np.log10(dip_hz)) ** 2) / (2 * 0.02 ** 2))
    if noise:
        rng = np.random.default_rng(0)
        db += noise * rng.standard_normal(f.shape)
    return f, db


def test_detect_features_finds_peak_and_dip():
    f, db = _meas_with_features(peak_hz=10000, dip_hz=8000)
    feats = detect_features(f, db)
    peaks = [x for x in feats if x[2] == 1]
    dips = [x for x in feats if x[2] == -1]
    assert any(abs(p[0] - 10000) < 400 for p in peaks)
    assert any(abs(d[0] - 8000) < 400 for d in dips)


def test_alignment_identical_is_one():
    f, db = _meas_with_features(peak_hz=10000, dip_hz=8000)
    a = detect_features(f, db)
    assert alignment_score(a, a) > 0.999


def test_alignment_gain_invariant():
    fa, da = _meas_with_features(peak_hz=10000, dip_hz=8000, gain=0.0)
    fb, db = _meas_with_features(peak_hz=10000, dip_hz=8000, gain=7.0)  # louder
    sa = detect_features(fa, da)
    sb = detect_features(fb, db)
    assert alignment_score(sa, sb) > 0.999  # location identical -> perfect


def test_alignment_shift_lowers_score():
    fa, da = _meas_with_features(peak_hz=10000, dip_hz=8000)
    fb, db = _meas_with_features(peak_hz=10600, dip_hz=8500)  # shifted up
    s_close = alignment_score(detect_features(fa, da), detect_features(fa, da))
    s_shift = alignment_score(detect_features(fa, da), detect_features(fb, db))
    assert s_shift < s_close
    assert 0.0 <= s_shift <= 1.0


def test_alignment_unmatched_feature_excluded():
    # a has an extra dip the other lacks; the shared peak still matches perfectly
    fa, da = _meas_with_features(peak_hz=10000, dip_hz=8000)
    fb, db = _meas_with_features(peak_hz=10000, dip_hz=None)
    score = alignment_score(detect_features(fa, da), detect_features(fb, db))
    assert score > 0.99  # only the matched peak counts


def test_alignment_no_match_is_zero():
    fa, da = _meas_with_features(peak_hz=10000)
    fb, db = _meas_with_features(dip_hz=8000)  # only a dip; no peak to match
    assert alignment_score(detect_features(fa, da), detect_features(fb, db)) == 0.0


def test_alignment_is_symmetric():
    # optimal assignment -> order-independent (greedy was not).
    fa, da = _meas_with_features(peak_hz=8700, dip_hz=None)
    fb, db = _meas_with_features(peak_hz=8700, dip_hz=None)
    a, b = detect_features(fa, da), detect_features(fb, db)
    assert alignment_score(a, b) == alignment_score(b, a)


def test_alignment_shared_resonance_optimal_not_greedy():
    # A and B share a peak at 10500 (plus a non-shared peak each). Greedy could
    # let A's 8700 steal the 10500 match and score ~0; optimal assignment keeps
    # the perfect 10500<->10500 match and drops the out-of-tolerance leftovers.
    a = [(8700.0, 4.0, 1), (10500.0, 4.0, 1)]
    b = [(10500.0, 4.0, 1), (12500.0, 4.0, 1)]
    assert alignment_score(a, b) > 0.9
    assert alignment_score(a, b) == alignment_score(b, a)  # symmetric


def _bass(sub_db, mid_db=0.0):
    f = np.geomspace(20, 20000, 2000)
    db = np.full_like(f, 80.0)
    db[f <= 400] = 80.0 + mid_db
    db[f <= 45] = 80.0 + sub_db  # sub-bass region
    return f, db


def test_sub_bass_metric_flat_is_near_zero():
    f, db = _bass(sub_db=0.0, mid_db=0.0)
    assert abs(sub_bass_metric(f, db)) < 0.5


def test_seal_loss_flags_rolled_off_only():
    metrics = {"L1": 0.2, "L2": 0.1, "R1": 0.8, "R2": -9.0}  # R2 rolled off
    flagged = detect_seal_loss(metrics, threshold_db=6.0)
    assert flagged == {"R2"}


def test_seal_loss_ignores_level_offset():
    # sub_bass_metric is self-normalizing, so two measurements differing only by
    # an overall level (not a roll-off) both read ~0 -> nothing flagged.
    fa, da = _bass(sub_db=0.0)
    fb, db = _bass(sub_db=0.0)
    db = db + 3.0  # louder overall, same shape
    metrics = {"a": sub_bass_metric(fa, da), "b": sub_bass_metric(fb, db)}
    assert detect_seal_loss(metrics) == set()


def test_seal_loss_uniform_rolloff_flags_nothing():
    metrics = {"L1": -8.0, "L2": -8.2, "R1": -8.1, "R2": -7.9}  # all rolled off
    assert detect_seal_loss(metrics, threshold_db=6.0) == set()
