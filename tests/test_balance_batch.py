"""Balance EQ batch: filename parsing, set detection, 3xN generation, swap."""

import re
from pathlib import Path

import numpy as np

from eq_helper.dsp.balance_eq import (
    detect_measurement_sets,
    effective_other_channel,
    generate_balance_batch,
    parse_side_index,
)
from eq_helper.dsp.eq_response import detect_lr_swap, load_profile_lr_swap


def test_parse_side_index():
    assert parse_side_index("L 1.txt") == ("L", 1)
    assert parse_side_index("R 3.txt") == ("R", 3)
    assert parse_side_index("left2.csv") == ("L", 2)
    assert parse_side_index("Right_10.txt") == ("R", 10)
    assert parse_side_index("L.txt") == ("L", None)
    # not a side label -> None (don't mistake "reference" for Right)
    assert parse_side_index("reference.txt") is None
    assert parse_side_index("balance.txt") is None


def test_detect_measurement_sets(tmp_path):
    for n in ("L 1.txt", "L 2.txt", "L 3.txt", "R 1.txt", "R 2.txt", "notes.md"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    left, right = detect_measurement_sets(tmp_path)
    assert [i for i, _ in left] == [1, 2, 3]
    assert [i for i, _ in right] == [1, 2]
    assert all(p.name.startswith("L") for _, p in left)


def test_effective_other_channel():
    assert effective_other_channel("L") == "R"
    assert effective_other_channel("R") == "L"
    assert effective_other_channel("L", invert=True) == "L"
    assert effective_other_channel("R", invert=True) == "R"


def _write_meas(path, peak_hz, peak_db):
    f = np.geomspace(20, 20000, 400)
    lf = np.log10(f)
    db = 85 + peak_db * np.exp(-((lf - np.log10(peak_hz)) ** 2) / (2 * 0.05 ** 2))
    path.write_text(
        "Frequency (Hz),Raw (dB)\n"
        + "\n".join(f"{a:.3f},{b:.4f}" for a, b in zip(f, db)),
        encoding="utf-8",
    )


def test_generate_balance_batch_counts_and_names(tmp_path):
    for i in (1, 2, 3):
        _write_meas(tmp_path / f"L {i}.txt", 1000 + 200 * i, 3.0)
    for j in (1, 2):
        _write_meas(tmp_path / f"R {j}.txt", 5000 + 300 * j, -2.0)
    left, right = detect_measurement_sets(tmp_path)
    results, skipped = generate_balance_batch(left, right, "L", 48000)
    assert len(results) == 6  # 3 x 2
    assert skipped == []
    names = [n for n, _, _ in results]
    # other side first, reference second, trailing "(0.NN)" score
    assert all(re.match(r"^R\d L\d \(\d\.\d\d\)$", n) for n in names)
    assert any(n.startswith("R1 L1 ") for n in names)
    assert any(n.startswith("R2 L3 ") for n in names)
    # reference=L, no invert -> one block scoped to R
    assert all(len(eqs) == 1 and eqs[0].channels == ("R",) for _, eqs, _ in results)


def test_generate_balance_batch_invert_flips_channel(tmp_path):
    _write_meas(tmp_path / "L 1.txt", 1200, 3.0)
    _write_meas(tmp_path / "R 1.txt", 5000, -2.0)
    left, right = detect_measurement_sets(tmp_path)
    results, skipped = generate_balance_batch(left, right, "L", 48000, invert=True)
    # name still reflects the physical pairing, channel is inverted to L
    assert results[0][0].startswith("R1 L1 (")
    assert results[0][1][0].channels == ("L",)


def test_generate_balance_batch_midpoint(tmp_path):
    for i in (1, 2):
        _write_meas(tmp_path / f"L {i}.txt", 1000 + 200 * i, 3.0)
    _write_meas(tmp_path / "R 1.txt", 5000, -2.0)
    left, right = detect_measurement_sets(tmp_path)
    results, skipped = generate_balance_batch(left, right, "L", 48000, method="midpoint")
    assert len(results) == 2 and skipped == []
    for name, eqs, _md in results:
        # two channel-scoped blocks (L and R), L-first name
        assert {e.channels for e in eqs} == {("L",), ("R",)}
        assert re.match(r"^L\d R\d \(\d\.\d\d\)$", name), name


def test_generate_balance_batch_skips_bad_pair(tmp_path):
    _write_meas(tmp_path / "L 1.txt", 1200, 3.0)
    _write_meas(tmp_path / "R 1.txt", 5000, -2.0)
    # R 2 covers a band that doesn't overlap [20, 20000] after clamping
    f = np.geomspace(22000, 40000, 50)
    (tmp_path / "R 2.txt").write_text(
        "Frequency (Hz),Raw (dB)\n" + "\n".join(f"{a:.3f},80.0" for a in f),
        encoding="utf-8",
    )
    left, right = detect_measurement_sets(tmp_path)
    results, skipped = generate_balance_batch(left, right, "L", 48000)
    # R1 L1 still generated; R2 L1 skipped, not the whole batch
    assert len(results) == 1 and results[0][0].startswith("R1 L1 (")
    assert len(skipped) == 1 and skipped[0][0] == "R2 L1"


def test_detect_lr_swap_duplicate_include_parity(tmp_path):
    from eq_helper.dsp.eq_response import load_profile_lr_swap
    swap = tmp_path / "swap.txt"
    swap.write_text("Copy: L=R R=L\n", encoding="utf-8")
    # included twice (non-cyclic) -> APO applies it twice -> NOT swapped
    twice = tmp_path / "twice.txt"
    twice.write_text(f"Include: {swap}\nInclude: {swap}\n", encoding="utf-8")
    assert load_profile_lr_swap(twice) is False
    # included once -> swapped
    once = tmp_path / "once.txt"
    once.write_text(f"Include: {swap}\n", encoding="utf-8")
    assert load_profile_lr_swap(once) is True


def test_fallback_labels_unique_on_duplicate_index(tmp_path):
    # 'L 1.txt' and 'L1.txt' both parse to (L, 1); labels must stay unique so
    # the seal-marker/metrics key doesn't collide.
    from eq_helper.dsp.balance_eq import _fallback_labels
    for n in ("L 1.txt", "L1.txt", "R 1.txt"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    left, right = detect_measurement_sets(tmp_path)
    labels = _fallback_labels(left)
    assert len(set(labels)) == len(left)  # unique


def test_seal_within_side_not_across(tmp_path):
    # A genuinely bass-light R side (all R rolled off vs L) must NOT be flagged
    # (that's a genuine L/R difference, not seal loss). A single R outlier IS.
    def rolloff(path, sub_db):
        f = np.geomspace(20, 20000, 800)
        db = np.full_like(f, 80.0)
        db[f <= 60] = 80.0 + sub_db
        path.write_text(
            "Frequency (Hz),Raw (dB)\n" + "\n".join(f"{a:.3f},{b:.4f}" for a, b in zip(f, db)),
            encoding="utf-8",
        )
    # L flat; all R uniformly bass-light by 9 dB (genuine channel difference)
    for i in (1, 2, 3):
        rolloff(tmp_path / f"L {i}.txt", 0.0)
        rolloff(tmp_path / f"R {i}.txt", -9.0)
    left, right = detect_measurement_sets(tmp_path)
    results, _ = generate_balance_batch(left, right, "L", 48000)
    assert not any("!!!" in n for n, _, _ in results)  # no false seal flag

    # Now make R2 an outlier among R (a lost seal on that one seating).
    rolloff(tmp_path / "R 2.txt", -17.0)  # 8 dB below the other R captures
    left, right = detect_measurement_sets(tmp_path)
    results, _ = generate_balance_batch(left, right, "L", 48000)
    flagged = {n for n, _, _ in results if "R2!!!" in n}
    assert flagged  # R2 flagged as the within-side outlier


def test_detect_lr_swap_active(tmp_path):
    assert detect_lr_swap("Copy: L=R R=L\nFilter: ON PK Fc 1000 Hz Gain 1 dB Q 1\n")
    assert not detect_lr_swap("# Copy: L=R R=L\n")  # commented
    assert not detect_lr_swap("Copy: L=R\n")  # not a full swap
    # two swaps cancel (parity)
    assert not detect_lr_swap("Copy: L=R R=L\nCopy: L=R R=L\n")


def test_load_profile_lr_swap_across_includes(tmp_path):
    inc = tmp_path / "inner.txt"
    inc.write_text("Filter: ON PK Fc 1000 Hz Gain 1 dB Q 1\n", encoding="utf-8")
    top = tmp_path / "top.txt"
    top.write_text(f"Copy: L=R R=L\nInclude: {inc}\n", encoding="utf-8")
    assert load_profile_lr_swap(top) is True
