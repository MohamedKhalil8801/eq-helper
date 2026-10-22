"""EQ editor: an interactive parametric-EQ filter list bound to ONE APO EQ file.

Selecting an EQ file (from the Profiles panel) loads its filters + preamp +
graphic EQs; edits are written straight back to that file via a debounced,
atomic, backed write, so Equalizer APO live-reloads and the change is audible.
When no file is selected the editor is empty/disabled.

The app's in-app sosfilt EQ chain is intentionally NOT used: all EQ lives in APO
files and APO processes the system audio (including the app's own tones), so
applying it again in-app would double it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import List, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from eq_helper.apo.errors import ApoWriteError
from eq_helper.apo.writer import atomic_write_with_backup
from eq_helper.audio.engine import AudioEngine
from eq_helper.dsp.eq_chain import (
    EQFilter,
    compute_chain_response,
    format_apo_number,
    match_filter_line,
    parse_apo_filters_with_lines,
    parse_preamp,
)
from eq_helper.dsp.graphic_eq import (
    GraphicEQ,
    graphic_eq_to_apo_text,
    parse_graphic_eq_line,
)

_EQ_FREQS = np.geomspace(20.0, 20000.0, 500)
_FILTER_TYPES = [
    "PK", "LS", "HS", "LSC", "HSC", "LP", "HP", "LPQ", "HPQ", "NO", "BP",
]


class _PrecisionSpinBox(QDoubleSpinBox):
    """A DoubleSpinBox that uses a finer step when the CTRL key is held."""

    def __init__(self, *args, ctrl_step_ratio: float = 0.2, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ctrl_step_ratio = ctrl_step_ratio

    def stepBy(self, steps: int) -> None:
        mods = QGuiApplication.keyboardModifiers()
        if mods & Qt.ControlModifier:
            old_step = self.singleStep()
            try:
                self.setSingleStep(old_step * self._ctrl_step_ratio)
                super().stepBy(steps)
            finally:
                self.setSingleStep(old_step)
        else:
            super().stepBy(steps)


class _FilterRow(QWidget):
    """One row in the filter list: type, freq, gain, Q, enable, remove."""

    changed = Signal(str)
    removeClicked = Signal()

    def __init__(self, filt: EQFilter, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.source_index: Optional[int] = None
        self.original: Optional[EQFilter] = None
        self.changed_fields: set[str] = set()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.type_combo = QComboBox()
        self.type_combo.addItems(_FILTER_TYPES)
        if self.type_combo.findText(filt.filter_type) < 0:
            # Preserve types this build does not know about instead of
            # snapping them to the first entry (which would rewrite the file).
            self.type_combo.addItem(filt.filter_type)
        self.type_combo.setCurrentText(filt.filter_type)
        self.type_combo.setFixedWidth(65)
        layout.addWidget(self.type_combo)

        self.fc_spin = _PrecisionSpinBox()
        self.fc_spin.setRange(1.0, 100000.0)
        self.fc_spin.setDecimals(2)
        self.fc_spin.setValue(filt.fc_hz)
        self.fc_spin.setSuffix(" Hz")
        self.fc_spin.setSingleStep(10.0)
        layout.addWidget(self.fc_spin)

        self.gain_spin = _PrecisionSpinBox()
        self.gain_spin.setRange(-100.0, 100.0)
        self.gain_spin.setDecimals(4)
        self.gain_spin.setValue(filt.gain_db)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setSingleStep(0.5)
        layout.addWidget(self.gain_spin)

        self.q_spin = _PrecisionSpinBox()
        self.q_spin.setRange(0.01, 1000.0)
        self.q_spin.setDecimals(4)
        self.q_spin.setValue(filt.q)
        self.q_spin.setPrefix("Q ")
        self.q_spin.setSingleStep(0.1)
        layout.addWidget(self.q_spin)

        self.enable_check = QCheckBox()
        self.enable_check.setChecked(filt.enabled)
        self.enable_check.setToolTip("Enable/disable this filter")
        layout.addWidget(self.enable_check)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedWidth(24)
        self.remove_btn.setToolTip("Remove filter")
        layout.addWidget(self.remove_btn)

        self.type_combo.currentTextChanged.connect(lambda _: self.changed.emit("type"))
        self.fc_spin.valueChanged.connect(lambda _: self.changed.emit("fc"))
        self.gain_spin.valueChanged.connect(lambda _: self.changed.emit("gain"))
        self.q_spin.valueChanged.connect(lambda _: self.changed.emit("q"))
        self.enable_check.toggled.connect(lambda _: self.changed.emit("enabled"))
        self.remove_btn.clicked.connect(self.removeClicked.emit)

    def to_filter(self) -> EQFilter:
        return EQFilter(
            filter_type=self.type_combo.currentText(),
            fc_hz=self.fc_spin.value(),
            gain_db=self.gain_spin.value(),
            q=self.q_spin.value(),
            enabled=self.enable_check.isChecked(),
        )

    def update_from_filter(self, f: EQFilter) -> None:
        """Update widgets without emitting changed signals."""
        self.blockSignals(True)
        self.type_combo.setCurrentText(f.filter_type)
        self.fc_spin.setValue(f.fc_hz)
        self.gain_spin.setValue(f.gain_db)
        self.q_spin.setValue(f.q)
        self.enable_check.setChecked(f.enabled)
        self.blockSignals(False)


class EQModePanel(QGroupBox):
    """Parametric EQ filter list bound to a single APO EQ file."""

    # (eq_response_freqs, eq_response_db) for graph display
    eqResponseChanged = Signal(object, object)
    # Emitted when the filter list changes (for graph dot sync)
    filtersChanged = Signal(list)

    def __init__(self, engine: AudioEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__("EQ Filters", parent)
        self._engine = engine
        self._eq_file_path: Optional[str] = None
        self._preamp_db: float = 0.0
        self._rows: List[_FilterRow] = []
        self._graphic_eqs: List[GraphicEQ] = []
        self._graphic_snapshot: List[GraphicEQ] = []
        self._source_lines: List[str] = []
        self._encoding = "utf-8"
        self._newline = "\n"
        self._final_newline = True
        self._filter_line_indices: List[int] = []
        self._graphic_line_indices: List[int] = []

        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("+ Add")
        self.btn_clear = QPushButton("Clear")
        self.btn_add.clicked.connect(self._on_add)
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._hint = QLabel("Select an EQ file in Profiles to edit it.")
        self._hint.setStyleSheet("color: #999; font-style: italic;")
        layout.addWidget(self._hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._filter_container = QWidget()
        self._filter_layout = QVBoxLayout(self._filter_container)
        self._filter_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_layout.addStretch()
        self._scroll.setWidget(self._filter_container)
        layout.addWidget(self._scroll, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._auto_save)

        self._set_editing_enabled(False)

    # ------------------------------------------------------------- file

    def set_eq_file(self, path: Optional[str]) -> None:
        """Bind the editor to an EQ file (or clear it when ``path`` is None).

        Flushes any pending save for the previously-bound file first. Loading
        never writes: the original text is kept verbatim and only the lines the
        user actually edits are rewritten on save.
        """
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._auto_save()

        if not path:
            self._eq_file_path = None
            self._preamp_db = 0.0
            self._graphic_eqs = []
            self._graphic_snapshot = []
            self._source_lines = []
            self._filter_line_indices = []
            self._graphic_line_indices = []
            self._rebuild_rows([])
            self._set_editing_enabled(False)
            self._emit_response()
            self.filtersChanged.emit([])
            return

        self._eq_file_path = str(path)
        text = self._read_document(Path(path))
        filters: List[EQFilter] = []
        geqs: List[GraphicEQ] = []
        self._filter_line_indices = []
        self._graphic_line_indices = []
        if text is not None:
            parsed = parse_apo_filters_with_lines(text)
            filters = [f for _idx, f in parsed]
            self._filter_line_indices = [idx for idx, _f in parsed]
            for i, line in enumerate(self._source_lines):
                geq = parse_graphic_eq_line(line, name=f"GEQ {len(geqs) + 1}")
                if geq is not None:
                    geqs.append(geq)
                    self._graphic_line_indices.append(i)
        self._preamp_db = parse_preamp(text or "")
        self._graphic_eqs = geqs
        self._graphic_snapshot = copy.deepcopy(geqs)
        self._rebuild_rows(filters, self._filter_line_indices)
        self._set_editing_enabled(True)
        self._emit_response()
        self.filtersChanged.emit(self.filters())

    def eq_file(self) -> Optional[str]:
        return self._eq_file_path

    def has_pending_save(self) -> bool:
        """True if a debounced save is queued (the user just edited)."""
        return self._save_timer.isActive()

    # ------------------------------------------------------------ graphic EQ

    def graphic_eqs(self) -> List[GraphicEQ]:
        return list(self._graphic_eqs)

    def set_graphic_eqs(self, geqs: List[GraphicEQ]) -> None:
        """Update graphic EQs (from the graphic-EQ panel) and persist."""
        if self._eq_file_path is None:
            return
        self._graphic_eqs = list(geqs)
        self._on_any_changed()

    # ------------------------------------------------------------- filters

    def filters(self) -> List[EQFilter]:
        return [row.to_filter() for row in self._rows]

    def add_filter(self, f: EQFilter) -> None:
        """Add a filter programmatically (e.g. from a Ctrl+click on the graph)."""
        if self._eq_file_path is None:
            return
        self._add_row(f)
        self._on_any_changed()

    def update_filter(self, index: int, f: EQFilter) -> None:
        if self._eq_file_path is not None and 0 <= index < len(self._rows):
            row = self._rows[index]
            row.update_from_filter(f)
            row.changed_fields.update({"type", "fc", "gain", "q", "enabled"})
            self._on_any_changed()

    # ----------------------------------------------------------- internal

    def _set_editing_enabled(self, enabled: bool) -> None:
        self.btn_add.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)
        self._scroll.setEnabled(enabled)
        self._hint.setVisible(not enabled)

    def _rebuild_rows(
        self,
        filters: List[EQFilter],
        source_indices: Optional[List[int]] = None,
    ) -> None:
        for row in self._rows:
            self._filter_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        for i, f in enumerate(filters):
            src = source_indices[i] if source_indices is not None else None
            self._add_row(f, source_index=src)

    def _add_row(self, f: EQFilter, source_index: Optional[int] = None) -> None:
        row = _FilterRow(f)
        row.source_index = source_index
        row.original = copy.deepcopy(f)
        row.changed.connect(lambda field, r=row: self._on_row_changed(r, field))
        row.removeClicked.connect(lambda r=row: self._on_remove(r))
        self._filter_layout.insertWidget(self._filter_layout.count() - 1, row)
        self._rows.append(row)

    def _on_row_changed(self, row: _FilterRow, field: str) -> None:
        if row in self._rows:
            row.changed_fields.add(field)
            self._on_any_changed()

    def _on_remove(self, row: _FilterRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self._filter_layout.removeWidget(row)
            row.deleteLater()
            self._on_any_changed()

    def _on_any_changed(self) -> None:
        self._emit_response()
        self._save_timer.start()
        self.filtersChanged.emit(self.filters())

    def _emit_response(self) -> None:
        filters = self.filters()
        if not filters:
            self.eqResponseChanged.emit(None, None)
            return
        db = compute_chain_response(filters, _EQ_FREQS, self._engine.sample_rate)
        self.eqResponseChanged.emit(_EQ_FREQS, db)

    # ----------------------------------------------------- lossless document

    def _read_document(self, path: Path) -> Optional[str]:
        """Read the file and remember its encoding/newlines for a faithful save.

        Returns the decoded text, or ``None`` when the file cannot be read.
        """
        try:
            raw = path.read_bytes()
        except OSError:
            self._source_lines = []
            return None
        text = ""
        for encoding in ("utf-8", "cp1252"):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            self._encoding = encoding
            break
        else:
            self._encoding = "utf-8"
            text = raw.decode("utf-8", errors="replace")

        self._newline = "\r\n" if "\r\n" in text else "\n"
        self._final_newline = text.endswith(("\n", "\r"))
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self._source_lines = lines
        return text

    def _source_text(self) -> str:
        body = self._newline.join(self._source_lines)
        return body + self._newline if self._final_newline else body

    def _has_edits(self) -> bool:
        if any(row.changed_fields for row in self._rows):
            return True
        if any(row.source_index is None for row in self._rows):
            return True
        current = {row.source_index for row in self._rows}
        if any(idx not in current for idx in self._filter_line_indices):
            return True
        return self._graphic_eqs != self._graphic_snapshot

    def _render_document(self) -> str:
        """Rebuild the file, touching only lines backed by actual edits."""
        lines: List[Optional[str]] = list(self._source_lines)

        current = {row.source_index for row in self._rows if row.source_index is not None}
        for idx in self._filter_line_indices:
            if idx not in current:
                lines[idx] = None

        for row in self._rows:
            if row.source_index is None or not row.changed_fields:
                continue
            if 0 <= row.source_index < len(self._source_lines):
                lines[row.source_index] = self._render_filter_line(
                    row, self._source_lines[row.source_index]
                )

        for pos, idx in enumerate(self._graphic_line_indices):
            if pos >= len(self._graphic_eqs):
                lines[idx] = None
            elif self._graphic_eqs[pos] != self._graphic_snapshot[pos]:
                lines[idx] = graphic_eq_to_apo_text(self._graphic_eqs[pos])

        used_numbers: List[int] = []
        for row in self._rows:
            if row.source_index is not None and 0 <= row.source_index < len(self._source_lines):
                m = match_filter_line(self._source_lines[row.source_index])
                if m and m.group("index"):
                    used_numbers.append(int(m.group("index")))
        next_number = max(used_numbers, default=0) + 1
        new_filter_lines: List[str] = []
        for row in self._rows:
            if row.source_index is None:
                new_filter_lines.append(
                    self._render_new_filter_line(row.to_filter(), next_number)
                )
                next_number += 1

        insert_at = max(
            (idx for idx in self._filter_line_indices if lines[idx] is not None),
            default=len(lines) - 1,
        ) + 1
        out: List[str] = []
        for i, line in enumerate(lines):
            if i == insert_at:
                out.extend(new_filter_lines)
                new_filter_lines = []
            if line is not None:
                out.append(line)
        out.extend(new_filter_lines)
        out.extend(
            graphic_eq_to_apo_text(geq)
            for geq in self._graphic_eqs[len(self._graphic_line_indices):]
            if geq.bands
        )
        body = self._newline.join(out)
        return body + self._newline if self._final_newline else body

    def _render_filter_line(self, row: _FilterRow, line: str) -> str:
        """Rewrite one filter line, keeping every untouched token verbatim."""
        m = match_filter_line(line)
        if m is None:
            return line
        f = row.to_filter()
        changed = row.changed_fields
        head = line[: m.start("on")]
        suffix = line[m.end():]

        on = m.group("on") if "enabled" not in changed else ("ON" if f.enabled else "OFF")
        ftype = m.group("type") if "type" not in changed else f.filter_type
        fc = m.group("fc") if "fc" not in changed else format_apo_number(f.fc_hz)
        gain_txt = (
            m.group("gain") if "gain" not in changed else format_apo_number(f.gain_db)
        )
        q_txt = m.group("q") if "q" not in changed else format_apo_number(f.q)

        needs_gain = ftype.upper() not in ("LP", "HP", "LPQ", "HPQ")
        include_gain = needs_gain and (
            m.group("gain") is not None or "type" in changed or "gain" in changed
        )
        include_q = (
            m.group("q") is not None or "type" in changed or "q" in changed
        )

        parts = [head, on, " ", ftype, " Fc ", fc, " Hz"]
        if include_gain:
            parts.extend([" Gain ", gain_txt, " dB"])
        if include_q:
            parts.extend([" Q ", q_txt])
        parts.append(suffix)
        return "".join(parts)

    def _render_new_filter_line(self, f: EQFilter, number: int) -> str:
        on = "ON" if f.enabled else "OFF"
        line = (
            f"Filter {number}: {on} {f.filter_type} "
            f"Fc {format_apo_number(f.fc_hz)} Hz"
        )
        if f.filter_type.upper() not in ("LP", "HP", "LPQ", "HPQ"):
            line += f" Gain {format_apo_number(f.gain_db)} dB"
        return line + f" Q {format_apo_number(f.q)}"

    def _adopt_document(self, text: str) -> None:
        """Make freshly-written text the new baseline without rebuilding rows."""
        self._source_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if self._source_lines and self._source_lines[-1] == "":
            self._source_lines.pop()
        parsed = parse_apo_filters_with_lines(text)
        if len(parsed) != len(self._rows):
            # Unexpected layout: re-read from disk rather than risk a bad map.
            if self._eq_file_path:
                self.set_eq_file(self._eq_file_path)
            return
        self._filter_line_indices = [idx for idx, _f in parsed]
        for row, (idx, f) in zip(self._rows, parsed):
            row.source_index = idx
            row.original = copy.deepcopy(f)
            row.changed_fields.clear()

        geqs: List[GraphicEQ] = []
        self._graphic_line_indices = []
        for i, line in enumerate(self._source_lines):
            geq = parse_graphic_eq_line(line, name=f"GEQ {len(geqs) + 1}")
            if geq is not None:
                geqs.append(geq)
                self._graphic_line_indices.append(i)
        self._graphic_snapshot = copy.deepcopy(self._graphic_eqs)

    def _auto_save(self) -> None:
        if not self._eq_file_path:
            return
        if not self._has_edits():
            return  # opening/reading a file must never rewrite it
        text = self._render_document()
        if text == self._source_text():
            return
        try:
            atomic_write_with_backup(
                Path(self._eq_file_path), text, encoding=self._encoding
            )
        except ApoWriteError:
            # Non-fatal: a later edit/save will retry.
            return
        self._adopt_document(text)

    # ------------------------------------------------------------- actions

    def _on_add(self) -> None:
        self.add_filter(EQFilter())

    def _on_clear(self) -> None:
        self._rebuild_rows([])
        self._on_any_changed()

    def cleanup(self) -> None:
        self._save_timer.stop()
        self._auto_save()
