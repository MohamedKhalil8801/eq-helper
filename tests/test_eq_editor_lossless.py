"""Loading an APO EQ file must not reformat or drop anything.

Saving is surgical: only the fields the user actually edited are rewritten,
so untouched lines (comments, Channel:/Include:, unknown syntax, precision,
trailing zeros, line endings) survive byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

SAMPLE = """Preamp: -19.2 dB
Filter 1: ON PK Fc 20 Hz Gain 3.42 dB Q 0.401
Filter 2: ON PK Fc 62 Hz Gain 2.03 dB Q 0.709
Filter 3: ON PK Fc 161 Hz Gain -0.58 dB Q 1.088
Filter 4: ON PK Fc 328 Hz Gain 0.39 dB Q 0.683
Filter 5: ON PK Fc 399 Hz Gain -0.38 dB Q 2.791
Filter 6: ON PK Fc 404 Hz Gain 1.84 dB Q 0.400
Filter 7: ON PK Fc 823 Hz Gain 1.65 dB Q 1.021
Filter 8: ON PK Fc 1053 Hz Gain -0.52 dB Q 2.435
Filter 9: ON PK Fc 1971 Hz Gain 1.94 dB Q 1.584
Filter 10: ON PK Fc 2553 Hz Gain 2.48 dB Q 2.336
Filter 11: ON PK Fc 3223 Hz Gain -1.67 dB Q 1.770
Filter 12: ON PK Fc 4769 Hz Gain -5.12 dB Q 2.131
Filter 13: ON PK Fc 5629 Hz Gain 0.83 dB Q 3.966
Filter 14: ON PK Fc 6608 Hz Gain -3.61 dB Q 3.012
Filter 15: ON PK Fc 7792 Hz Gain -3.19 dB Q 3.298
Filter 16: ON PK Fc 9507 Hz Gain 7.35 dB Q 0.708
Filter 17: ON PK Fc 10840 Hz Gain -5.97 dB Q 2.982
Filter 18: ON LSC Fc 13650 Hz Gain 5.73 dB Q 1.627
Filter 19: ON HSC Fc 16000 Hz Gain 2.52 dB Q 1.598
Filter 20: ON PK Fc 16000 Hz Gain 16.00 dB Q 0.628
# my note
Channel: L
Filter 21: ON PK Fc 12000 Hz Gain 1.25 dB BW Oct 1.0
GraphicEQ: 100 3.0; 1000 -2.0; 8000 1.5
"""


def _write(path: Path, text: str = SAMPLE) -> Path:
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _panel(qtbot, path: Path):
    from eq_helper.audio.engine import AudioEngine
    from eq_helper.ui.eq_mode_panel import EQModePanel

    panel = EQModePanel(AudioEngine(sample_rate=48000))
    qtbot.addWidget(panel)
    panel.set_eq_file(str(path))
    return panel


def _save(panel) -> None:
    panel._save_timer.stop()
    panel._auto_save()


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", newline="").splitlines()


def test_open_and_close_never_writes(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    panel = _panel(qtbot, path)
    panel.cleanup()

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime
    assert not path.with_suffix(".txt.eqhelper.bak").exists()


def test_no_change_save_is_byte_identical(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)
    _save(panel)
    assert path.read_text(encoding="utf-8", newline="") == SAMPLE


def test_edit_gain_rewrites_only_that_field(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)

    panel._rows[0].gain_spin.setValue(4.0)
    _save(panel)

    lines = _lines(path)
    assert lines[1] == "Filter 1: ON PK Fc 20 Hz Gain 4 dB Q 0.401"
    assert all(
        a == b for i, (a, b) in enumerate(zip(SAMPLE.splitlines(), lines)) if i != 1
    )
    assert len(lines) == len(SAMPLE.splitlines())


def test_edit_q_keeps_other_tokens_verbatim(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)

    panel._rows[5].q_spin.setValue(0.6)
    _save(panel)

    assert _lines(path)[6] == "Filter 6: ON PK Fc 404 Hz Gain 1.84 dB Q 0.6"


def test_untouched_precision_is_not_rounded(qtbot, tmp_path) -> None:
    text = (
        "Preamp: -1.2345 dB\n"
        "Filter 1: ON PK Fc 1053.334 Hz Gain 16.004 dB Q 0.4012\n"
        "Filter 2: ON PK Fc 62.125 Hz Gain 2.03 dB Q 0.7093\n"
    )
    path = _write(tmp_path / "eq.txt", text)
    panel = _panel(qtbot, path)

    panel._rows[0].gain_spin.setValue(15.0)
    _save(panel)

    lines = _lines(path)
    assert lines[1] == "Filter 1: ON PK Fc 1053.334 Hz Gain 15 dB Q 0.4012"
    assert lines[2] == "Filter 2: ON PK Fc 62.125 Hz Gain 2.03 dB Q 0.7093"
    assert lines[0] == "Preamp: -1.2345 dB"


def test_values_outside_editor_display_ranges_are_preserved(qtbot, tmp_path) -> None:
    text = (
        "Filter 1: ON PK Fc 5 Hz Gain 50 dB Q 42\n"
        "Filter 2: ON PK Fc 50000 Hz Gain -40 dB Q 0.02\n"
    )
    path = _write(tmp_path / "eq.txt", text)
    panel = _panel(qtbot, path)

    panel._rows[0].gain_spin.setValue(3.0)
    _save(panel)

    lines = _lines(path)
    assert lines[0] == "Filter 1: ON PK Fc 5 Hz Gain 3 dB Q 42"
    assert lines[1] == "Filter 2: ON PK Fc 50000 Hz Gain -40 dB Q 0.02"


def test_lsc_hsc_types_survive_edit(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)
    assert panel._rows[17].type_combo.currentText() == "LSC"
    assert panel._rows[18].type_combo.currentText() == "HSC"

    panel._rows[17].gain_spin.setValue(6.0)
    _save(panel)

    assert _lines(path)[18] == "Filter 18: ON LSC Fc 13650 Hz Gain 6 dB Q 1.627"


def test_remove_filter_keeps_other_lines_and_numbering(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)

    panel._rows[2].remove_btn.click()
    _save(panel)

    lines = _lines(path)
    assert "Filter 3:" not in "\n".join(lines)
    assert "Filter 2: ON PK Fc 62 Hz Gain 2.03 dB Q 0.709" in lines
    assert "Filter 4: ON PK Fc 328 Hz Gain 0.39 dB Q 0.683" in lines


def test_add_filter_numbers_after_last_and_stays_with_filters(
    qtbot, tmp_path
) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)

    panel.btn_add.click()
    _save(panel)

    lines = _lines(path)
    new_idx = lines.index("Filter 22: ON PK Fc 1000 Hz Gain 0 dB Q 1")
    assert lines[new_idx - 1].startswith("Filter 21:")
    assert lines[new_idx + 1].startswith("GraphicEQ:")


def test_clear_removes_filters_only(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)

    panel.btn_clear.click()
    _save(panel)

    text = path.read_text(encoding="utf-8", newline="")
    assert "Preamp: -19.2 dB" in text
    assert "# my note" in text
    assert "Channel: L" in text
    assert "GraphicEQ:" in text
    assert "Filter 1:" not in text


def test_graphic_eq_edit_preserves_filter_lines(qtbot, tmp_path) -> None:
    path = _write(tmp_path / "eq.txt")
    panel = _panel(qtbot, path)

    geqs = panel.graphic_eqs()
    geqs[0].bands[0].gain_db = 3.5
    panel.set_graphic_eqs(geqs)
    _save(panel)

    lines = _lines(path)
    assert lines[-1] == "GraphicEQ: 100 3.5; 1000 -2.0; 8000 1.5"
    assert lines[1:21] == SAMPLE.splitlines()[1:21]


def test_crlf_line_endings_preserved(qtbot, tmp_path) -> None:
    path = tmp_path / "eq.txt"
    path.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))
    panel = _panel(qtbot, path)

    panel._rows[0].gain_spin.setValue(4.0)
    _save(panel)

    raw = path.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert b"Gain 4 dB" in raw


def test_cp1252_encoding_preserved(qtbot, tmp_path) -> None:
    path = tmp_path / "eq.txt"
    path.write_bytes(
        "Preamp: -3 dB\r\n# Caf\xe9 note\r\nFilter 1: ON PK Fc 100 Hz Gain 2 dB Q 1\r\n"
        .encode("cp1252")
    )
    panel = _panel(qtbot, path)

    panel._rows[0].gain_spin.setValue(3.0)
    _save(panel)

    raw = path.read_bytes()
    assert b"# Caf\xe9 note" in raw
    assert b"\xef\xbf\xbd" not in raw  # no UTF-8 replacement characters
