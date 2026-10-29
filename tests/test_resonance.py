"""Public measurement-learning and channel-export behavior; synthetic inputs."""

import numpy as np
import pytest

from eq_helper.dsp.graphic_eq import parse_graphic_eq_line, graphic_eq_response
from eq_helper.dsp.resonance import (
    Measurements, adjust_personal_treble, detect_peak, interp, match_channels,
)


def write_fit(path, peak=8000, offset=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = np.unique(np.r_[np.geomspace(20, 20000, 2400), 1000])
    y = (75 + offset + 9 * np.exp(-0.5 * (np.log(f / peak) / .075) ** 2)
         + 2 * np.log(peak / 8000) * np.exp(-0.5 * (np.log(f / 300) / .8) ** 2))
    # Source channel intentionally differs from the ear, as in real REW exports.
    np.savetxt(path, np.c_[f, y], header='* Source: input, L\n* Measurement: ' + path.stem, comments='')
    return f, y


def make_stereo(folder):
    for side, offset in [('L', 0), ('R', 2)]:
        for i, peak in enumerate([6500, 7200, 8000, 8800]):
            write_fit(folder / f'{side}{i}.txt', peak, offset)
    return Measurements.from_folder(folder)


def test_learns_both_sides_and_preserves_relative_levels(tmp_path):
    data = make_stereo(tmp_path)
    assert set(data.models) == {'L', 'R'}
    left, right = data.models['L'], data.models['R']
    assert left.sample_count == right.sample_count == 4
    assert left.validation['interpolation_rms_db'] >= 0
    assert len(left.validation['candidates_rms_db']) == 3
    assert abs(interp(left.freqs, right.predict(7700) - left.predict(7700), [1000])[0] - 2) < 1e-6
    for side in data.models.values():
        for peak in (6000, 6500, 7700, 9000):
            prediction = side.predict(peak)
            assert np.isfinite(prediction).all()
            assert abs(detect_peak(side.freqs, prediction) - peak) < 20
        for peak, curve in zip(side.peaks, side.curves):
            np.testing.assert_array_equal(side.predict(peak), curve)


def test_personal_treble_adjustment_warps_only_treble_correction_without_changing_prediction(tmp_path):
    data = make_stereo(tmp_path)
    f = np.geomspace(20, 20000, 1200)
    target = (6 * np.log10(f / 1000)
              - 5 * np.exp(-0.5 * (np.log(f / 15500) / .12) ** 2))
    eq = match_channels(data, {'L': 7500, 'R': 8200}, f, target, 'Target')
    original = eq.channels['L']
    second_peak = 5 * np.exp(-0.5 * (np.log(original.freqs / 15500) / .12) ** 2)
    original.predicted_db += second_peak
    original.correction_db -= second_peak

    no_override = adjust_personal_treble(original, 7500, None)
    np.testing.assert_array_equal(no_override.correction_db, original.correction_db)
    assert no_override.personal_peak_hz is None
    assert no_override.prediction_note == original.prediction_note

    adjusted = adjust_personal_treble(original, 7500, 13500)
    assert adjusted.personal_peak_hz == 13500
    assert adjusted.coupler_treble_peak_hz == pytest.approx(15500, abs=150)
    assert 'Personal treble correction alignment' in adjust_personal_treble(original, 7500, 13500).apo_text()
    baseline = match_channels(data, {'L': 7500, 'R': 8200}, f, target, 'Target')
    assert eq.preamp_db == pytest.approx(
        -max(np.max(c.correction_db) for c in baseline.channels.values()) - 1
    )
    assert adjusted.peak_hz == original.peak_hz
    assert adjusted.prediction_note == original.prediction_note
    np.testing.assert_array_equal(adjusted.predicted_db, original.predicted_db)
    np.testing.assert_array_equal(adjusted.target_db, original.target_db)
    np.testing.assert_array_equal(adjusted.freqs, original.freqs)
    np.testing.assert_allclose(adjusted.correction_db, adjust_personal_treble(original, 7500, 13500).correction_db)
    assert np.max(np.abs(adjusted.correction_db - original.correction_db)) > 1e-3
    # Below the primary resonance, a second-peak fit must leave EQ alone.
    low = adjusted.freqs < 7500
    np.testing.assert_array_equal(adjusted.correction_db[low], original.correction_db[low])


def test_no_higher_coupler_peak_leaves_optional_fit_as_noop(tmp_path):
    data = make_stereo(tmp_path)
    freqs = np.geomspace(20, 20000, 1200)
    target = np.zeros_like(freqs)
    eq = match_channels(data, {'L': 7500, 'R': 7500}, freqs, target, 'flat')
    adjusted = adjust_personal_treble(eq.channels['L'], 7500, 13500)
    assert adjusted is eq.channels['L']


def test_stereo_eq_uses_channel_filters_and_one_common_preamp(tmp_path):
    data = make_stereo(tmp_path)
    f = np.geomspace(20, 20000, 600)
    target = 6 * np.log10(f / 1000)
    eq = match_channels(data, {'L': 7500, 'R': 8200}, f, target, 'Target')
    text = eq.apo_text()
    assert text.count('Preamp:') == 1
    assert text.count('GraphicEQ:') == 2
    assert '\nChannel: L\n' in text and '\nChannel: R\n' in text
    assert text.endswith('Channel: L R\n')
    assert 'channel: all' not in text.lower()  # heard to upset the balance; L R named instead
    for channel, line in zip(eq.channels.values(), [s for s in text.splitlines() if s.startswith('GraphicEQ:')]):
        np.testing.assert_allclose(channel.predicted_db + channel.correction_db, channel.target_db, atol=1e-12)
        bands = parse_graphic_eq_line(line).bands
        np.testing.assert_allclose(graphic_eq_response(bands, channel.freqs), channel.correction_db, atol=1e-10)
        assert eq.preamp_db + max(channel.correction_db) <= -.999999
    bypass = eq.apo_text(False)
    assert 'GraphicEQ:' not in bypass
    assert next(s for s in text.splitlines() if s.startswith('Preamp:')) in bypass


@pytest.mark.parametrize('side', ['L', 'R', 'M'])
def test_one_side_or_unlabelled_set_applies_same_eq_to_both(tmp_path, side):
    name = 'fit' if side == 'M' else side
    write_fit(tmp_path / f'{name} 1.txt')
    data = Measurements.from_folder(tmp_path)
    assert set(data.models) == {side}
    eq = match_channels(data, {side: 7700}, np.array([20., 1000., 20000.]), np.zeros(3), 'flat')
    text = eq.apo_text()
    assert 'GraphicEQ:' in text
    assert '\nChannel: L\n' not in text and '\nChannel: R\n' not in text
    assert 'channel: all' not in text.lower() and '\nChannel: L R\nGraphicEQ:' in text
    assert 'single fit' in data.models[side].describe(7700)


def test_side_subfolders_repeats_and_bad_files_reported(tmp_path):
    write_fit(tmp_path / 'Left' / 'one.txt', 7800)
    write_fit(tmp_path / 'Left' / 'repeat.txt', 7800, .1)
    write_fit(tmp_path / 'Right' / 'one.csv', 8200)
    (tmp_path / 'broken.txt').write_text('not a curve')
    write_fit(tmp_path / 'unlabelled.txt', 7900)
    data = Measurements.from_folder(tmp_path)
    assert data.models['L'].sample_count == 2
    assert len(data.models['L'].peaks) == 1
    assert data.models['R'].sample_count == 1
    assert any('broken.txt' in w for w in data.warnings)
    assert any('unlabelled.txt' in w for w in data.warnings)
    assert np.isfinite(data.models['L'].predict(6000)).all()


def test_empty_or_invalid_folder_is_not_a_silent_fallback(tmp_path):
    with pytest.raises(ValueError, match='No usable measurements'):
        Measurements.from_folder(tmp_path)
    np.savetxt(tmp_path / 'L 1.txt', [[20, 0], [1000, 0], [6000, float('nan')], [20000, 0]])
    with pytest.raises(ValueError, match='finite'):
        Measurements.from_folder(tmp_path)


def test_target_validation_and_new_measurements_change_prediction(tmp_path):
    write_fit(tmp_path / 'L 1.txt', 8000)
    original = Measurements.from_folder(tmp_path)
    write_fit(tmp_path / 'L 1.txt', 7000, 4)
    updated = Measurements.from_folder(tmp_path)
    assert abs(updated.models['L'].peaks[0] - original.models['L'].peaks[0]) > 900
    assert not np.allclose(original.models['L'].predict(7700), updated.models['L'].predict(7700))
    with pytest.raises(ValueError, match='1 kHz'):
        match_channels(updated, {'L': 7700}, np.array([2000., 20000.]), np.zeros(2), 'bad target')


def _fits_from_folder(folder):
    from eq_helper.dsp.resonance import read_fits
    fits, warnings = read_fits(folder)
    return fits, warnings


def test_from_fits_matches_from_folder(tmp_path):
    from eq_helper.dsp.resonance import Fit
    folder = make_stereo(tmp_path)
    fits, _ = _fits_from_folder(tmp_path)
    assert all(isinstance(f, Fit) for f in fits)
    mem = Measurements.from_fits(fits, name='Nova')
    assert mem.name == 'Nova'
    assert set(mem.models) == set(folder.models)
    for side in folder.models:
        np.testing.assert_array_equal(folder.models[side].freqs, mem.models[side].freqs)
        np.testing.assert_allclose(folder.models[side].predict(7700), mem.models[side].predict(7700))
        assert folder.models[side].alignment == mem.models[side].alignment


def test_from_fits_in_memory_without_files():
    from eq_helper.dsp.resonance import Fit
    f = np.unique(np.r_[np.geomspace(20, 20000, 2400), 1000])
    fits = [Fit(side, f'{side}{i}', f,
                75 + off + 9 * np.exp(-0.5 * (np.log(f / peak) / .075) ** 2))
            for side, off in [('L', 0), ('R', 2)]
            for i, peak in enumerate([6500, 7200, 8000, 8800])]
    data = Measurements.from_fits(fits, name='mem')
    assert set(data.models) == {'L', 'R'}
    assert data.models['L'].sources == ('L0', 'L1', 'L2', 'L3')
    assert abs(interp(f, data.models['R'].predict(7700) - data.models['L'].predict(7700), [1000])[0] - 2) < 1e-6


def test_from_fits_rejects_unlabelled_mix_and_invalid_fits():
    from eq_helper.dsp.resonance import Fit
    f = np.unique(np.r_[np.geomspace(20, 20000, 2400), 1000])
    y = 75 + 9 * np.exp(-0.5 * (np.log(f / 8000) / .075) ** 2)
    fits = [Fit('L', 'left', f, y), Fit(None, 'nolabel', f, y),
            Fit('R', 'narrow', f[f > 2000], y[f > 2000]),
            Fit('R', 'nan', f, np.where(f > 5000, np.nan, y))]
    data = Measurements.from_fits(fits, name='mix')
    assert set(data.models) == {'L'}
    assert any('nolabel' in w and 'no L/R label' in w for w in data.warnings)
    assert any('narrow' in w and '1 kHz' in w for w in data.warnings)
    assert any('nan' in w and 'finite' in w for w in data.warnings)
    with pytest.raises(ValueError, match='No usable measurements'):
        Measurements.from_fits([], name='empty')
    with pytest.raises(ValueError, match='side'):
        Fit('X', 'bad', f, y)
