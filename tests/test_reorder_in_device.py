"""Device-scoped preset reorder transform + PresetLibrary.reorder_presets."""

from pathlib import Path

from eq_helper.apo.include_file import reorder_includes_in_device
from eq_helper.apo.presets import PresetLibrary

DEV = "Device: HD600 {0123abcd-0000-0000-0000-000000000000}"
SPK = "Device: Speakers {0123abcd-1111-1111-1111-111111111111}"


def test_reorder_within_device_scope():
    text = (
        f"{DEV}\n"
        "Include: a.txt\n"
        "Include: b.txt\n"
        "Include: c.txt\n"
        f"{SPK}\n"
        "Include: z.txt\n"
    )
    out = reorder_includes_in_device(text, DEV[len("Device: "):], "/cfg",
                                     ["c.txt", "a.txt", "b.txt"])
    lines = [ln for ln in out.splitlines() if ln.startswith("Include:")]
    assert lines[:3] == ["Include: c.txt", "Include: a.txt", "Include: b.txt"]
    assert lines[3] == "Include: z.txt"  # other device untouched


def test_reorder_presets_via_library(tmp_path):
    (tmp_path / "config.txt").write_text(f"{DEV}\n", encoding="utf-8")
    lib = PresetLibrary(tmp_path)
    a = lib.create_preset("HD600", "A")
    b = lib.create_preset("HD600", "B")
    c = lib.create_preset("HD600", "C")
    # reverse the order
    lib.reorder_presets("HD600", [c.path, b.path, a.path])
    names = [p.name for p in lib.list_presets("HD600")]
    assert names == ["C", "B", "A"]
