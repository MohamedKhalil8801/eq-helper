"""Shared-only apps: one averaged EQ for both ears, plus a balance value."""

from dataclasses import replace

import numpy as np
import pytest

from eq_helper.dsp.resonance import ChannelEQ, Measurements, match_channels
from eq_helper.exports.formats import get_format
from eq_helper.exports.formats.base import ExportMeta, shared_only


@pytest.fixture(scope="module")
def eq():
    f = np.geomspace(20, 20000, 384)
    return match_channels(Measurements.bundled(), {"L": 7600, "R": 8100}, f, -2 * np.log10(f / 1000), "D")


def _with_sides(eq, left, right):
    return ChannelEQ({"L": left, "R": right}, eq.target_name, eq.preamp_db)


def test_identical_sides_give_no_balance(eq):
    left = eq.channels["L"]
    reduced = shared_only(_with_sides(eq, left, left), balance_control=True)
    assert list(reduced.channel_eq.channels) == ["M"]
    np.testing.assert_allclose(reduced.channel_eq.channels["M"].correction_db, left.correction_db)
    assert reduced.balance_db is None
    assert any("averaged" in n for n in reduced.notes)


def test_offset_sides_balance_equals_offset(eq):
    left = eq.channels["L"]
    right = replace(left, correction_db=left.correction_db + 1.5)
    reduced = shared_only(_with_sides(eq, left, right), balance_control=True)
    assert reduced.balance_db == pytest.approx(1.5, abs=1e-9)
    np.testing.assert_allclose(reduced.channel_eq.channels["M"].correction_db, left.correction_db + 0.75)
    assert any("right" in n and "1.5 dB" in n for n in reduced.notes)
    louder_left = shared_only(_with_sides(eq, right, left), balance_control=True)
    assert louder_left.balance_db == pytest.approx(-1.5) and any("left" in n for n in louder_left.notes)


def test_no_balance_control_still_reports_compromise(eq):
    reduced = shared_only(eq, balance_control=False)
    assert reduced.balance_db is None
    assert reduced.channel_eq.preamp_db == eq.preamp_db
    assert any("reduced" in n or "averaged" in n for n in reduced.notes)
    avg_grid = reduced.channel_eq.channels["M"].freqs
    assert np.all(np.diff(avg_grid) > 0)


def test_single_set_passes_through(eq):
    single = ChannelEQ({"M": eq.channels["L"]}, "D", eq.preamp_db)
    reduced = shared_only(single, balance_control=True)
    assert reduced.channel_eq is single and reduced.balance_db is None and reduced.notes == ()


def test_notes_reach_exported_files(eq, tmp_path):
    reduced = shared_only(eq, balance_control=True)
    meta = ExportMeta("Ada", "Nova", "2026-10-27", notes=reduced.notes)
    (path,) = get_format("graphic_eq").export(reduced.channel_eq, {}, tmp_path, "x", meta)
    text = path.read_text(encoding="utf-8")
    assert "averaged" in text and text.count("GraphicEQ:") == 1
