"""Smoke tests for the MicPanel UI widget."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")


def test_mic_panel_boots_and_has_controls(qtbot) -> None:
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    assert window.mic_panel is not None
    assert window.mic_panel.monitor_check is not None
    assert window.mic_panel.btn_record is not None
    assert window.mic_panel.btn_stop is not None
    assert window.mic_panel.btn_play is not None

    window.mic_panel.cleanup()
    window.close()


def test_monitor_toggle_updates_engine(qtbot) -> None:
    from eq_helper.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    window.mic_panel.monitor_check.setChecked(True)
    assert window.engine.state.snapshot().mic_monitoring is True
    window.mic_panel.monitor_check.setChecked(False)
    assert window.engine.state.snapshot().mic_monitoring is False

    window.mic_panel.cleanup()
    window.close()
