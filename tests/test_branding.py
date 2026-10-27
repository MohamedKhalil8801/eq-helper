"""One branding setting drives the customer app's name, byline, contact and logo."""

import json
from pathlib import Path

from eq_helper.calibration.branding import DEFAULT_BRANDING_PATH, Branding, load_branding

ROOT = Path(__file__).resolve().parent.parent


def test_defaults_and_bundled_file_agree():
    bundled = load_branding()
    assert DEFAULT_BRANDING_PATH == ROOT / "resonancefit" / "assets" / "branding.json"
    assert bundled == Branding()
    assert bundled.product_name == "ResonanceFit"
    assert bundled.byline == "by Mohamed Khalil"
    assert bundled.title == "ResonanceFit, by Mohamed Khalil"


def test_overrides_unknown_keys_and_bad_files(tmp_path):
    p = tmp_path / "branding.json"
    p.write_text(json.dumps({"product_name": "EarTune", "contact_url": "https://x.example",
                             "logo": "logo.png", "future": 1}))
    b = load_branding(p)
    assert (b.product_name, b.byline, b.contact_url) == ("EarTune", "by Mohamed Khalil", "https://x.example")
    assert b.logo_path == tmp_path / "logo.png"
    p.write_text("{not json")
    assert load_branding(p) == Branding()
    assert load_branding(tmp_path / "missing.json") == Branding()
    p.write_text(json.dumps({"product_name": 5}))
    assert load_branding(p).product_name == "ResonanceFit"


def test_file_stem_is_filesystem_safe():
    b = Branding(product_name='Res/Fit: "Pro"?')
    assert b.file_stem == "Res-Fit- -Pro--"
    assert Branding().file_stem == "ResonanceFit"


def test_customer_app_never_hard_codes_product_name():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "resonancefit").rglob("*.py")
                 if "ResonanceFit" in p.read_text(encoding="utf-8")]
    assert offenders == []
