"""One-page calibration sheet PDF."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from eq_helper.admin.calibration_sheet import sheet_file_name, sheet_lines, write_calibration_sheet
from eq_helper.calibration.branding import Branding
from eq_helper.calibration.rescal import Accessory, Operator
from tests.rf.conftest import make_calibration

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def calibration():
    cal = make_calibration(customer="Ada Lovelace", model="Nova")
    cal.manifest.accessories = [Accessory("tips", "Spinfit W1 M", "customer"),
                                Accessory("dac", "Apple dongle", "operator")]
    cal.manifest.operator = Operator("Mohamed")
    cal.manifest.coupler = "IEC-711 clone"
    return cal


def test_sheet_lines_cover_everything(calibration):
    text = "\n".join(sheet_lines(calibration, Branding(), calibration.measurements()))
    for expected in ("Ada Lovelace", "Truthear Nova", "2026-10-27", "IEC-711 clone",
                     "Spinfit W1 M (yours)", "Apple dongle (provided by Mohamed)",
                     "Left", "Right", "Hz", "4 fits"):
        assert expected in text


def test_pdf_written_one_page_with_text(qapp, tmp_path, calibration):
    path = write_calibration_sheet(tmp_path / "sheet.pdf", calibration, Branding())
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 3000
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert abs(float(box.width) - 595) < 3 and abs(float(box.height) - 842) < 3  # A4 in points
    if sys.platform == "win32" and qapp.platformName() == "offscreen":
        pytest.skip("Qt's offscreen platform has no fonts on Windows; see the native-platform test")
    text = " ".join(reader.pages[0].extract_text().split())
    assert "Ada Lovelace" in text and "Truthear Nova" in text


SHEET_PROBE = """
import sys
from PySide6.QtWidgets import QApplication
app = QApplication([])
from eq_helper.admin.calibration_sheet import write_calibration_sheet
from eq_helper.calibration.branding import Branding
from tests.rf.conftest import make_calibration
write_calibration_sheet(sys.argv[1], make_calibration(customer="Ada Lovelace", model="Nova"), Branding())
"""


@pytest.mark.skipif(sys.platform != "win32", reason="checks the operator's real Windows font setup")
def test_pdf_text_with_native_windows_fonts(tmp_path):
    """EQ Helper runs with Qt's native Windows platform: the sheet's text must be extractable."""
    pypdf = pytest.importorskip("pypdf")
    out = tmp_path / "sheet.pdf"
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-c", SHEET_PROBE, str(out)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    text = " ".join(pypdf.PdfReader(str(out)).pages[0].extract_text().split())
    assert "Ada Lovelace" in text and "Truthear Nova" in text


def test_file_name_uses_branding_and_is_safe(calibration):
    name = sheet_file_name(calibration.manifest, Branding(product_name="Res/Fit"))
    assert name == "Ada Lovelace – Truthear Nova – Res-Fit calibration sheet.pdf"
