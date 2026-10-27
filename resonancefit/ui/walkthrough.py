"""Three-step first-launch walkthrough (shown once; reopen from Settings → Help)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .common import heading, hint, tr

STEPS = (
    ("Step 1 of 3: find your resonance",
     "Put your IEMs in with the same ear tips you were measured with, turn your volume down and turn "
     "off any other EQ (your music app's, sound enhancements, your dongle's). "
     "Press Find it for me: a short listening test (about 2 minutes) asks which sound is louder, "
     "then which is smoothest, for each ear. Or adjust by hand with a tone and the slider."),
    ("Step 2 of 3: pick a target and listen",
     "The target is the sound you want; the one marked default was chosen for you. Play a song you "
     "know well and switch the EQ on and off to compare."),
    ("Step 3 of 3: save it and use it everywhere",
     "Export the EQ for the app you listen with — or apply it directly with Equalizer APO (Windows) or EasyEffects (Linux). "
     "Every tuning you save, export or apply is kept in History, so you can always go back."),
)


class Walkthrough(QDialog):
    def __init__(self, product_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Welcome to {product}").format(product=product_name))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        for title, body in STEPS:
            page = QWidget()
            v = QVBoxLayout(page)
            v.addWidget(heading(tr(title), 2))
            text = QLabel(tr(body))
            text.setWordWrap(True)
            v.addWidget(text)
            v.addStretch(1)
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        layout.addWidget(hint(tr("Press Space at any time to stop all sound.")))
        row = QHBoxLayout()
        skip = QPushButton(tr("Skip"))
        skip.setFlat(True)
        skip.clicked.connect(self.accept)
        row.addWidget(skip)
        row.addStretch(1)
        self.back_button = QPushButton(tr("Back"))
        self.back_button.clicked.connect(lambda: self._go(-1))
        row.addWidget(self.back_button)
        self.next_button = QPushButton(tr("Next"))
        self.next_button.setObjectName("primary")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(lambda: self._go(1))
        row.addWidget(self.next_button)
        layout.addLayout(row)
        self._sync()

    def count(self) -> int:
        return self.stack.count()

    def index(self) -> int:
        return self.stack.currentIndex()

    def _go(self, step: int) -> None:
        target = self.index() + step
        if target >= self.count():
            self.accept()
            return
        self.stack.setCurrentIndex(max(0, target))
        self._sync()

    def _sync(self) -> None:
        self.back_button.setEnabled(self.index() > 0)
        self.next_button.setText(tr("Start") if self.index() == self.count() - 1 else tr("Next"))
