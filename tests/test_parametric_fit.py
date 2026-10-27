"""Parametric EQ fitter: accurate, within each app's limits, deterministic."""

import numpy as np
import pytest

from eq_helper.dsp.biquad import peaking_sos, sos_magnitude_response
from eq_helper.dsp.parametric_fit import (
    PeqFilter, PeqLimits, filters_response_db, fit_parametric, fit_stereo, max_error_db, peaking_db,
)
from eq_helper.dsp.resonance import Measurements, match_channels

F = np.geomspace(20, 20000, 2000)


def test_peaking_helper_matches_cookbook_biquad():
    np.testing.assert_allclose(peaking_db(F, 8000, -6, 4),
                               sos_magnitude_response(peaking_sos(8000, -6, 4, 48000), F, 48000), atol=1e-9)
    filt = [PeqFilter("PK", 8000, -6, 4)]
    np.testing.assert_allclose(filters_response_db(filt, F, 48000), peaking_db(F, 8000, -6, 4), atol=1e-9)


def test_fits_single_peak_exactly():
    target = peaking_db(F, 8000, -6, 4)
    peq = fit_parametric(F, target, PeqLimits(max_filters=10))
    assert max_error_db(peq, F, target, upto=16000) < 0.1
    assert len(peq.filters) <= 3


def test_fits_two_peaks_and_a_shelf():
    target = (peaking_db(F, 6200, -7, 5) + peaking_db(F, 3000, 3, 1.5)
              + filters_response_db([PeqFilter("LSC", 105, 4, 0.7)], F, 48000))
    peq = fit_parametric(F, target, PeqLimits(max_filters=6))
    assert max_error_db(peq, F, target, upto=16000) < 0.3


def _resonance_correction():
    f = np.geomspace(20, 20000, 384)
    eq = match_channels(Measurements.bundled(), {"L": 7600, "R": 8100}, f, -2 * np.log10(f / 1000), "D")
    return eq


def test_respects_limits():
    ch = _resonance_correction().channels["L"]
    limits = PeqLimits(max_filters=5, gain_range=(-6, 3), q_range=(0.5, 3), types=("PK",),
                       fixed_preamp=-8.0, gain_step=0.1, q_step=0.01, fc_step=1)
    peq = fit_parametric(ch.freqs, ch.correction_db, limits)
    assert 1 <= len(peq.filters) <= 5
    for filt in peq.filters:
        assert filt.type == "PK"
        assert -6 <= filt.gain_db <= 3 and 0.5 <= filt.q <= 3
        assert filt.gain_db == pytest.approx(round(filt.gain_db, 1))
        assert filt.q == pytest.approx(round(filt.q, 2)) and filt.fc == round(filt.fc)
    assert peq.preamp_db == -8.0
    with pytest.raises(ValueError, match="type"):
        PeqLimits(types=("BP",))


def test_preamp_gives_headroom_when_not_fixed():
    target = peaking_db(F, 2000, 5, 2)
    peq = fit_parametric(F, target, PeqLimits())
    assert peq.preamp_db == pytest.approx(-max(0.0, filters_response_db(peq.filters, F, 48000).max()) - 1, abs=0.05)


def test_reports_accuracy_bands():
    ch = _resonance_correction().channels["L"]
    peq = fit_parametric(ch.freqs, ch.correction_db, PeqLimits(max_filters=10))
    acc = peq.accuracy
    assert acc.max_error_db[10000] >= acc.max_error_db[1000]
    assert acc.max_error_db[20000] == pytest.approx(max_error_db(peq, ch.freqs, ch.correction_db, upto=20000))
    text = acc.describe(tolerance_db=1.0)
    assert text.startswith("within ±1 dB up to") and "kHz" in text
    assert acc.max_error_db[acc.within_hz(1.0)] <= 1.0


def test_stereo_fits_independently_shared_preamp():
    eq = _resonance_correction()
    curves = {s: (c.freqs, c.correction_db) for s, c in eq.channels.items()}
    fits = fit_stereo(curves, PeqLimits(max_filters=8))
    assert set(fits) == {"L", "R"}
    assert fits["L"].filters != fits["R"].filters
    assert fits["L"].preamp_db == fits["R"].preamp_db
    worst = max(filters_response_db(p.filters, F, 48000).max() for p in fits.values())
    assert fits["L"].preamp_db == pytest.approx(-max(0.0, worst) - 1, abs=0.05)


def test_deterministic():
    ch = _resonance_correction().channels["R"]
    a = fit_parametric(ch.freqs, ch.correction_db, PeqLimits(max_filters=8))
    b = fit_parametric(ch.freqs, ch.correction_db, PeqLimits(max_filters=8))
    assert a.filters == b.filters and a.preamp_db == b.preamp_db


def test_sample_rate_aware_near_nyquist():
    target = peaking_db(F, 15000, -5, 3, fs=44100)
    peq = fit_parametric(F, target, PeqLimits(sample_rate=44100))
    assert peq.sample_rate == 44100
    assert max_error_db(peq, F, target, upto=16000) < 0.2


# ── export format ───────────────────────────────────────────────────────────

def _meta():
    from eq_helper.exports.formats.base import ExportMeta
    return ExportMeta("Ada", "Truthear Nova", "2026-10-27", "L 7600 / R 8100")


def test_parametric_format_writers(tmp_path):
    import re
    from eq_helper.exports.formats import get_format
    eq = _resonance_correction()
    fmt = get_format("parametric")
    opts = {"limits": {"max_filters": 6, "gain_range": [-10, 10], "q_range": [0.4, 6], "gain_step": 0.1}}

    (apo,) = fmt.export(eq, {**opts, "writer": "apo_filters"}, tmp_path, "Nova PEQ", _meta())
    text = apo.read_text(encoding="utf-8")
    assert text.startswith("# Customer: Ada")
    assert "# Accuracy L: within" in text
    assert text.count("Preamp:") == 1 and "\nChannel: L\n" in text and "\nChannel: R\n" in text
    rows = re.findall(r"^Filter \d+: ON (PK|LSC|HSC) Fc (\S+) Hz Gain (\S+) dB Q (\S+)$", text, re.M)
    assert 2 <= len(rows) <= 12
    assert all(-10 <= float(g) <= 10 for _, _, g, _ in rows)

    files = fmt.export(eq, {**opts, "writer": "autoeq_text"}, tmp_path, "Nova", _meta())
    assert [p.name for p in files] == ["Nova L ParametricEQ.txt", "Nova R ParametricEQ.txt"]
    left = files[0].read_text(encoding="utf-8")
    assert re.search(r"^Preamp: -?\d+\.\d dB$", left, re.M)
    assert re.search(r"^Filter 1: ON \w+ Fc \d+ Hz Gain -?\d+\.\d dB Q \d+\.\d\d$", left, re.M)

    (csv_path,) = fmt.export(eq, {**opts, "writer": "csv"}, tmp_path, "Nova", _meta())
    lines = [l for l in csv_path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    assert lines[0] == "side,type,fc_hz,gain_db,q"
    assert {l.split(",")[0] for l in lines[1:]} >= {"L", "R", "preamp"}

    fits = fmt.fit(eq, opts)
    assert set(fits) == {"L", "R"} and fits["L"].accuracy.describe().startswith("within")
    with pytest.raises(ValueError, match="writer"):
        fmt.export(eq, {"writer": "xml"}, tmp_path, "x", _meta())
