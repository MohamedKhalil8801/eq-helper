"""Pluggable export formats: GraphicEQ text and convolution packaging variants."""

import zipfile
from pathlib import PurePosixPath

import numpy as np
import pytest
import soundfile as sf

from eq_helper.dsp.graphic_eq import graphic_eq_response, parse_graphic_eq_line
from eq_helper.dsp.resonance import Measurements, match_channels
from eq_helper.exports.formats import get_format, registered_formats
from eq_helper.exports.formats.base import ExportMeta

META = ExportMeta(customer="Ada", iem="Truthear Nova", date="2026-10-27", tuning="L 7600 Hz / R 8100 Hz · PEQdB Diamond")


@pytest.fixture(scope="module")
def eq():
    f = np.geomspace(20, 20000, 384)
    return match_channels(Measurements.bundled(), {"L": 7600, "R": 8100}, f, -2 * np.log10(f / 1000), "PEQdB Diamond")


def test_registry_lists_formats():
    ids = set(registered_formats())
    assert {"graphic_eq", "convolution"} <= ids
    with pytest.raises(KeyError):
        get_format("nope")
    for fid in ids:
        fmt = get_format(fid)
        assert fmt.id == fid and isinstance(fmt.options_schema, dict)


def test_meta_header_lines():
    lines = META.header_lines()
    assert "Customer: Ada" in lines and "IEM: Truthear Nova" in lines and "Date: 2026-10-27" in lines
    assert any(line.startswith("Tuning: L 7600") for line in lines)


def test_graphic_eq_wraps_apo_text_with_comments(eq, tmp_path):
    (path,) = get_format("graphic_eq").export(eq, {}, tmp_path, "Nova EQ", META)
    assert path.name == "Nova EQ.txt"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Customer: Ada\n")
    assert text.endswith(eq.apo_text())
    lines = [s for s in text.splitlines() if s.startswith("GraphicEQ:")]
    assert len(lines) == 2
    for line, ch in zip(lines, eq.channels.values()):
        bands = parse_graphic_eq_line(line).bands
        np.testing.assert_allclose(graphic_eq_response(bands, ch.freqs), ch.correction_db, atol=1e-6)


def _peak_db(samples):
    spectrum = np.abs(np.fft.rfft(samples, 1 << 16, axis=0))
    return 20 * np.log10(spectrum.max())


def test_convolution_stereo_single_rate(eq, tmp_path):
    paths = get_format("convolution").export(eq, {"packaging": "stereo_single_rate", "sample_rate": 48000},
                                             tmp_path, "Nova", META)
    wavs = [p for p in paths if p.suffix == ".wav"]
    assert [p.name for p in wavs] == ["Nova 48kHz.wav"]
    data, rate = sf.read(wavs[0])
    assert rate == 48000 and data.shape[1] == 2
    assert sf.info(wavs[0]).subtype == "PCM_24"
    assert _peak_db(data) == pytest.approx(-1.0, abs=0.05)
    readme = next(p for p in paths if p.name.endswith("README.txt"))
    text = readme.read_text(encoding="utf-8")
    assert "Customer: Ada" in text and "Truthear Nova" in text and "Minimum phase" in text


def test_convolution_per_rate_named_for_camilladsp(eq, tmp_path):
    paths = get_format("convolution").export(eq, {"packaging": "per_rate_named"}, tmp_path, "Nova", META)
    wavs = sorted(p.name for p in paths if p.suffix == ".wav")
    assert wavs == ["Nova 44100.wav", "Nova 48000.wav", "Nova 88200.wav", "Nova 96000.wav"]
    readme = next(p for p in paths if p.name.endswith("README.txt")).read_text(encoding="utf-8")
    assert "$samplerate$" in readme and "type: Conv" in readme and "channel: 1" in readme
    # One shared gain: each rate's loudest channel peaks at the same level.
    peaks = [_peak_db(sf.read(tmp_path / name)[0]) for name in wavs]
    np.testing.assert_allclose(peaks, -1.0, atol=0.05)


def test_convolution_roon_zip_relative_paths(eq, tmp_path):
    (zpath,) = get_format("convolution").export(eq, {"packaging": "roon_zip"}, tmp_path, "Nova", META)
    assert zpath.name == "Nova Roon.zip"
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        for n in names:
            assert not PurePosixPath(n).is_absolute() and ".." not in n and "/" not in n
        cfgs = [n for n in names if n.endswith(".cfg")]
        wavs = [n for n in names if n.endswith(".wav")]
        assert len(cfgs) == len(wavs) == 6
        assert "README.txt" in names[-1] or any(n.endswith("README.txt") for n in names)
        cfg = z.read("Nova 44100.cfg").decode()
        first = cfg.splitlines()[0].split()
        assert first[:3] == ["44100", "2", "2"]
        assert cfg.count("Nova 44100.wav") == 2


def test_convolution_rejects_unknown_options(eq, tmp_path):
    fmt = get_format("convolution")
    with pytest.raises(ValueError, match="packaging"):
        fmt.export(eq, {"packaging": "tape"}, tmp_path, "x", META)
    with pytest.raises(ValueError, match="sample rate"):
        fmt.export(eq, {"packaging": "stereo_single_rate", "sample_rate": 12345}, tmp_path, "x", META)


def test_graphic_single_line_and_band_limit(eq, tmp_path):
    from eq_helper.exports.formats.base import shared_only
    fmt = get_format("graphic_eq")
    with pytest.raises(ValueError, match="shared"):
        fmt.export(eq, {"single_line": True}, tmp_path, "x", META)
    shared = shared_only(eq, balance_control=False).channel_eq
    path, readme = fmt.export(shared, {"single_line": True, "max_bands": 127}, tmp_path, "Nova GraphicEQ", META)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("GraphicEQ:") and text.count("\n") == 1
    assert len(parse_graphic_eq_line(text.strip()).bands) == 127
    assert "Preamp" in readme.read_text(encoding="utf-8")
