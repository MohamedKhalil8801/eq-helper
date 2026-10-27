"""One-page calibration sheet PDF."""

import pytest

from eq_helper.admin.calibration_sheet import sheet_file_name, sheet_lines, write_calibration_sheet
from eq_helper.calibration.branding import Branding
from eq_helper.calibration.rescal import Accessory, Operator
from tests.rf.conftest import make_calibration


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
    text = " ".join(reader.pages[0].extract_text().split())
    assert "Ada Lovelace" in text and "Truthear Nova" in text
    box = reader.pages[0].mediabox
    assert abs(float(box.width) - 595) < 3 and abs(float(box.height) - 842) < 3  # A4 in points


def test_file_name_uses_branding_and_is_safe(calibration):
    name = sheet_file_name(calibration.manifest, Branding(product_name="Res/Fit"))
    assert name == "Ada Lovelace – Truthear Nova – Res-Fit calibration sheet.pdf"
