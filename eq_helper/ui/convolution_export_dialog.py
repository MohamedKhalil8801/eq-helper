"""Options panel for saving the resonance EQ as convolution impulse responses.

Every convolver on every OS applies the same file format, so this panel is the
portability boundary: rates, channel layout, sample format and phase are all
chosen here, and the defaults produce the pair of files that is safe to hand to
someone who does not own Equalizer APO.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eq_helper.dsp.convolution import (
    FORMATS,
    PHASES,
    PEAK_TARGET_DB,
    SAMPLE_RATES,
    latency_samples,
    render_set,
    taps_for,
)

_PHASE_LABELS = {
    "minimum": "Minimum phase (recommended)",
    "linear": "Linear phase",
}
_FORMAT_LABELS = {
    "pcm24": "24-bit PCM WAV (recommended)",
    "f32": "32-bit float WAV",
    "pcm16": "16-bit PCM WAV",
}
_LAYOUT_LABELS = {
    "stereo": "Stereo — L/R in one file",
    "mono_pair": "Two mono files — one per ear",
    "mono": "Mono — one file for both channels",
}
_DEFAULT_RATES = (44100, 48000)


class ConvolutionExportDialog(QDialog):
    """Collect export options, then render and write the impulse responses."""

    def __init__(self, eq, settings: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save as convolution file")
        self.resize(620, 430)
        self.eq = eq
        self.saved_paths: list[Path] = []
        self._settings = settings

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Writes impulse response files that any convolution engine can load "
            "(Equalizer APO's Convolution filter, CamillaDSP, Roon, foobar2000, "
            "phone players). A single stereo file carries both ears."
        )
        intro.setWordWrap(True)
        intro.setMaximumWidth(580)  # keep the panel compact instead of one long line
        layout.addWidget(intro)

        form = QGridLayout()
        form.addWidget(QLabel("Channel layout"), 0, 0)
        self.layout_combo = QComboBox()
        form.addWidget(self.layout_combo, 0, 1)

        form.addWidget(QLabel("Phase"), 1, 0)
        self.phase_combo = QComboBox()
        self.phase_combo.setToolTip(
            "Minimum phase needs no look-ahead and puts all ringing after the transient. "
            "Linear phase keeps the phase flat but delays everything by half the file length."
        )
        for phase in PHASES:
            self.phase_combo.addItem(_PHASE_LABELS[phase], phase)
        form.addWidget(self.phase_combo, 1, 1)

        form.addWidget(QLabel("Sample format"), 2, 0)
        self.format_combo = QComboBox()
        self.format_combo.setToolTip(
            "24-bit PCM is accepted by the widest range of hardware and phone players."
        )
        for fmt in FORMATS:
            self.format_combo.addItem(_FORMAT_LABELS[fmt], fmt)
        form.addWidget(self.format_combo, 2, 1)

        form.addWidget(QLabel("Output folder"), 3, 0)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        form.addLayout(folder_row, 3, 1)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        layout.addWidget(QLabel("Sample rates (one file set per rate):"))
        rates_grid = QGridLayout()
        self.rate_checks: dict[int, QCheckBox] = {}
        for index, rate in enumerate(SAMPLE_RATES):
            check = QCheckBox(f"{rate / 1000:g} kHz")
            check.toggled.connect(self._on_changed)
            self.rate_checks[rate] = check
            rates_grid.addWidget(check, index // 3, index % 3)
        layout.addLayout(rates_grid)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Save).setText("Save impulse responses")
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._populate_layouts()
        self._apply_settings()
        self._on_changed()

    # ------------------------------------------------------------- population

    def _populate_layouts(self) -> None:
        """Only offer layouts that can hold the channels this EQ actually has."""
        choices = ("mono", "stereo") if len(self.eq.channels) == 1 else ("stereo", "mono_pair")
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        for key in choices:
            self.layout_combo.addItem(_LAYOUT_LABELS[key], key)
        self.layout_combo.blockSignals(False)
        self.layout_combo.currentIndexChanged.connect(self._on_changed)

    def _apply_settings(self) -> None:
        """Restore the last used options, falling back to the safe defaults."""
        saved_layout = self._settings.get("resonance_export_layout")
        index = self.layout_combo.findData(saved_layout)
        self.layout_combo.setCurrentIndex(max(0, index))
        index = self.phase_combo.findData(self._settings.get("resonance_export_phase"))
        self.phase_combo.setCurrentIndex(max(0, index))
        index = self.format_combo.findData(self._settings.get("resonance_export_format"))
        self.format_combo.setCurrentIndex(max(0, index))
        self.phase_combo.currentIndexChanged.connect(self._on_changed)
        self.format_combo.currentIndexChanged.connect(self._on_changed)

        saved_rates = self._settings.get("resonance_export_sample_rates")
        if not isinstance(saved_rates, (list, tuple)) or not saved_rates:
            saved_rates = _DEFAULT_RATES
        valid = {int(rate) for rate in saved_rates if self._is_rate(rate)}
        if not valid:
            valid = set(_DEFAULT_RATES)
        for rate, check in self.rate_checks.items():
            check.setChecked(rate in valid)

        saved_dir = self._settings.get("resonance_export_dir")
        folder = Path(saved_dir) if saved_dir else Path.home()
        self.folder_edit.setText(str(folder))

    @staticmethod
    def _is_rate(value) -> bool:
        try:
            return int(value) in SAMPLE_RATES
        except (TypeError, ValueError):
            return False

    # -------------------------------------------------------------- accessors

    def channel_layout(self) -> str:
        return self.layout_combo.currentData()

    def phase(self) -> str:
        return self.phase_combo.currentData()

    def sample_format(self) -> str:
        return self.format_combo.currentData()

    def sample_rates(self) -> list[int]:
        return [rate for rate, check in self.rate_checks.items() if check.isChecked()]

    def options(self) -> dict:
        return {
            "resonance_export_layout": self.channel_layout(),
            "resonance_export_phase": self.phase(),
            "resonance_export_format": self.sample_format(),
            "resonance_export_sample_rates": self.sample_rates(),
            "resonance_export_dir": self.folder_edit.text().strip(),
        }

    def _files_per_rate(self) -> int:
        return len(self.eq.channels) if self.channel_layout() == "mono_pair" else 1

    def _base_name(self) -> str:
        channels = list(self.eq.channels.values())
        if len(channels) == 1:
            text = f"Resonance {channels[0].peak_hz:.0f}Hz"
        else:
            text = "Resonance " + " ".join(
                f"{side}{channel.peak_hz:.0f}" for side, channel in self.eq.channels.items()
            )
        cleaned = "".join("_" if char in '<>:"/\\|?*' else char for char in text)
        return cleaned.strip() or "Resonance"

    # ---------------------------------------------------------------- actions

    def _on_changed(self, *_args) -> None:
        rates = self.sample_rates()
        save = self._buttons.button(QDialogButtonBox.Save)
        save.setEnabled(bool(rates))
        if not rates:
            self.summary.setText("Select at least one sample rate.")
            return
        low, high = min(rates), max(rates)
        taps = (f"{taps_for(low)} taps" if low == high
                else f"{taps_for(low)}–{taps_for(high)} taps")
        if self.phase() == "minimum":
            delay = "no look-ahead"
        else:
            delay = (f"{latency_samples('linear', taps_for(high)) / high * 1000.0:.0f} ms "
                     "of delay to compensate")
        files = len(rates) * self._files_per_rate()
        gain = "one gain" if len(self.eq.channels) == 1 else "both ears share one gain"
        self.summary.setText(
            f"{taps} · {delay} · {files} file{'s' if files != 1 else ''} + README · "
            f"{gain}, peak {PEAK_TARGET_DB:g} dB"
        )

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output folder", self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    def _save(self) -> None:
        rates = self.sample_rates()
        if not rates:
            QMessageBox.warning(self, "No sample rate", "Select at least one output sample rate.")
            return
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "Folder not found",
                                f"Choose an existing output folder.\n\n{folder}")
            return
        curves = {
            side: (channel.freqs, channel.correction_db)
            for side, channel in self.eq.channels.items()
        }
        try:
            self.saved_paths = render_set(
                curves, rates, self.channel_layout(), self.phase(), self.sample_format(),
                folder, self._base_name(),
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not save impulse responses", str(exc))
            return
        self.accept()
