"""Generate customer app… — the operator turns a measuring session into a
calibration file, a calibration sheet and per-OS customer downloads.

The work runs in :func:`eq_helper.admin.generate.generate` on a worker
thread; this dialog collects the fields, previews the measurement sets,
remembers the operator's accessories and prefills from the last record.
"""

from __future__ import annotations

import gc
import threading
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from eq_helper.admin.build_cache import SYSTEM_NAMES, SYSTEMS, BuildCache
from eq_helper.admin.customer_library import CustomerLibrary, MeasurementSource
from eq_helper.admin.generate import DIAGNOSTIC_TARGETS, GenerateRequest, generate, preview
from eq_helper.calibration.branding import load_branding
from eq_helper.calibration.rescal import (
    Accessory, Customer, Iem, Manifest, Operator, SongLink, new_calibration_id,
)

_ROOT = Path(__file__).resolve().parents[2]
TARGETS_DIR = _ROOT / "assets" / "targets"
DEFAULT_TARGET = "PEQdB Diamond.txt"
DEFAULT_COUPLER = "IEC-711 clone"
ACCESSORY_KINDS = (("tips", "Ear tips"), ("dac", "DAC / amp"), ("cable", "Cable"),
                   ("nozzle", "Nozzle / filter"), ("other", "Other"))

_gc_lock = threading.Lock()
_gc_pauses = 0


def _run_without_gc(fn):
    """Run ``fn`` with automatic GC paused: a collection started on a worker
    thread can free Qt objects off the GUI thread and crash."""
    global _gc_pauses
    with _gc_lock:
        _gc_pauses += 1
        gc.disable()
    try:
        return fn()
    finally:
        with _gc_lock:
            _gc_pauses -= 1
            if _gc_pauses == 0:
                gc.enable()


class _Job(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = _run_without_gc(lambda: self._fn(self.progress.emit))
        except Exception as exc:  # noqa: BLE001 - shown to the operator
            self.failed.emit(str(exc))
        else:
            self.done.emit(result)


def _folder_row(edit: QLineEdit, parent) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    browse = QPushButton("Browse…")

    def pick():
        folder = QFileDialog.getExistingDirectory(parent, "Choose a folder", edit.text())
        if folder:
            edit.setText(folder)
            edit.editingFinished.emit()
    browse.clicked.connect(pick)
    row.addWidget(browse)
    return row


class GenerateCustomerDialog(QDialog):
    def __init__(self, parent=None, *, settings: dict | None = None, library: CustomerLibrary | None = None,
                 cache=None, branding=None, targets_dir: Path = TARGETS_DIR, opener=None):
        super().__init__(parent)
        self.setWindowTitle("Generate customer app…")
        self.resize(820, 900)
        self.settings = settings if settings is not None else {}
        self.library = library or CustomerLibrary()
        self.cache = cache or BuildCache(local_folder=self.settings.get("build_local_folder"))
        self.branding = branding or load_branding()
        self.targets_dir = Path(targets_dir)
        self.opener = opener or (lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        self.result = None
        self._jobs: set[_Job] = set()
        self._preview_token = 0

        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Start from a saved calibration:"))
        self.library_combo = QComboBox()
        self.library_combo.addItem("New calibration", None)
        for record in self.library.records():
            m = record.manifest
            self.library_combo.addItem(f"{m.customer.name} – {m.iem.display_name} ({m.created})", record.record_id)
        self.library_combo.currentIndexChanged.connect(self._load_record)
        top.addWidget(self.library_combo, 1)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form_layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # customer and IEM
        who = QGroupBox("Customer and IEM")
        f = QFormLayout(who)
        self.customer_name = QComboBox()
        self.customer_name.setEditable(True)
        self.customer_name.addItem("")
        self._contacts = {}
        for c in self.library.customers():
            self.customer_name.addItem(c.name)
            self._contacts[c.name] = c.contact or ""
        self.customer_name.setCurrentText("")
        self.customer_name.currentTextChanged.connect(self._customer_picked)
        f.addRow("Customer", self.customer_name)
        self.customer_contact = QLineEdit()
        self.customer_contact.setPlaceholderText("optional")
        f.addRow("Contact", self.customer_contact)
        self.iem_brand, self.iem_model, self.iem_serial = QLineEdit(), QLineEdit(), QLineEdit()
        self.iem_serial.setPlaceholderText("optional")
        f.addRow("IEM brand", self.iem_brand)
        f.addRow("IEM model", self.iem_model)
        f.addRow("Serial", self.iem_serial)
        form_layout.addWidget(who)

        # accessories
        acc = QGroupBox("Accessories (leave empty if not used)")
        grid = QGridLayout(acc)
        self.accessories: dict[str, tuple[QComboBox, QComboBox]] = {}
        for row, (kind, label) in enumerate(ACCESSORY_KINDS):
            grid.addWidget(QLabel(label), row, 0)
            desc = QComboBox()
            desc.setEditable(True)
            remembered = self.library.operator_accessories(kind)
            desc.addItems([""] + remembered)
            desc.setCurrentText("")
            grid.addWidget(desc, row, 1)
            whose = QComboBox()
            whose.addItem("Customer's", "customer")
            whose.addItem("Mine", "operator")
            whose.setCurrentIndex(1 if kind != "tips" else 0)
            desc.currentTextChanged.connect(lambda text, w=whose, r=remembered: text in r and w.setCurrentIndex(1))
            grid.addWidget(whose, row, 2)
            self.accessories[kind] = (desc, whose)
        grid.setColumnStretch(1, 1)
        form_layout.addWidget(acc)

        # session details
        details = QGroupBox("Session")
        f = QFormLayout(details)
        self.coupler = QLineEdit(self.settings.get("customer_coupler") or DEFAULT_COUPLER)
        f.addRow("Coupler", self.coupler)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        f.addRow("Calibration date", self.date_edit)
        self.operator_name = QLineEdit(self.settings.get("operator_name") or self.branding.byline.removeprefix("by "))
        f.addRow("Operator", self.operator_name)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(54)
        f.addRow("Notes", self.notes)
        form_layout.addWidget(details)

        # measurements
        meas = QGroupBox("Measurements")
        v = QVBoxLayout(meas)
        self.one_folder_radio = QRadioButton("One folder (L/R detected from names)")
        self.sides_radio = QRadioButton("Separate L and R folders")
        group = QButtonGroup(self)
        group.addButton(self.one_folder_radio)
        group.addButton(self.sides_radio)
        self.one_folder_radio.setChecked(True)
        v.addWidget(self.one_folder_radio)
        self.folder_edit = QLineEdit()
        v.addLayout(_folder_row(self.folder_edit, self))
        v.addWidget(self.sides_radio)
        g = QGridLayout()
        self.left_edit, self.right_edit = QLineEdit(), QLineEdit()
        g.addWidget(QLabel("Left"), 0, 0)
        g.addLayout(_folder_row(self.left_edit, self), 0, 1)
        g.addWidget(QLabel("Right"), 1, 0)
        g.addLayout(_folder_row(self.right_edit, self), 1, 1)
        v.addLayout(g)
        self.preview_label = QLabel("Choose the measurement folder to preview the fits.")
        self.preview_label.setWordWrap(True)
        v.addWidget(self.preview_label)
        self.preview_warning = QLabel()
        self.preview_warning.setWordWrap(True)
        self.preview_warning.setStyleSheet("color: #A64B00;")
        v.addWidget(self.preview_warning)
        for edit in (self.folder_edit, self.left_edit, self.right_edit):
            edit.editingFinished.connect(self._refresh_preview)
        self.one_folder_radio.toggled.connect(self._sync_mode)
        form_layout.addWidget(meas)

        # targets and songs
        tg = QGroupBox("Targets and test songs")
        v = QVBoxLayout(tg)
        v.addWidget(QLabel("Targets to include:"))
        self.target_list = QListWidget()
        self.target_list.setFixedHeight(150)
        for path in sorted(self.targets_dir.glob("*.txt"), key=lambda p: p.name.casefold()):
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            diagnostic = path.name in DIAGNOSTIC_TARGETS
            item.setCheckState(Qt.Unchecked if diagnostic else Qt.Checked)
            if diagnostic:
                item.setToolTip("Not a coupler target (no ear gain): for checks in EQ Helper, not for customers.")
            self.target_list.addItem(item)
        self.target_list.itemChanged.connect(lambda _i: self._sync_default_targets())
        v.addWidget(self.target_list)
        row = QHBoxLayout()
        row.addWidget(QLabel("Default target:"))
        self.default_target = QComboBox()
        row.addWidget(self.default_target, 1)
        v.addLayout(row)
        v.addWidget(QLabel("Test-song links, one per line as “Label | URL”:"))
        self.links_edit = QPlainTextEdit()
        self.links_edit.setFixedHeight(70)
        v.addWidget(self.links_edit)
        form_layout.addWidget(tg)

        # output
        out = QGroupBox("Output")
        v = QVBoxLayout(out)
        row = QHBoxLayout()
        self.system_checks: dict[str, QCheckBox] = {}
        for system in SYSTEMS:
            check = QCheckBox(SYSTEM_NAMES[system])
            check.setChecked(system == "windows")
            self.system_checks[system] = check
            row.addWidget(check)
        self.file_only = QCheckBox("Calibration file only (recalibration)")
        self.file_only.toggled.connect(self._sync_file_only)
        row.addWidget(self.file_only)
        row.addStretch(1)
        v.addLayout(row)
        self.output_edit = QLineEdit(self.settings.get("customer_output_dir") or str(Path.home() / "dist-customers"))
        v.addLayout(_folder_row(self.output_edit, self))
        row = QHBoxLayout()
        self.check_build_button = QPushButton("Check for new build")
        self.check_build_button.clicked.connect(self._check_build)
        row.addWidget(self.check_build_button)
        self.build_status = QLabel()
        self.build_status.setWordWrap(True)
        row.addWidget(self.build_status, 1)
        v.addLayout(row)
        form_layout.addWidget(out)
        form_layout.addStretch(1)

        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setDefault(True)
        self.generate_button.clicked.connect(self.run_generate)
        bottom.addWidget(self.generate_button)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        outer.addLayout(bottom)

        self._sync_default_targets()
        self._sync_mode()
        self._prefill_from_last()

    # ── prefill ────────────────────────────────────────────────────────────
    def _apply_manifest(self, m: Manifest, with_customer: bool) -> None:
        if with_customer:
            self.customer_name.setCurrentText(m.customer.name)
            self.customer_contact.setText(m.customer.contact or "")
        self.iem_brand.setText(m.iem.brand)
        self.iem_model.setText(m.iem.model)
        self.iem_serial.setText((m.iem.serial or "") if with_customer else "")
        for desc, _whose in self.accessories.values():
            desc.setCurrentText("")
        for a in m.accessories:
            if a.kind in self.accessories and (with_customer or a.supplied_by == "operator"):
                desc, whose = self.accessories[a.kind]
                desc.setCurrentText(a.description)
                whose.setCurrentIndex(whose.findData(a.supplied_by))
        if m.coupler:
            self.coupler.setText(m.coupler)
        if m.operator and m.operator.name:
            self.operator_name.setText(m.operator.name)
        if with_customer:
            self.notes.setPlainText(m.notes)
        self.links_edit.setPlainText("\n".join(f"{l.label} | {l.url}" for l in m.test_song_links))

    def _apply_targets_and_systems(self, record) -> None:
        if record.targets:
            wanted = {Path(t).name for t in record.targets}
            for i in range(self.target_list.count()):
                item = self.target_list.item(i)
                item.setCheckState(Qt.Checked if item.text() in wanted else Qt.Unchecked)
        self._sync_default_targets(record.manifest.default_target)
        self.file_only.setChecked(not record.systems)
        for system, check in self.system_checks.items():
            check.setChecked(system in record.systems)

    def _prefill_from_last(self) -> None:
        record = self.library.last_record()
        if record is None:
            for kind, (desc, whose) in self.accessories.items():
                last = self.library.last_used(kind)
                if last:
                    desc.setCurrentText(last)
                    whose.setCurrentIndex(whose.findData("operator"))
            return
        self._apply_manifest(record.manifest, with_customer=False)
        self._apply_targets_and_systems(record)

    def _load_record(self, index: int) -> None:
        record_id = self.library_combo.itemData(index)
        if not record_id:
            return
        record = self.library.get(record_id)
        self._apply_manifest(self.library.regenerate_manifest(record, new_calibration=True), with_customer=True)
        self.date_edit.setDate(QDate.currentDate())
        source = record.source
        if source.mode == "folder":
            self.one_folder_radio.setChecked(True)
            self.folder_edit.setText(source.folder or "")
        else:
            self.sides_radio.setChecked(True)
            self.left_edit.setText(source.left or "")
            self.right_edit.setText(source.right or "")
        self._apply_targets_and_systems(record)
        self._refresh_preview()

    def _customer_picked(self, name: str) -> None:
        if name in self._contacts and not self.customer_contact.text():
            self.customer_contact.setText(self._contacts[name])

    # ── small syncs ────────────────────────────────────────────────────────
    def chosen_targets(self) -> list[Path]:
        return [Path(self.target_list.item(i).data(Qt.UserRole)) for i in range(self.target_list.count())
                if self.target_list.item(i).checkState() == Qt.Checked]

    def _sync_default_targets(self, wanted: str | None = None) -> None:
        current = wanted or self.default_target.currentText() or DEFAULT_TARGET
        names = [p.name for p in self.chosen_targets()]
        self.default_target.blockSignals(True)
        self.default_target.clear()
        self.default_target.addItems(names)
        index = self.default_target.findText(current)
        if index < 0:
            index = self.default_target.findText(DEFAULT_TARGET)
        self.default_target.setCurrentIndex(max(index, 0))
        self.default_target.blockSignals(False)

    def _sync_mode(self) -> None:
        one = self.one_folder_radio.isChecked()
        self.folder_edit.setEnabled(one)
        self.left_edit.setEnabled(not one)
        self.right_edit.setEnabled(not one)
        self._refresh_preview()

    def _sync_file_only(self, only: bool) -> None:
        for check in self.system_checks.values():
            check.setEnabled(not only)

    def _source(self) -> MeasurementSource | None:
        if self.one_folder_radio.isChecked():
            folder = self.folder_edit.text().strip()
            return MeasurementSource.from_folder(folder) if folder else None
        left, right = self.left_edit.text().strip(), self.right_edit.text().strip()
        return MeasurementSource.from_sides(left, right) if left and right else None

    # ── preview ────────────────────────────────────────────────────────────
    def _refresh_preview(self) -> None:
        source = self._source()
        self._preview_token += 1
        token = self._preview_token
        if source is None:
            self.preview_label.setText("Choose the measurement folder to preview the fits.")
            self.preview_warning.setText("")
            return
        self.preview_label.setText("Reading measurements…")
        self.preview_warning.setText("")
        job = _Job(lambda _say: preview(source), self)
        job.done.connect(lambda info: self._show_preview(token, info))
        job.failed.connect(lambda msg: self._show_preview_error(token, msg))
        self._start(job)

    def _show_preview(self, token, info) -> None:
        if token != self._preview_token:
            return
        self.preview_label.setText(info.summary())
        self.preview_warning.setText("\n".join(info.warnings_below_three() + info.warnings[:4]))

    def _show_preview_error(self, token, message) -> None:
        if token != self._preview_token:
            return
        self.preview_label.setText("These measurements can't be used:")
        self.preview_warning.setText(message)

    # ── build cache ────────────────────────────────────────────────────────
    def _check_build(self) -> None:
        self.build_status.setText("Checking…")
        job = _Job(lambda _say: self.cache.check_latest(), self)
        job.done.connect(lambda r: self.build_status.setText(
            f"Latest release {r.version}: " + ", ".join(SYSTEM_NAMES[s] for s in SYSTEMS if s in r.assets)))
        job.failed.connect(self.build_status.setText)
        self._start(job)

    # ── generating ─────────────────────────────────────────────────────────
    def request(self) -> GenerateRequest:
        """Collect and validate the fields. Raises ValueError with what's missing."""
        name = self.customer_name.currentText().strip()
        if not name:
            raise ValueError("Enter the customer's name.")
        brand, model = self.iem_brand.text().strip(), self.iem_model.text().strip()
        if not brand or not model:
            raise ValueError("Enter the IEM brand and model.")
        source = self._source()
        if source is None:
            raise ValueError("Choose the measurement folder (or the L and R folders).")
        targets = self.chosen_targets()
        if not targets:
            raise ValueError("Include at least one target.")
        accessories = [Accessory(kind, desc.currentText().strip(), whose.currentData())
                       for kind, (desc, whose) in self.accessories.items() if desc.currentText().strip()]
        links = []
        for line in self.links_edit.toPlainText().splitlines():
            if not line.strip():
                continue
            label, sep, url = line.partition("|")
            label, url = label.strip(), url.strip()
            links.append(SongLink(label, url) if sep and url else SongLink(line.strip(), line.strip()))
        operator = self.operator_name.text().strip()
        manifest = Manifest(
            customer=Customer(name, self.customer_contact.text().strip() or None),
            iem=Iem(brand, model, self.iem_serial.text().strip() or None),
            accessories=accessories, coupler=self.coupler.text().strip(), notes=self.notes.toPlainText().strip(),
            default_target=self.default_target.currentText() or DEFAULT_TARGET, test_song_links=links,
            operator=Operator(operator) if operator else None,
            created=self.date_edit.date().toString(Qt.ISODate) or date.today().isoformat(),
            calibration_id=new_calibration_id())
        systems = [] if self.file_only.isChecked() else [s for s in SYSTEMS if self.system_checks[s].isChecked()]
        if not self.file_only.isChecked() and not systems:
            raise ValueError("Choose at least one system, or “Calibration file only”.")
        output = self.output_edit.text().strip()
        if not output:
            raise ValueError("Choose an output folder.")
        return GenerateRequest(manifest, source, targets, systems, Path(output))

    def run_generate(self) -> None:
        try:
            request = self.request()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.settings.update({"customer_output_dir": str(request.output_dir),
                              "customer_coupler": request.manifest.coupler,
                              "operator_name": request.manifest.operator.name if request.manifest.operator else ""})
        self.generate_button.setEnabled(False)
        self.result = None
        library, cache, branding = self.library, self.cache, self.branding
        job = _Job(lambda say: generate(request, library, cache, branding, say), self)
        job.progress.connect(self.status.setText)
        job.done.connect(lambda result: self._generated(request, result))
        job.failed.connect(self._failed)
        self._start(job)

    def _generated(self, request, result) -> None:
        self.generate_button.setEnabled(True)
        self.result = result
        text = f"Done: {len(result.outputs)} files in {request.output_dir}."
        if result.warnings:
            text += " " + " ".join(result.warnings[:2])
        self.status.setText(text)
        self.opener(request.output_dir)

    def _failed(self, message: str) -> None:
        self.generate_button.setEnabled(True)
        self.status.setText(f"Couldn't generate: {message}")

    def _start(self, job: _Job) -> None:
        self._jobs.add(job)
        job.finished.connect(lambda: self._jobs.discard(job))
        job.start()

    def done(self, result) -> None:
        for job in list(self._jobs):
            job.wait(30000)
        super().done(result)

