"""Unit tests for pure APO include-list text transforms (Qt-free, no disk)."""

from __future__ import annotations

import sys

import pytest

from eq_helper.apo.include_file import (
    IncludeRef,
    add_include,
    add_include_under_device,
    parse_includes,
    paths_match,
    remove_include,
    reorder_includes,
    set_active_include,
    set_active_include_in_device,
    set_include_enabled,
)


class TestParse:
    def test_reads_enabled_and_commented(self) -> None:
        text = "Device: X\nInclude: a.txt\n# Include: b.txt\n#Include: c.txt\nPreamp: -3 dB\n"
        assert parse_includes(text) == [
            IncludeRef(path="a.txt", enabled=True, line_no=1),
            IncludeRef(path="b.txt", enabled=False, line_no=2),
            IncludeRef(path="c.txt", enabled=False, line_no=3),
        ]

    def test_no_includes(self) -> None:
        assert parse_includes("Preamp: 0 dB\nFilter 1: ON PK Fc 1000 Hz Gain 1 dB Q 1\n") == []


class TestPathsMatch:
    def test_case_and_slash_insensitive(self) -> None:
        assert paths_match(r"C:\A\B.txt", "c:/a/b.txt")
        assert paths_match("x.txt", "  X.TXT  ")
        assert not paths_match("a.txt", "b.txt")


class TestSetActiveInclude:
    def test_radio_activates_one_comments_others(self) -> None:
        text = "Include: a.txt\n# Include: b.txt\nInclude: keep.txt\n"
        out = set_active_include(text, {"a.txt", "b.txt"}, "b.txt")
        assert "# Include: a.txt\n" in out
        assert "Include: b.txt\n" in out and "# Include: b.txt" not in out
        assert "Include: keep.txt\n" in out  # unmanaged untouched

    def test_none_deactivates_all_managed(self) -> None:
        text = "Include: a.txt\nInclude: b.txt\n"
        out = set_active_include(text, {"a.txt", "b.txt"}, None)
        assert out == "# Include: a.txt\n# Include: b.txt\n"

    def test_preserves_crlf_and_other_lines(self) -> None:
        text = "Device: X\r\nInclude: a.txt\r\n# Include: b.txt\r\n"
        out = set_active_include(text, {"a.txt", "b.txt"}, "b.txt")
        assert "Device: X\r\n" in out
        assert "# Include: a.txt\r\n" in out
        assert "Include: b.txt\r\n" in out


class TestSetIncludeEnabled:
    def test_enable_and_disable(self) -> None:
        text = "# Include: a.txt\nInclude: b.txt\n"
        assert "Include: a.txt\n" in set_include_enabled(text, "a.txt", True)
        assert "# Include: b.txt\n" in set_include_enabled(text, "b.txt", False)

    def test_idempotent(self) -> None:
        text = "Include: a.txt\n"
        assert set_include_enabled(text, "a.txt", True) == text


class TestAddInclude:
    def test_append_enabled(self) -> None:
        assert add_include("Include: a.txt\n", "b.txt") == "Include: a.txt\nInclude: b.txt\n"

    def test_append_disabled(self) -> None:
        assert add_include("", "b.txt", enabled=False) == "# Include: b.txt\n"

    def test_file_without_trailing_newline(self) -> None:
        assert add_include("Include: a.txt", "b.txt") == "Include: a.txt\nInclude: b.txt\n"


class TestAddIncludeUnderDevice:
    def test_inserts_after_last_include_in_scope(self) -> None:
        text = (
            "Device: Headset USB {abc-123}\n"
            "Include: a.txt\n"
            "Device: Speakers {def-456}\n"
            "Include: s.txt\n"
        )
        out = add_include_under_device(text, "Headset USB", "b.txt")
        lines = out.splitlines()
        # b.txt must be right after a.txt and before the Speakers device line.
        assert lines.index("Include: b.txt") == lines.index("Include: a.txt") + 1
        assert lines.index("Include: b.txt") < lines.index("Device: Speakers {def-456}")

    def test_inserts_after_device_line_when_no_includes(self) -> None:
        text = "Device: HD600\nDevice: Speakers\nInclude: s.txt\n"
        out = add_include_under_device(text, "HD600", "n.txt")
        lines = out.splitlines()
        assert lines[1] == "Include: n.txt"

    def test_missing_device_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError):
            add_include_under_device("Device: X\n", "Nope", "n.txt")

    def test_disabled_insert(self) -> None:
        out = add_include_under_device("Device: X\n", "X", "n.txt", enabled=False)
        assert "# Include: n.txt" in out


class TestRemoveInclude:
    def test_removes_matching_only(self) -> None:
        text = "Include: a.txt\n# Include: b.txt\nInclude: c.txt\n"
        out = remove_include(text, "b.txt")
        assert "b.txt" not in out
        assert "Include: a.txt\n" in out and "Include: c.txt\n" in out

    def test_noop_when_absent(self) -> None:
        text = "Include: a.txt\n"
        assert remove_include(text, "z.txt") == text


class TestReorder:
    def test_reorders_in_place_preserving_other_lines(self) -> None:
        text = "Include: a.txt\nPreamp: 0 dB\n# Include: b.txt\nInclude: c.txt\n"
        out = reorder_includes(text, ["c.txt", "b.txt", "a.txt"])
        lines = out.splitlines()
        # Non-include line stays in its slot (index 1).
        assert lines[1] == "Preamp: 0 dB"
        # Include slots now hold c, b, a in order; b keeps its disabled state.
        assert lines[0] == "Include: c.txt"
        assert lines[2] == "# Include: b.txt"
        assert lines[3] == "Include: a.txt"

    def test_unknown_paths_kept_at_end(self) -> None:
        text = "Include: a.txt\nInclude: b.txt\n"
        out = reorder_includes(text, ["b.txt"])
        lines = out.splitlines()
        assert lines[0] == "Include: b.txt"
        assert lines[1] == "Include: a.txt"

    def test_preserves_crlf_endings(self) -> None:
        text = "Include: a.txt\r\nInclude: b.txt\r\n"
        out = reorder_includes(text, ["b.txt", "a.txt"])
        assert out == "Include: b.txt\r\nInclude: a.txt\r\n"


class TestSetActiveInDevice:
    def test_only_toggles_target_device_scope(self) -> None:
        # a1.txt appears in BOTH device A and device B; activating a2 in A must
        # NOT touch B's a1 or the global include.
        text = (
            "Include: C:/x/glob.txt\n"
            "Device: A\n"
            "Include: C:/x/a1.txt\n"
            "# Include: C:/x/a2.txt\n"
            "Device: B\n"
            "Include: C:/x/a1.txt\n"
        )
        out = set_active_include_in_device(text, "A", "C:/cfg", "C:/x/a2.txt")
        lines = out.splitlines()
        assert lines[0] == "Include: C:/x/glob.txt"     # global untouched
        assert lines[2] == "# Include: C:/x/a1.txt"      # A.a1 -> off (radio)
        assert lines[3] == "Include: C:/x/a2.txt"        # A.a2 -> on
        assert lines[5] == "Include: C:/x/a1.txt"        # B.a1 untouched

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths (Equalizer APO is Windows-only)")
    def test_resolves_relative_include_against_base_dir(self) -> None:
        text = "Device: A\n# Include: rel/p.txt\n"
        out = set_active_include_in_device(text, "A", "C:/cfg", "C:/cfg/rel/p.txt")
        assert "Include: rel/p.txt" in out
        assert "# Include: rel/p.txt" not in out

    def test_none_deactivates_all_in_scope(self) -> None:
        text = "Device: A\nInclude: C:/x/a.txt\n"
        out = set_active_include_in_device(text, "A", "C:/cfg", None)
        assert "# Include: C:/x/a.txt" in out

    def test_matches_exact_device_by_full_line_not_short_name(self) -> None:
        # Two physically distinct devices share the friendly name "Headset"
        # (different GUIDs). Activating a preset under the AAAA device must NOT
        # touch the BBBB device's active include.
        text = (
            "Device: Headset {AAAA}\n"
            "Include: C:/x/a1.txt\n"
            "# Include: C:/x/a2.txt\n"
            "Device: Headset {BBBB}\n"
            "Include: C:/x/b1.txt\n"
        )
        out = set_active_include_in_device(text, "Headset {AAAA}", "C:/cfg", "C:/x/a2.txt")
        lines = out.splitlines()
        assert lines[1] == "# Include: C:/x/a1.txt"  # AAAA a1 -> off
        assert lines[2] == "Include: C:/x/a2.txt"     # AAAA a2 -> on
        assert lines[4] == "Include: C:/x/b1.txt"     # BBBB untouched (not corrupted)


class TestLineEndings:
    def test_add_include_uses_crlf_when_file_is_crlf(self) -> None:
        assert add_include("Include: a.txt\r\n", "b.txt") == "Include: a.txt\r\nInclude: b.txt\r\n"

    def test_add_under_device_preserves_crlf(self) -> None:
        out = add_include_under_device("Device: X\r\nInclude: a.txt\r\n", "X", "b.txt")
        assert "Include: b.txt\r\n" in out

    def test_add_under_device_stops_at_bare_device_reset(self) -> None:
        text = "Device: X\nInclude: a.txt\nDevice:\nInclude: g.txt\n"
        out = add_include_under_device(text, "X", "b.txt")
        lines = out.splitlines()
        assert lines.index("Include: b.txt") == lines.index("Include: a.txt") + 1
        assert lines.index("Include: b.txt") < lines.index("Device:")
