"""Uncaught errors are written to a log in the app's data folder."""

import sys

from resonancefit.app import log_errors


def test_uncaught_errors_go_to_the_log(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: seen.append(exc))
    path = tmp_path / "data" / "errors.log"
    log_errors(path, "1.2.3")
    try:
        raise ValueError("the button broke")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    text = path.read_text(encoding="utf-8")
    assert "1.2.3" in text and "ValueError: the button broke" in text
    assert len(seen) == 1  # still reported as before
