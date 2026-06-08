"""region_bypass_groups flattens the net EQ inside a band, untouched outside."""

import numpy as np

from eq_helper.dsp.eq_optimize import region_bypass_groups
from eq_helper.dsp.eq_response import load_profile_eq_response
from eq_helper.dsp.graphic_eq import graphic_eq_response


def test_bypass_flattens_band(tmp_path):
    p = tmp_path / "prof.txt"
    p.write_text(
        "Filter 1: ON PK Fc 3000 Hz Gain 8 dB Q 1.0\n"
        "Filter 2: ON PK Fc 6000 Hz Gain -6 dB Q 1.5\n",
        encoding="utf-8",
    )
    freqs = np.geomspace(20, 20000, 2000)
    groups = region_bypass_groups(p, 4000.0, 7000.0, freqs)
    assert len(groups) == 1 and groups[0].channels is None  # no channels => bare
    C = graphic_eq_response(groups[0].geq.bands, freqs)
    R = load_profile_eq_response(p, freqs)
    net = R + C
    inside = (freqs >= 4500) & (freqs <= 6500)
    outside = (freqs < 3000) | (freqs > 9000)
    assert np.max(np.abs(net[inside])) < 0.2
    assert np.max(np.abs(C[outside])) < 0.2
