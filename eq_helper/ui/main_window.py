"""Main application window: assembles panels, wires persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from eq_helper.apo.errors import ApoError
from eq_helper.apo.paths import MAIN_CONFIG_NAME, resolve_apo_config_dir
from eq_helper.audio.engine import AudioEngine
from eq_helper.storage.settings import (
    default_settings_path,
    load_settings,
    save_settings,
)
from eq_helper.ui.apo_bridge import ApoBridge, _key
from eq_helper.ui.audio_panel import AudioPanel
from eq_helper.ui.band_panel import BandPanel
from eq_helper.ui.eq_library_panel import EqLibraryPanel
from eq_helper.ui.eq_mode_panel import EQModePanel
from eq_helper.ui.graphic_eq_panel import GraphicEQPanel
from eq_helper.ui.mic_panel import MicPanel
from eq_helper.ui.profile_panel import ProfilePanel
from eq_helper.ui.spectrum_view import SpectrumView
from eq_helper.ui.test_songs_panel import TestSongsDialog, TestSongsPanel
from eq_helper.ui.widgets import CollapsibleSection


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EQ Helper")
        self.resize(1100, 720)

        self._settings = load_settings()
        self._settings_path = default_settings_path()

        self.engine = AudioEngine()
        self.audio_panel = AudioPanel(self.engine)
        self.band_panel = BandPanel(self.engine)
        self.profile_panel = ProfilePanel(
            get_override=lambda: self._settings.get("apo_config_dir_override"),
            get_isolation_band=lambda: (
                self.band_panel.fc_spin.value(),
                self.band_panel.bw_spin.value(),
            ),
        )
        self.profile_panel.set_sample_rate(self.engine.sample_rate)
        self.spectrum_view = SpectrumView(self.engine)
        self.mic_panel = MicPanel(self.engine)
        self.test_songs_panel = TestSongsPanel()
        self.test_songs_dialog = TestSongsDialog(self.test_songs_panel, self)
        self.test_songs_button = QPushButton("Test Songs…")
        self.eq_panel = EQModePanel(self.engine)
        self.graphic_eq_panel = GraphicEQPanel()

        # EQ Filters + Profiles merge into one drill-down panel: browse presets
        # /files, double-click to edit a file (full height), Back to return.
        self.eq_library = EqLibraryPanel(self.profile_panel, self.eq_panel)

        # Fold the less-used Microphone / Frequency-isolation panels into
        # collapsible sections (their group-box title moves to the section header).
        self.mic_panel.setTitle("")
        self.mic_panel.setFlat(True)
        self.mic_section = CollapsibleSection(
            "Microphone", self.mic_panel,
            expanded=bool(self._settings.get("mic_section_expanded", True)),
        )
        self.band_panel.setTitle("")
        self.band_panel.setFlat(True)
        self.band_section = CollapsibleSection(
            "Frequency isolation", self.band_panel,
            expanded=bool(self._settings.get("band_section_expanded", True)),
        )
        self.mic_section.toggled.connect(self._schedule_save)
        self.band_section.toggled.connect(self._schedule_save)

        # Left column — scrollable so nothing clips on shorter windows.
        left_col = QVBoxLayout()
        left_col.addWidget(self.audio_panel)
        left_col.addWidget(self.mic_section)
        left_col.addWidget(self.band_section)
        left_col.addWidget(self.eq_library, 1)
        left_col.addWidget(self.test_songs_button)
        left_container = QWidget()
        left_container.setLayout(left_col)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_container)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(380)

        # Right column: vertical splitter with spectrum (2/3) + graphic EQ (1/3).
        self._right_splitter = QSplitter(Qt.Vertical)
        self._right_splitter.addWidget(self.spectrum_view)
        self._right_splitter.addWidget(self.graphic_eq_panel)
        self._right_splitter.setStretchFactor(0, 2)
        self._right_splitter.setStretchFactor(1, 1)

        layout = QHBoxLayout()
        layout.addWidget(left_scroll, 0)
        layout.addWidget(self._right_splitter, 1)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        # Link graphic EQ x-axis to spectrum view x-axis.
        self.graphic_eq_panel.link_x_axis(self.spectrum_view._plot.getViewBox())

        # Debounced save timer — must exist before _wire() connects signals
        # or before apply_settings() fires signals during restore.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_now)

        # Two-way live sync with Equalizer APO (auto-reload after edits +
        # pick up external changes). Created before _wire so writes are observed.
        self.apo_bridge = ApoBridge(self._config_path, self)
        self.apo_bridge.externalChange.connect(self._on_external_change)

        self._build_menu()
        self._wire()

        self.audio_panel.apply_settings(self._settings)
        self.mic_panel.apply_settings(self._settings)
        self.band_panel.apply_settings(self._settings)
        self.profile_panel.apply_settings(self._settings)
        self.test_songs_panel.apply_settings(self._settings)
        self._apply_initial_band_region()
        # Measurements and EQ curves are loaded by the profile panel's
        # apply_settings → _emit_all_curves → curvesChanged signal.
        # The EQ editor starts empty until the user picks an EQ file in Profiles;
        # the graphic-EQ panel is disabled until then (nothing to save into).
        self.graphic_eq_panel.setEnabled(False)
        self.graphic_eq_panel.set_audition_noise(self._settings.get("audition_noise", "pink"))
        self.graphic_eq_panel.set_audition_enabled(
            bool(self._settings.get("audition_enabled", False))
        )
        self._refresh_apo_watch()

        # The stream is opened lazily on the first play-button press so the
        # UI can boot without touching real audio hardware.

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")

        set_apo = QAction("Set Equalizer APO path\u2026", self)
        set_apo.triggered.connect(self._on_set_apo_path)
        menu.addAction(set_apo)

        menu.addSeparator()

        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.close)
        menu.addAction(quit_act)

    def _wire(self) -> None:
        self.spectrum_view.bandChanged.connect(self.band_panel.set_from_region)
        self.band_panel.bandValueChanged.connect(self.spectrum_view.set_band)
        self.band_panel.enable_check.toggled.connect(self.spectrum_view.set_band_visible)

        # Settings change triggers debounced save.
        self.audio_panel.freq_slider.valueChanged.connect(self._schedule_save)
        self.audio_panel.vol_slider.valueChanged.connect(self._schedule_save)
        self.audio_panel.sweep_duration.valueChanged.connect(self._schedule_save)
        self.audio_panel.focus_width.selectionChanged.connect(self._schedule_save)
        self.audio_panel.focus_speed.valueChanged.connect(self._schedule_save)
        self.audio_panel.channel_switch.selectionChanged.connect(self._schedule_save)
        self.audio_panel.mono_check.toggled.connect(self._schedule_save)
        self.audio_panel.loop_check.toggled.connect(self._schedule_save)
        self.audio_panel.dual_check.toggled.connect(self._schedule_save)
        self.audio_panel.device_a_combo.currentIndexChanged.connect(self._schedule_save)
        self.audio_panel.device_b_combo.currentIndexChanged.connect(self._schedule_save)
        self.audio_panel.routing_mirror.toggled.connect(self._schedule_save)
        self.band_panel.fc_spin.valueChanged.connect(self._schedule_save)
        self.band_panel.bw_spin.valueChanged.connect(self._schedule_save)
        self.band_panel.enable_check.toggled.connect(self._schedule_save)
        self.profile_panel.curvesChanged.connect(self._on_curves_changed)
        self.spectrum_view.frequencyClicked.connect(self._on_graph_freq_clicked)
        self.spectrum_view.focusSweepRequested.connect(self._on_focus_sweep_requested)
        self.mic_panel.monitor_check.toggled.connect(self._schedule_save)
        self.mic_panel.itemsChanged.connect(self._schedule_save)
        self.test_songs_panel.itemsChanged.connect(self._schedule_save)
        self.test_songs_button.clicked.connect(self._on_open_test_songs)

        # EQ mode panel
        self.eq_panel.eqResponseChanged.connect(self._on_eq_preview_changed)
        self.eq_panel.filtersChanged.connect(
            lambda _: self.spectrum_view.update_eq_dots(self.eq_panel.filters())
        )
        self.spectrum_view.eqFilterCreated.connect(self._on_eq_filter_created)
        self.spectrum_view.eqFilterMoved.connect(self._on_eq_filter_moved)
        self.spectrum_view.eqFilterQChanged.connect(self._on_eq_filter_q_changed)

        # Graphic EQ panel
        self.graphic_eq_panel.graphicEqChanged.connect(self._on_graphic_eq_changed)
        self.graphic_eq_panel.auditionIsolateRequested.connect(self._on_audition_isolate)
        self.graphic_eq_panel.auditionSweepRequested.connect(self._on_audition_sweep)
        self.graphic_eq_panel.auditionModeChanged.connect(self._on_audition_mode_changed)
        self.graphic_eq_panel.auditionModeChanged.connect(self._schedule_save)
        self.graphic_eq_panel.auditionNoiseChanged.connect(self._schedule_save)

        # Profiles panel: clicking an EQ file loads it into the editor.
        self.profile_panel.editEqFileRequested.connect(self._on_edit_eq_file)

        # Spacebar stops all audio playback.
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space_shortcut.activated.connect(self.audio_panel.stop_all)

    def _apply_initial_band_region(self) -> None:
        self.spectrum_view.set_band(
            self.band_panel.fc_spin.value(),
            self.band_panel.bw_spin.value(),
        )
        self.spectrum_view.set_band_visible(self.band_panel.enable_check.isChecked())

    # -------- test songs

    def _on_open_test_songs(self) -> None:
        """Open the Test Songs list in its dialog."""
        self.test_songs_dialog.show()
        self.test_songs_dialog.raise_()
        self.test_songs_dialog.activateWindow()

    # -------- EQ file editing

    def _on_edit_eq_file(self, path: str) -> None:
        """Load the selected EQ file into the editor and sync the graphic EQ."""
        self.eq_panel.set_eq_file(path)
        self.graphic_eq_panel.set_graphic_eqs(self.eq_panel.graphic_eqs())
        # Graphic-EQ edits only make sense once a file is bound to save into.
        self.graphic_eq_panel.setEnabled(bool(self.eq_panel.eq_file()))
        self._refresh_apo_watch()

    # -------- Equalizer APO live sync

    def _config_path(self) -> Optional[Path]:
        try:
            base = resolve_apo_config_dir(self._settings.get("apo_config_dir_override"))
        except ApoError:
            return None
        return base / MAIN_CONFIG_NAME

    def _refresh_apo_watch(self) -> None:
        """Watch config.txt and the currently-open EQ file for external edits."""
        cfg = self._config_path()
        paths = [str(cfg)] if cfg else []
        open_eq = self.eq_panel.eq_file()
        if open_eq:
            paths.append(open_eq)
        self.apo_bridge.set_watched(paths)

    def _on_external_change(self, path: str) -> None:
        """A watched file changed outside the app (e.g. edited in Equalizer APO).

        Reactions here MUST only read (never write a watched file), or they'd
        loop back through the poke; a re-entrancy guard + broad except keep a
        transient read/parse error on an external edit from crashing the loop.
        """
        if getattr(self, "_in_external_reload", False):
            return
        self._in_external_reload = True
        try:
            cfg = self._config_path()
            if cfg is not None and _key(path) == _key(cfg):
                # Presets/activation changed externally → reload browser + curves.
                self.profile_panel.apply_settings(self._settings)
                self._refresh_apo_watch()
                return
            open_eq = self.eq_panel.eq_file()
            if open_eq and _key(path) == _key(open_eq):
                if self.eq_panel.has_pending_save():
                    return  # in-app edit wins
                self.eq_panel.set_eq_file(open_eq)
                self.graphic_eq_panel.set_graphic_eqs(self.eq_panel.graphic_eqs())
        except Exception:
            pass
        finally:
            self._in_external_reload = False

    # -------- graphic EQ

    def _on_graphic_eq_changed(self, geqs: list) -> None:
        """Graphic EQ bands changed — persist into the selected EQ file. The
        in-app FIR stays off; Equalizer APO applies the EQ system-wide."""
        if self.eq_panel.eq_file() is None:
            return  # nothing to save into; ignore stray edits
        self.eq_panel.set_graphic_eqs(geqs)
        self.engine.set_graphic_eq(None)

    # -------- graphic-EQ region audition

    def _on_audition_isolate(self, lo: float, hi: float) -> None:
        """Clicked a band point: isolate its region; auto-play noise if idle."""
        from eq_helper.audio.shared_state import Mode
        self.band_panel.isolate(fc=(lo + hi) / 2.0, bw=hi - lo)
        if self.engine.state.snapshot().mode == Mode.IDLE:
            self.audio_panel.start_audition_noise(self.graphic_eq_panel.audition_noise())

    def _on_audition_sweep(self, lo: float, hi: float) -> None:
        """Shift+clicked a band point: focus-sweep its region."""
        import math
        center = math.sqrt(lo * hi)
        width_octaves = math.log2(hi / lo)
        self.audio_panel.trigger_focus_sweep_band(center, width_octaves)

    def _on_audition_mode_changed(self, on: bool) -> None:
        """Turning audition mode off restores normal listening (isolation off)."""
        if not on:
            self.band_panel.enable_check.setChecked(False)

    # -------- EQ preview response

    def _on_eq_preview_changed(self, freqs, db) -> None:
        """Update the EQ preview curve on the spectrum view."""
        if freqs is not None and db is not None:
            self.spectrum_view.set_eq_preview(freqs, db)
        else:
            self.spectrum_view.clear_eq_preview()

    # -------- EQ mode graph interaction

    def _on_eq_filter_created(self, hz: float, gain_db: float) -> None:
        from eq_helper.dsp.eq_chain import EQFilter
        self.eq_panel.add_filter(EQFilter(fc_hz=hz, gain_db=gain_db))

    def _on_eq_filter_moved(self, index: int, hz: float, gain_db: float) -> None:
        from eq_helper.dsp.eq_chain import EQFilter
        filters = self.eq_panel.filters()
        if 0 <= index < len(filters):
            f = filters[index]
            f.fc_hz = hz
            f.gain_db = gain_db
            self.eq_panel.update_filter(index, f)

    def _on_eq_filter_q_changed(self, index: int, new_q: float) -> None:
        filters = self.eq_panel.filters()
        if 0 <= index < len(filters):
            f = filters[index]
            f.q = new_q
            self.eq_panel.update_filter(index, f)

    # -------- graph click → sine freq / EQ filter

    def _on_graph_freq_clicked(self, hz: float) -> None:
        """A plain click on the spectrum auditions a sine wave at this freq.
        (Ctrl+click is handled inside the spectrum view as an EQ filter op.)"""
        import math
        self.engine.set_freq(hz)
        self.audio_panel.freq_slider.set_value(math.log10(hz))
        from eq_helper.audio.shared_state import Mode
        if self.engine.state.snapshot().mode != Mode.SINE:
            self.audio_panel._on_play_sine()
            self.audio_panel._set_active(self.audio_panel.btn_sine)

    def _on_focus_sweep_requested(self, hz: float) -> None:
        """Shift+click on the spectrum: centre on this frequency and start a
        focused sweep around it."""
        import math
        hz = max(20.0, min(20000.0, hz))
        self.engine.set_freq(hz)
        self.audio_panel.freq_slider.set_value(math.log10(hz))
        self.audio_panel.trigger_focus_sweep()

    # -------- curves (measurement + EQ from profile panel)

    def _on_curves_changed(self, slot, m_freqs, m_db, eq_freqs, eq_db) -> None:
        """Route measurement/EQ data from the profile panel to the correct
        curve set in the spectrum view. Slot 0 = first device, slot 1 = second."""
        self._schedule_save()
        if slot == 0:
            if m_freqs is not None and m_db is not None:
                self.spectrum_view.set_overlay(m_freqs, m_db)
            else:
                self.spectrum_view.clear_overlay()
            if eq_freqs is not None and eq_db is not None:
                self.spectrum_view.set_eq_response(eq_freqs, eq_db)
            else:
                self.spectrum_view.clear_eq_response()
        elif slot == 1:
            if m_freqs is not None and m_db is not None:
                self.spectrum_view.set_overlay2(m_freqs, m_db)
            else:
                self.spectrum_view.clear_overlay2()
            if eq_freqs is not None and eq_db is not None:
                self.spectrum_view.set_eq2_response(eq_freqs, eq_db)
            else:
                self.spectrum_view.clear_eq2_response()

    # -------- APO path

    def _on_set_apo_path(self) -> None:
        current = self._settings.get("apo_config_dir_override") or ""
        path, ok = QInputDialog.getText(
            self,
            "Equalizer APO path",
            "Path to Equalizer APO 'config' directory:",
            text=current,
        )
        if not ok:
            return
        path = path.strip()
        self._settings["apo_config_dir_override"] = path or None
        self._schedule_save()
        # Re-resolve presets/devices against the new path (else the panel stays
        # empty until restart — exactly the first-run case that motivates this).
        self.profile_panel.apply_settings(self._settings)
        self._refresh_apo_watch()

    # -------- persistence

    def _schedule_save(self, *_args: object) -> None:
        self._save_timer.start()

    def _save_now(self) -> None:
        self.audio_panel.collect_settings(self._settings)
        self.mic_panel.collect_settings(self._settings)
        self.band_panel.collect_settings(self._settings)
        self.profile_panel.collect_settings(self._settings)
        self.test_songs_panel.collect_settings(self._settings)
        self._settings["mic_section_expanded"] = self.mic_section.is_expanded()
        self._settings["band_section_expanded"] = self.band_section.is_expanded()
        self._settings["audition_enabled"] = self.graphic_eq_panel.audition_enabled()
        self._settings["audition_noise"] = self.graphic_eq_panel.audition_noise()
        try:
            save_settings(self._settings, self._settings_path)
        except OSError:
            # Non-fatal: a later save will retry.
            pass

    def closeEvent(self, event) -> None:  # noqa: D401
        self._save_timer.stop()
        self._save_now()
        try:
            self.apo_bridge.cleanup()
            self.eq_panel.cleanup()
            self.engine.stop()
            self.engine.stop_input_stream()
            self.engine.stop_stream()
        finally:
            super().closeEvent(event)
