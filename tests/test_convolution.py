"""Convolution export: phase behaviour, shared gain, packaging and WAV bytes."""

import numpy as np
import pytest
from scipy.io import wavfile

from eq_helper.dsp.convolution import (
    design_channel,
    latency_samples,
    package,
    readme_path,
    render_channels,
    render_set,
    taps_for,
    write_wav,
)


def curve(peak=7700.0, depth=-9.0, width=0.15, tilt=0.0):
    freqs = np.geomspace(20, 20000, 600)
    gain = depth * np.exp(-0.5 * (np.log(freqs / peak) / width) ** 2) + tilt * np.log10(freqs / 1000)
    return freqs, gain


def response_db(impulse, sample_rate, freqs):
    n = 1 << 18
    magnitude = np.abs(np.fft.rfft(impulse, n))
    hz = np.fft.rfftfreq(n, 1.0 / sample_rate)
    db = 20 * np.log10(np.maximum(magnitude, 1e-12))
    return np.interp(np.log(freqs), np.log(hz[1:]), db[1:])


def test_minimum_phase_is_front_loaded_and_reproduces_the_curve():
    freqs, gain = curve()
    impulse = design_channel(freqs, gain, 48000, "minimum")
    grid = np.geomspace(100, 18000, 500)
    error = response_db(impulse, 48000, grid) - np.interp(np.log(grid), np.log(freqs), gain)
    assert np.sqrt(np.mean(error ** 2)) < 0.05

    # Minimum phase means energy first: no symmetric pre-ring, no leading zero run.
    energy = np.cumsum(impulse ** 2) / np.sum(impulse ** 2)
    assert energy[np.searchsorted(energy, 0.99)] < impulse.size * 0.1
    assert abs(impulse[0]) == pytest.approx(np.max(np.abs(impulse)), rel=0.05)
    assert not np.allclose(impulse, impulse[::-1], atol=1e-6)


def test_linear_phase_is_symmetric_and_matches_the_same_magnitude():
    freqs, gain = curve()
    minimum = design_channel(freqs, gain, 48000, "minimum")
    linear = design_channel(freqs, gain, 48000, "linear")
    taps = taps_for(48000)
    assert linear.size == taps
    assert np.allclose(linear, linear[::-1], atol=1e-9)
    assert int(np.argmax(np.abs(linear))) == (taps - 1) // 2
    assert np.allclose(linear[:taps // 2], linear[-(taps // 2):][::-1], atol=1e-9)

    grid = np.geomspace(100, 18000, 400)
    np.testing.assert_allclose(response_db(minimum, 48000, grid),
                               response_db(linear, 48000, grid), atol=0.02)
    assert latency_samples("minimum", taps) == 0
    assert latency_samples("linear", taps) == (taps - 1) // 2


def test_taps_keep_resolution_constant_across_rates():
    assert all(taps_for(rate) % 2 == 1 for rate in (44100, 48000, 96000, 192000))
    resolutions = [rate / taps_for(rate) for rate in (44100, 48000, 96000, 192000)]
    assert max(resolutions) - min(resolutions) < 0.01


def test_one_shared_gain_keeps_the_channel_balance():
    freqs, gain = curve()
    irs = render_channels({"L": (freqs, gain), "R": (freqs, gain + 3)}, 44100, "minimum")
    peaks = {side: np.abs(np.fft.rfft(ir, 1 << 16)).max() for side, ir in irs.items()}
    louder = 20 * np.log10(peaks["R"])
    assert louder == pytest.approx(-1.0, abs=0.05)
    assert 20 * np.log10(peaks["L"]) == pytest.approx(-4.0, abs=0.05)


def test_package_maps_layouts_to_file_contents():
    freqs, gain = curve()
    irs = render_channels({"L": (freqs, gain), "R": (freqs, gain + 2)}, 48000)
    (label, stereo), = package(irs, "stereo")
    assert label == "" and stereo.shape == (taps_for(48000), 2)
    np.testing.assert_array_equal(stereo[:, 0], irs["L"])
    np.testing.assert_array_equal(stereo[:, 1], irs["R"])

    pairs = package(irs, "mono_pair")
    assert [label for label, _ in pairs] == ["L", "R"]
    assert all(samples.shape[1] == 1 for _, samples in pairs)

    single = render_channels({"M": (freqs, gain)}, 48000)
    (_, mono), = package(single, "mono")
    assert mono.shape == (taps_for(48000), 1)
    (_, duplicated), = package(single, "stereo")
    np.testing.assert_array_equal(duplicated[:, 0], duplicated[:, 1])

    with pytest.raises(ValueError, match="different L/R"):
        package(irs, "mono")


@pytest.mark.parametrize("fmt,width,dtype", [("pcm16", 2, np.int16),
                                             ("pcm24", 3, np.int32),
                                             ("f32", 4, np.float32)])
def test_wav_round_trip(tmp_path, fmt, width, dtype):
    samples = np.linspace(-1.0, 1.0, 64).reshape(32, 2)
    path = tmp_path / f"{fmt}.wav"
    write_wav(path, samples, 44100, fmt)
    rate, data = wavfile.read(path)
    assert rate == 44100 and data.shape == (32, 2)
    assert data.dtype == dtype
    header = path.read_bytes()[:44]
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    assert int.from_bytes(header[32:34], "little") == 2 * width
    assert int.from_bytes(header[34:36], "little") == width * 8
    assert int.from_bytes(header[40:44], "little") == 32 * 2 * width
    if fmt == "f32":
        np.testing.assert_allclose(data, samples, atol=1e-6)
    else:
        # scipy returns 24-bit samples left-justified in int32, hence the /2**31.
        full = 2 ** (8 * width - 1)
        stored_full = 2 ** (31 if width == 3 else 8 * width - 1)
        np.testing.assert_allclose(
            data.astype(float) / stored_full,
            np.clip(np.rint(samples * full), -full, full - 1) / full,
            atol=1e-6,
        )


def test_render_set_writes_every_requested_rate(tmp_path):
    freqs, gain = curve()
    curves = {"L": (freqs, gain), "R": (freqs, gain)}
    written = render_set(curves, [44100, 48000], "mono_pair", "minimum", "pcm24",
                         tmp_path, "Resonance L7300 R7300")
    assert len(written) == 4
    assert sorted(path.name for path in written) == sorted([
        "Resonance L7300 R7300 44.1kHz L.wav", "Resonance L7300 R7300 44.1kHz R.wav",
        "Resonance L7300 R7300 48kHz L.wav", "Resonance L7300 R7300 48kHz R.wav",
    ])
    rates = {wavfile.read(path)[0] for path in written}
    assert rates == {44100, 48000}
    # scipy squeezes mono files to one dimension; either way there is no second channel.
    assert all(wavfile.read(path)[1].ndim == 1 for path in written)


def test_render_set_writes_readme_describing_the_set(tmp_path):
    freqs, gain = curve()
    curves = {"L": (freqs, gain), "R": (freqs, gain)}
    written = render_set(curves, [48000], "stereo", "minimum", "pcm24",
                         tmp_path, "Resonance L7300 R7300")
    readme = readme_path(tmp_path, "Resonance L7300 R7300")
    assert readme.exists() and readme not in written
    text = readme.read_text(encoding="utf-8")
    assert written[0].name in text
    assert "48 kHz, 24-bit PCM" in text
    assert "Minimum phase" in text and "no look-ahead" in text
    assert "peaking at -1 dB" in text
    assert "no Channel: selector" in text and "channel 1" in text
    assert "convolvers do not resample" in text


def test_readme_documents_linear_phase_latency_and_mono_pairs(tmp_path):
    freqs, gain = curve()
    curves = {"L": (freqs, gain), "R": (freqs, gain + 2)}
    render_set(curves, [96000], "mono_pair", "linear", "f32",
               tmp_path, "Resonance 7300 8200")
    text = readme_path(tmp_path, "Resonance 7300 8200").read_text(encoding="utf-8")
    delay_ms = latency_samples("linear", taps_for(96000)) / 96000 * 1000.0
    assert "Linear phase" in text
    assert f"{delay_ms:.1f} ms of latency" in text
    assert "96 kHz, 32-bit float" in text
    assert "Resonance 7300 8200 96kHz L.wav" in text and "left channel" in text
    assert "Resonance 7300 8200 96kHz R.wav" in text and "right channel" in text
    assert "Channel: L" in text and "Channel: R" in text
    assert "no Channel: selector" not in text
