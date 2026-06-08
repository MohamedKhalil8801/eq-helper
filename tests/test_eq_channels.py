"""Channel-aware combined response + per-channel optimize / bypass."""

import numpy as np

from eq_helper.dsp.eq_optimize import (
    ChannelEQ,
    optimize_profile,
    region_bypass_groups,
    render_channel_eqs,
)
from eq_helper.dsp.eq_response import (
    ALL_CHANNELS,
    compute_channel_responses,
    compute_eq_response,
    load_profile_channel_responses,
)
from eq_helper.dsp.graphic_eq import GraphicEQ, GraphicEQBand, graphic_eq_response

_FREQS = np.geomspace(20, 20000, 1500)

# A balance config: a shared filter + L-only and R-only filters.
_BALANCE = (
    "Filter 1: ON PK Fc 1000 Hz Gain 4 dB Q 2\n"
    "Channel: L\n"
    "Filter 2: ON PK Fc 120 Hz Gain 3 dB Q 1\n"
    "Channel: R\n"
    "Filter 3: ON PK Fc 6000 Hz Gain -5 dB Q 2\n"
    "Channel: all\n"
    "Filter 4: ON PK Fc 1000 Hz Gain 1 dB Q 2\n"
)


def test_no_channels_returns_all_matching_combined():
    text = "Preamp: -3 dB\nFilter 1: ON PK Fc 1000 Hz Gain 6 dB Q 2\n"
    resps = compute_channel_responses(text, _FREQS)
    assert set(resps) == {ALL_CHANNELS}
    assert np.allclose(resps[ALL_CHANNELS], compute_eq_response(text, _FREQS))


def test_channel_scoping_splits_left_right():
    resps = compute_channel_responses(_BALANCE, _FREQS)
    assert set(resps) == {"L", "R"}
    # 120 Hz boost only on L, 6 kHz cut only on R.
    i120 = int(np.argmin(np.abs(_FREQS - 120)))
    i6k = int(np.argmin(np.abs(_FREQS - 6000)))
    assert resps["L"][i120] > 2.0
    assert resps["R"][i120] < 0.5
    assert resps["R"][i6k] < -2.0
    assert resps["L"][i6k] > -0.5
    # The shared 'all' filters (1 + 4 ~ +5 dB peak) hit BOTH channels at 1 kHz.
    i1k = int(np.argmin(np.abs(_FREQS - 1000)))
    assert resps["L"][i1k] > 4.5
    assert resps["R"][i1k] > 4.5


def test_channel_state_persists_across_include(tmp_path):
    inc = tmp_path / "inc.txt"
    inc.write_text("Filter 1: ON PK Fc 200 Hz Gain 6 dB Q 1\n", encoding="utf-8")
    # Channel: L set in the parent must apply to the included filter.
    main = tmp_path / "main.txt"
    main.write_text(f"Channel: L\nInclude: {inc}\n", encoding="utf-8")
    resps = load_profile_channel_responses(main, _FREQS)
    assert set(resps) == {"L", "R"}  # stereo universe; R exists but is untouched
    i200 = int(np.argmin(np.abs(_FREQS - 200)))
    assert resps["L"][i200] > 4.0
    assert abs(resps["R"][i200]) < 0.01  # R got nothing


def test_global_filter_reaches_both_sides_with_one_side_trim(tmp_path):
    # The key fix: a global (no-Channel) filter applies to BOTH L and R even
    # though only L is explicitly trimmed.
    p = tmp_path / "g.txt"
    p.write_text(
        "Filter 1: ON PK Fc 1000 Hz Gain 4 dB Q 2\n"   # global -> both sides
        "Channel: L\n"
        "Filter 2: ON PK Fc 120 Hz Gain 3 dB Q 1\n",   # L only
        encoding="utf-8",
    )
    resps = load_profile_channel_responses(p, _FREQS)
    assert set(resps) == {"L", "R"}
    i1k = int(np.argmin(np.abs(_FREQS - 1000)))
    i120 = int(np.argmin(np.abs(_FREQS - 120)))
    assert resps["L"][i1k] > 3.5 and resps["R"][i1k] > 3.5  # global on both
    assert resps["L"][i120] > 2.5                            # trim on L
    assert abs(resps["R"][i120]) < 0.3                       # not on R

    # Optimize must keep R (= global only), not collapse to L's curve.
    groups, max_dev = optimize_profile(p, 48000)
    assert {g.channels for g in groups} == {("L",), ("R",)}
    rmap = {g.channels[0]: g for g in groups}
    r_curve = graphic_eq_response(rmap["R"].geq.bands, _FREQS)
    assert abs(r_curve[i120]) < 0.3  # R has no 120 Hz boost


def test_non_stereo_channels_are_refused(tmp_path):
    import pytest
    p = tmp_path / "surround.txt"
    p.write_text(
        "Channel: C\nFilter 1: ON PK Fc 1000 Hz Gain 3 dB Q 2\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        optimize_profile(p, 48000)
    with pytest.raises(ValueError):
        region_bypass_groups(p, 500.0, 2000.0)


def test_self_include_not_double_counted(tmp_path):
    # A profile that includes itself must be counted once (cycle detection),
    # so the ALL bucket matches the single combined response.
    from eq_helper.dsp.eq_response import load_profile_eq_response
    p = tmp_path / "selfref.txt"
    p.write_text(
        f"Filter 1: ON PK Fc 1000 Hz Gain 6 dB Q 2\nInclude: {p}\n",
        encoding="utf-8",
    )
    resps = load_profile_channel_responses(p, _FREQS)
    single = load_profile_eq_response(p, _FREQS)
    assert set(resps) == {ALL_CHANNELS}
    assert np.allclose(resps[ALL_CHANNELS], single)


def test_optimize_two_graphic_eqs_when_channels_differ(tmp_path):
    p = tmp_path / "bal.txt"
    p.write_text(_BALANCE, encoding="utf-8")
    groups, max_dev = optimize_profile(p, 48000)
    assert {g.channels for g in groups} == {("L",), ("R",)}
    assert max_dev < 0.1
    # Each scoped GraphicEQ reproduces its channel's response.
    resps = load_profile_channel_responses(p, _FREQS)
    for g in groups:
        ch = g.channels[0]
        approx = graphic_eq_response(g.geq.bands, _FREQS)
        assert np.max(np.abs(approx - resps[ch])) < 0.1


def test_optimize_single_when_channels_equal(tmp_path):
    # Both channels get the same EQ -> one bare GraphicEQ (no Channel: line).
    p = tmp_path / "sym.txt"
    p.write_text(
        "Channel: L\nFilter 1: ON PK Fc 1000 Hz Gain 4 dB Q 2\n"
        "Channel: R\nFilter 2: ON PK Fc 1000 Hz Gain 4 dB Q 2\n",
        encoding="utf-8",
    )
    groups, _ = optimize_profile(p, 48000)
    assert len(groups) == 1
    assert groups[0].channels is None


def test_render_channel_eqs_format():
    g = GraphicEQ(name="x", bands=[GraphicEQBand(100, 0), GraphicEQBand(1000, 3)])
    # Single all-channel -> bare GraphicEQ.
    bare = render_channel_eqs([ChannelEQ(None, g)])
    assert bare.startswith("GraphicEQ:")
    assert "Channel:" not in bare
    # Multi-channel -> scoped blocks + trailing reset.
    multi = render_channel_eqs(
        [ChannelEQ(("L",), g), ChannelEQ(("R",), g)]
    )
    lines = multi.strip().splitlines()
    assert lines[0] == "Channel: L"
    assert lines[1].startswith("GraphicEQ:")
    assert lines[2] == "Channel: R"
    assert lines[-1] == "Channel: all"  # reset so state doesn't leak


def test_bypass_per_channel_flattens_both_sides(tmp_path):
    p = tmp_path / "bal.txt"
    p.write_text(_BALANCE, encoding="utf-8")
    # Bypass a band covering the R-only 6 kHz cut and around it.
    groups = region_bypass_groups(p, 4000.0, 9000.0)
    assert {g.channels for g in groups} == {("L",), ("R",)}
    resps = load_profile_channel_responses(p, _FREQS)
    inside = (_FREQS >= 4500) & (_FREQS <= 8200)
    for g in groups:
        ch = g.channels[0]
        net = resps[ch] + graphic_eq_response(g.geq.bands, _FREQS)
        assert np.max(np.abs(net[inside])) < 0.25  # each side flat in the band
