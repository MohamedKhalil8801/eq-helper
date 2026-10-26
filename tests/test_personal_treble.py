"""Personal alignment must move the correction, not just its metadata."""
import numpy as np
import pytest

from eq_helper.dsp.resonance import ResonanceEQ, adjust_personal_treble, match_channels, Measurements


def synthetic_eq():
    f = np.geomspace(20, 20000, 20001)
    predicted = 8 * np.exp(-.5 * (np.log(f / 15500) / .025) ** 2)
    return ResonanceEQ(7500, 'flat', f, predicted, np.zeros_like(f), -predicted, -1)


@pytest.mark.parametrize('destination', [12000, 13500, 17000, 18000])
def test_correction_notch_actually_lands_at_requested_frequency(destination):
    original = synthetic_eq()
    adjusted = adjust_personal_treble(original, 7500, destination)
    notch = adjusted.freqs[np.argmin(adjusted.correction_db)]
    assert notch == pytest.approx(destination, abs=15)
    assert np.min(adjusted.correction_db) == pytest.approx(-8, abs=.01)
    np.testing.assert_array_equal(adjusted.predicted_db, original.predicted_db)
    np.testing.assert_array_equal(adjusted.target_db, original.target_db)
    low = original.freqs <= 7500
    np.testing.assert_array_equal(adjusted.correction_db[low], original.correction_db[low])
    assert adjusted.correction_db[-1] == original.correction_db[-1]


def test_skip_and_identity_are_exact():
    original = synthetic_eq()
    assert adjust_personal_treble(original, 7500, None) is original
    from eq_helper.dsp.resonance import detect_high_treble_peak
    peak = detect_high_treble_peak(original.freqs, original.predicted_db, 7500)
    adjusted = adjust_personal_treble(original, 7500, peak)
    np.testing.assert_array_equal(adjusted.correction_db, original.correction_db)


def test_bounded_alignment_reports_applied_not_only_requested_frequency():
    adjusted = adjust_personal_treble(synthetic_eq(), 7500, 10500)
    notch = adjusted.freqs[np.argmin(adjusted.correction_db)]
    assert adjusted.applied_personal_peak_hz == pytest.approx(notch, abs=15)
    assert adjusted.applied_personal_peak_hz > 10500
    assert 'limited' in adjusted.apo_text()


def test_alignment_leaves_unrelated_treble_features_exactly_unchanged():
    original = synthetic_eq()
    # A feature above the first peak, but unrelated to the higher resonance.
    original.correction_db += 4 * np.exp(-.5 * (np.log(original.freqs / 9500) / .05) ** 2)
    adjusted = adjust_personal_treble(original, 7500, 13500)
    unrelated = (original.freqs < 12000) | (original.freqs > 18000)
    np.testing.assert_array_equal(adjusted.correction_db[unrelated], original.correction_db[unrelated])
    assert adjusted.freqs[np.argmin(adjusted.correction_db)] == pytest.approx(13500, abs=15)


def test_channel_api_sets_personal_metadata_and_safe_shared_preamp():
    data = Measurements.bundled()
    f = np.array([20., 1000., 20000.])
    base = match_channels(data, {'L': 7700, 'R': 7700}, f, np.zeros(3), 'flat')
    adjusted = match_channels(data, {'L': 7700, 'R': 7700}, f, np.zeros(3), 'flat',
                              personal_treble={'L': 13500})
    assert adjusted.personal_treble_enabled
    assert 'Personal higher-treble' in adjusted.apo_text()
    np.testing.assert_array_equal(base.channels['R'].correction_db, adjusted.channels['R'].correction_db)
    for eq in adjusted.channels.values():
        assert np.max(eq.correction_db) + adjusted.preamp_db <= -1 + 1e-9
