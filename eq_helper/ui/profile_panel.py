"""Profiles panel: device → switchable presets → stacked EQ files.

Per device, the top list is a one-click preset switcher (the active preset is
marked ●); the bottom list is the active preset's ordered stack of EQ files,
each with an enable checkbox, reorder, edit (loads it into the EQ editor), and
remove. Per-device measurement CSVs and the graph curve-sets are preserved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eq_helper.apo.errors import ApoError
from eq_helper.apo.paths import resolve_apo_config_dir
from eq_helper.apo.presets import EqFile, Preset, PresetLibrary
from eq_helper.dsp.eq_response import load_profile_eq_response
from eq_helper.dsp.overlay import OverlayLoadError, load_measurement_csv, normalize_at_1khz

_EQ_FREQS = np.geomspace(20.0, 20000.0, 500)
_APO_FILTER = "EQ / APO text (*.txt);;All files (*.*)"
# Batch balance-EQ candidate presets end with an alignment score like "(0.95)".
_CANDIDATE_RE = re.compile(r"\(\d\.\d\d\)")


class ProfilePanel(QGroupBox):
    """Device-aware preset switcher with a stacked EQ-file editor surface."""

    # (slot, overlay_freqs|None, overlay_db|None, eq_freqs|None, eq_db|None)
    curvesChanged = Signal(int, object, object, object, object)
    # Absolute path of an EQ file the user wants to edit.
    editEqFileRequested = Signal(str)

    def __init__(
        self,
        *,
        get_override: Callable[[], Optional[str]],
        get_isolation_band: Callable[[], Optional[Tuple[float, float]]] = lambda: None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__("Profiles", parent)
        self._get_override = get_override
        self._get_isolation_band = get_isolation_band
        self._sr = 48000
        self._measurements: Dict[str, Optional[str]] = {}
        self._loading = False  # guards itemChanged during programmatic refresh

        layout = QVBoxLayout(self)

        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("Device:"))
        self.device_combo = QComboBox()
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        dev_row.addWidget(self.device_combo, 1)
        layout.addLayout(dev_row)

        self._meas_label = QLabel("No measurement loaded")
        self._meas_label.setStyleSheet("color: #999; font-style: italic;")
        layout.addWidget(self._meas_label)

        # --- Presets (one-click switcher) ---
        layout.addWidget(QLabel("Presets (click to activate):"))
        self._presets = QListWidget()
        self._presets.itemClicked.connect(self._on_preset_clicked)
        layout.addWidget(self._presets, 1)
        pbtns = QHBoxLayout()
        self.btn_new_preset = QPushButton("New…")
        self.btn_add_preset = QPushButton("Add file…")
        self.btn_rename_preset = QPushButton("Rename…")
        self.btn_remove_preset = QPushButton("Remove")
        self.btn_preset_up = QPushButton("↑")
        self.btn_preset_down = QPushButton("↓")
        self.btn_preset_up.setToolTip("Move preset up (rank candidates)")
        self.btn_preset_down.setToolTip("Move preset down (rank candidates)")
        self.btn_new_preset.clicked.connect(self._on_new_preset)
        self.btn_add_preset.clicked.connect(self._on_add_preset_from_file)
        self.btn_rename_preset.clicked.connect(self._on_rename_preset)
        self.btn_remove_preset.clicked.connect(self._on_remove_preset)
        self.btn_preset_up.clicked.connect(lambda: self._move_preset(-1))
        self.btn_preset_down.clicked.connect(lambda: self._move_preset(1))
        for b in (self.btn_new_preset, self.btn_add_preset, self.btn_rename_preset,
                  self.btn_remove_preset, self.btn_preset_up, self.btn_preset_down):
            pbtns.addWidget(b)
        layout.addLayout(pbtns)

        self.btn_optimize = QPushButton("Optimize → single EQ")
        self.btn_optimize.setToolTip(
            "Combine the active preset's whole stack into one equivalent "
            "GraphicEQ in a new '(optimized)' preset — same sound, lighter."
        )
        self.btn_optimize.clicked.connect(self._on_optimize)
        layout.addWidget(self.btn_optimize)

        self.btn_balance = QPushButton("Balance EQ…")
        self.btn_balance.setToolTip(
            "Import Left and Right measurements and generate an EQ that makes "
            "one side identical to the other (chases every peak/dip exactly)."
        )
        self.btn_balance.clicked.connect(self._on_balance)
        layout.addWidget(self.btn_balance)

        bbtns = QHBoxLayout()
        self.btn_balance_batch = QPushButton("Balance EQ batch…")
        self.btn_balance_batch.setToolTip(
            "Generate every reference×other balance EQ from a folder of "
            "Left/Right measurements, each as its own preset to audition."
        )
        self.btn_remove_others = QPushButton("Remove other candidates")
        self.btn_remove_others.setToolTip(
            "Remove all balance-EQ candidate presets except the selected one."
        )
        self.btn_balance_batch.clicked.connect(self._on_balance_batch)
        self.btn_remove_others.clicked.connect(self._on_remove_other_candidates)
        bbtns.addWidget(self.btn_balance_batch)
        bbtns.addWidget(self.btn_remove_others)
        layout.addLayout(bbtns)

        # --- Stack (active preset's EQ files) ---
        layout.addWidget(QLabel("EQ files in this preset (double-click to edit):"))
        self._stack = QListWidget()
        self._stack.itemDoubleClicked.connect(self._on_stack_double_clicked)
        self._stack.itemChanged.connect(self._on_stack_item_changed)
        layout.addWidget(self._stack, 1)
        sbtns = QHBoxLayout()
        self.btn_new_eq = QPushButton("New EQ…")
        self.btn_add_eq = QPushButton("Add EQ…")
        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_remove_eq = QPushButton("Remove")
        self.btn_new_eq.clicked.connect(self._on_new_eq)
        self.btn_add_eq.clicked.connect(self._on_add_eq_from_file)
        self.btn_up.clicked.connect(lambda: self._move_eq(-1))
        self.btn_down.clicked.connect(lambda: self._move_eq(1))
        self.btn_remove_eq.clicked.connect(self._on_remove_eq)
        for b in (self.btn_new_eq, self.btn_add_eq, self.btn_up, self.btn_down, self.btn_remove_eq):
            sbtns.addWidget(b)
        layout.addLayout(sbtns)

        self.btn_bypass = QPushButton("Bypass isolation band")
        self.btn_bypass.setToolTip(
            "Append a correction EQ that flattens the net EQ inside the current "
            "Frequency-isolation band (toggle/remove it like any EQ file)."
        )
        self.btn_bypass.clicked.connect(self._on_bypass)
        layout.addWidget(self.btn_bypass)

        # --- Measurements ---
        mbtns = QHBoxLayout()
        self.btn_load_meas = QPushButton("Load Measurement…")
        self.btn_clear_meas = QPushButton("Clear Measurement")
        self.btn_load_meas.clicked.connect(self._on_load_measurement)
        self.btn_clear_meas.clicked.connect(self._on_clear_measurement)
        mbtns.addWidget(self.btn_load_meas)
        mbtns.addWidget(self.btn_clear_meas)
        layout.addLayout(mbtns)

    # ------------------------------------------------------------ settings

    def apply_settings(self, settings: dict) -> None:
        meas_raw = settings.get("measurements_by_device", {})
        self._measurements = meas_raw if isinstance(meas_raw, dict) else {}
        self._refresh_devices()
        self._refresh_lists()
        self._emit_all_curves()

    def collect_settings(self, settings: dict) -> None:
        settings["measurements_by_device"] = dict(self._measurements)

    # --------------------------------------------------------------- library

    def _library(self) -> Optional[PresetLibrary]:
        try:
            config_dir = resolve_apo_config_dir(self._get_override())
        except ApoError:
            return None
        return PresetLibrary(config_dir)

    def _warn_no_apo(self) -> None:
        QMessageBox.warning(
            self, "Equalizer APO not found",
            "Could not find the Equalizer APO config directory. "
            "Set the path in the File menu.",
        )

    # --------------------------------------------------------------- util

    def _current_device(self) -> str:
        return self.device_combo.currentText() or ""

    def _device_slot(self, device: str) -> int:
        # -1 when the device isn't in the combo, so callers don't fall back to
        # slot 0 and overwrite the first device's curves with unknown data.
        return self.device_combo.findText(device)

    def _active_preset(self) -> Optional[Preset]:
        lib = self._library()
        device = self._current_device()
        if lib is None or not device:
            return None
        return lib.active_preset(device)

    def _selected_eq(self) -> Optional[EqFile]:
        item = self._stack.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    # --------------------------------------------------------------- refresh

    def _refresh_devices(self) -> None:
        lib = self._library()
        prev = self.device_combo.currentText()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        if lib is not None:
            for device in lib.list_devices():
                self.device_combo.addItem(device)
        idx = self.device_combo.findText(prev)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.blockSignals(False)

    def _refresh_lists(self) -> None:
        self._refresh_presets()
        self._refresh_stack()
        self._update_meas_label()

    def _refresh_presets(self) -> None:
        self._presets.clear()
        lib = self._library()
        device = self._current_device()
        if lib is None or not device:
            return
        for p in lib.list_presets(device):
            label = f"● {p.name}" if p.is_active else f"   {p.name}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, p)
            item.setToolTip(p.path)
            if p.is_active:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self._presets.addItem(item)

    def _refresh_stack(self) -> None:
        self._loading = True
        try:
            self._stack.clear()
            preset = self._active_preset()
            lib = self._library()
            if lib is not None and preset is not None:
                for eq in lib.list_eq_files(preset):
                    item = QListWidgetItem(eq.name)
                    item.setData(Qt.UserRole, eq)
                    item.setToolTip(eq.path)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if eq.enabled else Qt.Unchecked)
                    self._stack.addItem(item)
        finally:
            self._loading = False

    def _update_meas_label(self) -> None:
        meas = self._measurements.get(self._current_device())
        if meas:
            self._meas_label.setText(f"Measurement: {Path(meas).name}")
            self._meas_label.setStyleSheet("color: #6cf; font-style: normal;")
        else:
            self._meas_label.setText("No measurement loaded")
            self._meas_label.setStyleSheet("color: #999; font-style: italic;")

    def _on_device_changed(self, _text: str) -> None:
        self._refresh_lists()

    # --------------------------------------------------------- preset actions

    def _on_preset_clicked(self, item: QListWidgetItem) -> None:
        preset = item.data(Qt.UserRole)
        lib = self._library()
        if preset is None or lib is None:
            return
        try:
            lib.activate(preset)
        except ApoError as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        self._refresh_lists()
        self._emit_curves_for_device(self._current_device())

    def _on_new_preset(self) -> None:
        lib = self._library()
        device = self._current_device()
        if lib is None:
            self._warn_no_apo()
            return
        if not device:
            QMessageBox.information(self, "No device", "No device is available in config.txt.")
            return
        name, ok = QInputDialog.getText(self, "New preset", "Preset name:")
        if not ok or not name.strip():
            return
        try:
            lib.create_preset(device, name.strip())
        except (ApoError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not create preset", str(exc))
            return
        self._refresh_lists()

    def _on_add_preset_from_file(self) -> None:
        lib = self._library()
        device = self._current_device()
        if lib is None or not device:
            self._warn_no_apo()
            return
        path, _ = QFileDialog.getOpenFileName(self, "Add preset from file", "", _APO_FILTER)
        if not path:
            return
        try:
            lib.add_preset_from_file(device, path)
        except (ApoError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not add preset", str(exc))
            return
        self._refresh_lists()

    def _on_rename_preset(self) -> None:
        item = self._presets.currentItem()
        lib = self._library()
        if item is None or lib is None:
            return
        preset = item.data(Qt.UserRole)
        name, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=preset.name)
        if not ok or not name.strip():
            return
        try:
            lib.rename_preset(preset, name.strip())
        except (ValueError, ApoError, OSError) as exc:
            QMessageBox.warning(self, "Could not rename", str(exc))
            return
        self._refresh_lists()
        self._emit_curves_for_device(self._current_device())

    def _on_remove_preset(self) -> None:
        item = self._presets.currentItem()
        lib = self._library()
        if item is None or lib is None:
            return
        preset = item.data(Qt.UserRole)
        if QMessageBox.question(
            self, "Remove preset",
            f"Remove preset '{preset.name}' from this device?",
        ) != QMessageBox.Yes:
            return
        try:
            lib.remove_preset(preset)
        except (ApoError, OSError) as exc:
            QMessageBox.critical(self, "Could not remove", str(exc))
            return
        self._refresh_lists()
        self._emit_curves_for_device(self._current_device())

    # --------------------------------------------------- optimize / bypass

    def set_sample_rate(self, sample_rate: int) -> None:
        """Sample rate used only for the optimize accuracy readout (the
        GraphicEQ text itself is rate-independent; APO re-synthesizes per rate)."""
        try:
            self._sr = int(sample_rate)
        except (TypeError, ValueError):
            pass

    def _do_optimize(self) -> Tuple[Preset, int, float, int]:
        """Create the ``(optimized)`` preset + its GraphicEQ file (one bare
        GraphicEQ, or per-channel scoped GraphicEQs when L/R differ).

        Assumes an active preset exists. Returns ``(new_preset, n_points,
        max_dev_db, n_channel_groups)``. Dialog-free so it is unit-testable;
        ``_on_optimize`` wraps it with the user prompts."""
        from eq_helper.apo.writer import atomic_write_with_backup
        from eq_helper.dsp.eq_optimize import optimize_profile, render_channel_eqs

        lib = self._library()
        preset = self._active_preset()
        if lib is None or preset is None:
            raise ValueError("no active preset")
        groups, max_dev = optimize_profile(Path(preset.path), self._sr)
        content = render_channel_eqs(groups)
        n_points = sum(len(g.geq.bands) for g in groups)
        new = lib.create_preset(preset.device, f"{preset.name} (optimized)")
        try:
            eq = lib.create_eq_file(new, "optimized")
            atomic_write_with_backup(Path(eq.path), content)
        except Exception:
            # Don't leave an orphan "(optimized)" preset if the EQ write fails.
            try:
                lib.remove_preset(new)
            except Exception:
                pass
            raise
        return new, n_points, max_dev, len(groups)

    def _on_optimize(self) -> None:
        lib = self._library()
        preset = self._active_preset()
        if lib is None or preset is None:
            QMessageBox.information(
                self, "No active preset", "Activate a preset to optimize."
            )
            return
        try:
            new, n_points, max_dev, n_groups = self._do_optimize()
        except Exception as exc:  # DSP runs before any write, so no partial state
            QMessageBox.critical(self, "Optimize failed", str(exc))
            return
        self._refresh_lists()
        self._emit_curves_for_device(self._current_device())
        if n_groups > 1:
            head = (
                f"Combined the stack into {n_groups} channel-scoped GraphicEQs "
                f"({n_points} points total) — left/right EQ differ, so each side "
                f"is reproduced separately."
            )
        else:
            head = f"Combined the stack into one GraphicEQ ({n_points} points)."
        msg = (
            f"{head}\n"
            f"Max deviation vs the original: {max_dev:.3f} dB.\n\n"
            f"Created preset '{new.name}'. Click it to switch and compare."
        )
        if max_dev >= 0.1:
            msg += (
                "\n\nNote: deviation exceeds 0.1 dB — likely a very-high-Q "
                "deep-bass feature near APO's 16384-tap FIR resolution limit "
                "(inherent to any single-filter consolidation)."
            )
        QMessageBox.information(self, "Optimized", msg)

    def _do_balance(
        self, left_path: str, right_path: str, reference: str, invert: bool = False
    ) -> Tuple[EqFile, float, str]:
        """Generate a balance EQ and add it to the active preset, scoped to the
        non-reference channel (inverted when ``invert`` — a config that swaps
        L/R). Assumes an active preset exists. Returns ``(eq_file, max_dev_db,
        channel)``. Dialog-free for testability."""
        from eq_helper.apo.writer import atomic_write_with_backup
        from eq_helper.dsp.balance_eq import effective_other_channel, generate_balance_eq
        from eq_helper.dsp.eq_optimize import render_channel_eqs
        from eq_helper.dsp.overlay import load_frequency_response

        lib = self._library()
        preset = self._active_preset()
        if lib is None or preset is None:
            raise ValueError("no active preset")
        other = effective_other_channel(reference, invert)
        phys_other = "R" if reference == "L" else "L"
        left_f, left_db = load_frequency_response(left_path)
        right_f, right_db = load_frequency_response(right_path)
        if reference == "L":
            ref_f, ref_db, oth_f, oth_db = left_f, left_db, right_f, right_db
        else:
            ref_f, ref_db, oth_f, oth_db = right_f, right_db, left_f, left_db
        ceq, max_dev = generate_balance_eq(
            ref_f, ref_db, oth_f, oth_db, other, self._sr
        )
        content = render_channel_eqs([ceq])
        eq = lib.create_eq_file(preset, f"balance {phys_other} to {reference}")
        atomic_write_with_backup(Path(eq.path), content)
        return eq, max_dev, other

    def _active_preset_swapped(self) -> bool:
        """Whether the active preset's chain has an L/R channel swap in effect."""
        from eq_helper.dsp.eq_response import load_profile_lr_swap

        preset = self._active_preset()
        if preset is None:
            return False
        try:
            return load_profile_lr_swap(Path(preset.path))
        except Exception:
            return False

    def _on_balance(self) -> None:
        from eq_helper.ui.balance_eq_dialog import BalanceEqDialog

        lib = self._library()
        preset = self._active_preset()
        if lib is None or preset is None:
            QMessageBox.information(
                self, "No active preset", "Activate a preset first."
            )
            return
        swap = self._active_preset_swapped()
        dlg = BalanceEqDialog(initial_invert=swap, swap_detected=swap, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        left, right = dlg.left_path(), dlg.right_path()
        if not left or not right:
            return
        try:
            eq, max_dev, other = self._do_balance(
                left, right, dlg.reference(), dlg.invert()
            )
        except Exception as exc:  # DSP runs before any write, so no partial state
            QMessageBox.critical(self, "Balance EQ failed", str(exc))
            return
        self._refresh_stack()
        self._emit_curves_for_device(self._current_device())
        msg = (
            f"Created '{eq.name}'. Corrects the {dlg.reference()}-referenced "
            f"side at every frequency (Channel: {other}).\n"
            f"Max deviation: {max_dev:.3f} dB.\n\n"
            f"It's an EQ file in this preset — toggle or remove it any time."
        )
        if max_dev >= 0.1:
            msg += (
                "\n\nNote: deviation exceeds 0.1 dB — a very narrow/deep feature "
                "near APO's 16384-tap FIR resolution limit."
            )
        QMessageBox.information(self, "Balance EQ created", msg)

    def _do_balance_batch(
        self, folder: str, reference: str, invert: bool
    ) -> Tuple[List[str], float, int]:
        """Generate every reference×other balance EQ from ``folder`` as its own
        preset under the active device; activate the first. Assumes an active
        preset exists (for the device). Returns ``(preset_names, worst_max_dev,
        skipped)``. Dialog-free for testability."""
        from eq_helper.apo.writer import atomic_write_with_backup
        from eq_helper.dsp.balance_eq import (
            detect_measurement_sets,
            generate_balance_batch,
        )
        from eq_helper.dsp.eq_optimize import render_channel_eqs

        lib = self._library()
        device = self._current_device()
        if lib is None or not device:
            raise ValueError("no device")
        left, right = detect_measurement_sets(folder)
        results, skipped = generate_balance_batch(
            left, right, reference, self._sr, invert=invert
        )

        created: List[str] = []
        worst = 0.0
        first_preset = None
        for name, ceq, max_dev in results:
            content = render_channel_eqs([ceq])
            new = lib.create_preset(device, name)
            try:
                eq = lib.create_eq_file(new, name)
                atomic_write_with_backup(Path(eq.path), content)
            except Exception:
                try:
                    lib.remove_preset(new)
                except Exception:
                    pass
                raise
            created.append(new.name)
            worst = max(worst, max_dev)
            if first_preset is None:
                first_preset = new
        if first_preset is not None:
            lib.activate(first_preset)
        return created, worst, len(skipped)

    def _on_balance_batch(self) -> None:
        from eq_helper.ui.balance_batch_dialog import BalanceBatchDialog

        lib = self._library()
        if lib is None or not self._current_device():
            QMessageBox.information(
                self, "No device", "No device is available in config.txt."
            )
            return
        swap = self._active_preset_swapped()
        dlg = BalanceBatchDialog(initial_invert=swap, swap_detected=swap, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        folder = dlg.folder()
        if not folder:
            return
        try:
            created, worst, skipped = self._do_balance_batch(
                folder, dlg.reference(), dlg.invert()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Balance batch failed", str(exc))
            return
        if not created:
            QMessageBox.warning(
                self, "Nothing generated",
                "No candidates could be generated (no usable measurement "
                "pairs).",
            )
            return
        self._refresh_lists()
        self._emit_curves_for_device(self._current_device())
        msg = (
            f"Generated {len(created)} balance-EQ presets (reference "
            f"{dlg.reference()}). Worst max deviation: {worst:.3f} dB.\n\n"
            f"Click each in the switcher to audition, ↑/↓ to rank them, then "
            f"'Remove other candidates' to keep your favourite."
        )
        if skipped:
            msg += (
                f"\n\n{skipped} pairing(s) were skipped (unreadable file or no "
                f"overlapping frequency range)."
            )
        QMessageBox.information(self, "Balance candidates created", msg)

    def _move_preset(self, delta: int) -> None:
        lib = self._library()
        device = self._current_device()
        row = self._presets.currentRow()
        if lib is None or not device or row < 0:
            return
        order = [
            self._presets.item(i).data(Qt.UserRole).path
            for i in range(self._presets.count())
        ]
        new_row = row + delta
        if not (0 <= new_row < len(order)):
            return
        order[row], order[new_row] = order[new_row], order[row]
        try:
            lib.reorder_presets(device, order)
        except (ApoError, OSError) as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        self._refresh_presets()
        self._presets.setCurrentRow(new_row)

    def _on_remove_other_candidates(self) -> None:
        lib = self._library()
        device = self._current_device()
        if lib is None or not device:
            return
        presets = lib.list_presets(device)
        candidates = [p for p in presets if _CANDIDATE_RE.search(p.name)]
        if not candidates:
            QMessageBox.information(
                self, "No candidates", "No balance-EQ candidate presets found."
            )
            return
        # Pick the one to KEEP — never delete all. Prefer the selected
        # candidate, then the active candidate, else the first candidate.
        sel_item = self._presets.currentItem()
        sel = sel_item.data(Qt.UserRole) if sel_item is not None else None
        keep = None
        if sel is not None and _CANDIDATE_RE.search(sel.name):
            keep = sel
        if keep is None:
            keep = next((p for p in candidates if p.is_active), None)
        if keep is None:
            keep = candidates[0]
        losers = [p for p in candidates if p.path != keep.path]
        if not losers:
            QMessageBox.information(
                self, "Only one candidate",
                f"'{keep.name}' is the only candidate — nothing to remove.",
            )
            return
        if QMessageBox.question(
            self, "Remove other candidates",
            f"Remove {len(losers)} candidate preset(s)? Keeping '{keep.name}'.",
        ) != QMessageBox.Yes:
            return
        for p in losers:
            try:
                lib.remove_preset(p)
            except (ApoError, OSError):
                pass
        self._refresh_lists()
        self._emit_curves_for_device(self._current_device())

    def _do_bypass(self, lo: float, hi: float) -> EqFile:
        """Create a region-bypass correction EQ (per-channel aware) and move it
        to the FIRST position in the active preset's stack. Assumes an active
        preset exists. Dialog-free for testability."""
        from eq_helper.apo.writer import atomic_write_with_backup
        from eq_helper.dsp.eq_optimize import region_bypass_groups, render_channel_eqs

        lib = self._library()
        preset = self._active_preset()
        if lib is None or preset is None:
            raise ValueError("no active preset")
        groups = region_bypass_groups(Path(preset.path), lo, hi)
        content = render_channel_eqs(groups)
        eq = lib.create_eq_file(preset, f"bypass {round(lo)}-{round(hi)}Hz")
        atomic_write_with_backup(Path(eq.path), content)
        # The bypass must be applied first; move it to the head of the stack.
        order = [e.include_path for e in lib.list_eq_files(preset)]
        order = [eq.include_path] + [p for p in order if p != eq.include_path]
        lib.reorder_eq(preset, order)
        return eq

    def _on_bypass(self) -> None:
        lib = self._library()
        preset = self._active_preset()
        if lib is None or preset is None:
            QMessageBox.information(
                self, "No active preset", "Activate a preset first."
            )
            return
        band = self._get_isolation_band()
        if not band:
            QMessageBox.information(
                self, "No band", "Set the Frequency-isolation band first."
            )
            return
        fc, bw = float(band[0]), float(band[1])
        lo = max(20.0, fc - bw / 2.0)
        hi = min(20000.0, fc + bw / 2.0)
        try:
            self._do_bypass(lo, hi)
        except Exception as exc:  # DSP runs before any write, so no partial state
            QMessageBox.critical(self, "Bypass failed", str(exc))
            return
        self._refresh_stack()
        self._emit_curves_for_device(self._current_device())
        QMessageBox.information(
            self, "Bypassed",
            f"Added a correction that flattens the EQ between "
            f"{int(lo)} and {int(hi)} Hz on every channel.\nIt's the first EQ "
            f"file in this preset — toggle or remove it any time.",
        )

    # ---------------------------------------------------------- stack actions

    def _on_stack_double_clicked(self, item: QListWidgetItem) -> None:
        eq = item.data(Qt.UserRole)
        if eq is not None:
            self.editEqFileRequested.emit(eq.path)

    def _on_stack_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        eq = item.data(Qt.UserRole)
        preset = self._active_preset()
        lib = self._library()
        if eq is None or preset is None or lib is None:
            return
        enabled = item.checkState() == Qt.Checked
        try:
            lib.set_eq_enabled(preset, eq.include_path, enabled)
        except ApoError as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
        self._emit_curves_for_device(self._current_device())

    def _on_new_eq(self) -> None:
        preset = self._active_preset()
        lib = self._library()
        if preset is None or lib is None:
            QMessageBox.information(self, "No active preset", "Activate or create a preset first.")
            return
        name, ok = QInputDialog.getText(self, "New EQ file", "EQ file name:")
        if not ok or not name.strip():
            return
        try:
            eq = lib.create_eq_file(preset, name.strip())
        except (ApoError, OSError) as exc:
            QMessageBox.critical(self, "Could not create EQ file", str(exc))
            return
        self._refresh_stack()
        self._emit_curves_for_device(self._current_device())
        self.editEqFileRequested.emit(eq.path)

    def _on_add_eq_from_file(self) -> None:
        preset = self._active_preset()
        lib = self._library()
        if preset is None or lib is None:
            QMessageBox.information(self, "No active preset", "Activate or create a preset first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Add EQ file", "", _APO_FILTER)
        if not path:
            return
        try:
            lib.add_eq_file_from_file(preset, path)
        except (ApoError, OSError) as exc:
            QMessageBox.critical(self, "Could not add EQ file", str(exc))
            return
        self._refresh_stack()
        self._emit_curves_for_device(self._current_device())

    def _move_eq(self, delta: int) -> None:
        preset = self._active_preset()
        lib = self._library()
        row = self._stack.currentRow()
        if preset is None or lib is None or row < 0:
            return
        order = [self._stack.item(i).data(Qt.UserRole).include_path for i in range(self._stack.count())]
        new_row = row + delta
        if not (0 <= new_row < len(order)):
            return
        order[row], order[new_row] = order[new_row], order[row]
        try:
            lib.reorder_eq(preset, order)
        except ApoError as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        self._refresh_stack()
        self._stack.setCurrentRow(new_row)
        self._emit_curves_for_device(self._current_device())

    def _on_remove_eq(self) -> None:
        preset = self._active_preset()
        lib = self._library()
        eq = self._selected_eq()
        if preset is None or lib is None or eq is None:
            return
        try:
            lib.remove_eq(preset, eq.include_path)
        except ApoError as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        self._refresh_stack()
        self._emit_curves_for_device(self._current_device())

    # --------------------------------------------------------- measurement

    def _on_load_measurement(self) -> None:
        device = self._current_device()
        if not device:
            QMessageBox.information(self, "No device", "No device available.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Load measurement for {device}", "",
            "CSV files (*.csv *.txt);;All files (*.*)",
        )
        if not path:
            return
        try:
            load_measurement_csv(path)
        except OverlayLoadError as exc:
            QMessageBox.warning(self, "Could not load", str(exc))
            return
        self._measurements[device] = path
        self._update_meas_label()
        self._emit_curves_for_device(device)

    def _on_clear_measurement(self) -> None:
        device = self._current_device()
        if not device:
            return
        self._measurements[device] = None
        self._update_meas_label()
        self._emit_curves_for_device(device)

    # --------------------------------------------------------- curve emission

    def _load_measurement_data(self, device: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        path = self._measurements.get(device)
        if not path:
            return None, None
        try:
            freqs, db = load_measurement_csv(path)
            db = normalize_at_1khz(freqs, db)
            return freqs, db
        except OverlayLoadError:
            return None, None

    def _load_eq_data(self, device: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        lib = self._library()
        if lib is None:
            return None, None
        active = lib.active_preset(device)
        if active is None:
            return None, None
        eq_db = load_profile_eq_response(Path(active.path), _EQ_FREQS)
        if eq_db is None:
            return None, None
        return _EQ_FREQS, eq_db

    def _emit_curves_for_device(self, device: str) -> None:
        if not device:
            return
        slot = self._device_slot(device)
        if slot < 0 or slot > 1:
            return
        m_freqs, m_db = self._load_measurement_data(device)
        eq_freqs, eq_db = self._load_eq_data(device)
        self.curvesChanged.emit(slot, m_freqs, m_db, eq_freqs, eq_db)

    def _emit_all_curves(self) -> None:
        for i in range(min(2, self.device_combo.count())):
            self._emit_curves_for_device(self.device_combo.itemText(i))
