"""The guided "Find my resonance" test, shown in place of Find your resonance's contents.

Drives a :class:`~resonancefit.guided_test.GuidedTest`. One Play/Pause bar
plays the selected option continuously; clicking a card switches the sound
at once (noise bands crossfade in the engine, EQs crossfade in ``StereoFIR``).
An answer can be given at any time (a ✓ marks the cards heard). Cards
are numbered 1–8 (their keys), never show frequencies, and their
animation follows the overall level only, so nothing gives the answer away.

With the calibration's model ready (its roughness tables are built on a
worker while the intro shows), each ear is the scorecard search (see
:mod:`resonancefit.scorecard`): an overview of eight EQs (two rows of
cards; right-click or X rules a card out, it stays playable), then A/B
rounds, all through Stage 2's sounds. Without the model each ear gets
Stage 2 alone from the calibration's guess; without that, Stage 1 first.
Stage 1 (Louder test) plays narrow noise bands (the search picks their
width) with the EQ preview off; Stage 2
(Smoothness check) plays pink noise, grey noise, focused noise (grey noise
only around the guess) or a slow focused sweep
(the customer switches between them at will) through three EQs designed on a worker
thread. After both primary resonances the upper treble is offered: no
louder test up there (hearing falls off too steeply to judge loudness), but
one question per ear — is a band at the predicted peak heard clearly? — and
then the smoothness check around the calibration's prediction (the sweep
first; EQs that move only the upper resonance). The EQ preview is put
back as it was when the test closes.
"""

from __future__ import annotations

import atexit
import random
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..guided_test import LOUDER, OVERVIEW, PICK, PRIMARY, UPPER, GuidedTest
from ..loudness import card_curve, level_offsets
from .common import Worker, side_name, tr
from .guided_widgets import (
    CARD_MARGIN, AnswerButton, OptionCard, ProgressLine, StageBackground, TransportBar, accent_colors,
    level_from_samples, stage_colors,
)

HEARD_SECONDS = 0.5   # an option counts as heard after this long selected while playing
FRAME_MS = 33         # ~30 fps while sound plays
LEVEL_SAMPLES = 1024
LEVEL_SMOOTHING = 0.35
LETTERS = "12345678"   # card names (and their keys): up to eight (the overview), four to a row
CARDS_PER_ROW = 4
STAGE2_SOUNDS = ("pink", "grey", "region", "focus")  # the smoothness check: pink, grey or focused noise, or the sweep
CENTRED_SOUNDS = ("region", "focus")  # follow each round's options

_JOBS: set = set()


@atexit.register
def _join_jobs() -> None:
    for job in list(_JOBS):
        job.wait(5000)


def _ear_title(side: str, round_number: int) -> str:
    if side == "M":
        return tr("Both ears · Round {n}").format(n=round_number)
    return tr("{ear} ear · Round {n}").format(ear=side_name(side), n=round_number)


def _ear(side: str) -> str:
    return side_name(side) if side == "M" else tr("{ear} ear").format(ear=side_name(side))


class GuidedTestView(StageBackground):
    """The whole test: a short intro, the rounds, and a result per ear.

    ``option_eqs(side, hz_list, target)`` is called on the GUI thread and
    returns a function that, run on a worker thread, builds Stage 2's
    ``[(channel_eq, designed_firs), …]`` (one per option, in order; ``target``
    is "primary" or "upper"). Signals: ``resultFound(side, hz)`` when an ear's
    primary resonance is done, ``upperFound(side, hz_or_None)`` for its upper
    resonance (``None``: no clear upper peak, leave it as it is),
    ``adjustByHand(found)`` to leave for the manual controls (``found``:
    {"primary": {side: Hz}, "upper": {side: Hz}} found so far) and ``finished()``.

    ``upper_prediction(side, primary_hz)`` (optional): where the calibration
    puts that ear's upper peak for its found primary resonance, or ``None``.
    Without it the upper treble isn't offered.

    ``tables()`` (optional) is called on the GUI thread when a test starts
    and returns a function that, on a worker thread, returns ``{side:
    RoughnessTable}``; with it, each ear is the overview and A/B rounds.
    """

    resultFound = Signal(str, float)
    upperFound = Signal(str, object)
    adjustByHand = Signal(object)
    apoPauseRequested = Signal()  # the intro's "Pause it while I tune" (our EQ is on system-wide)
    finished = Signal()

    def __init__(self, audio, option_eqs=None, upper_prediction=None, tables=None, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.option_eqs = option_eqs
        self.upper_prediction = upper_prediction
        self.tables = tables
        self._table_generation = 0
        self._preparing = False        # the roughness tables are being built
        self._begin_when_ready = False
        self._sides: tuple = ()
        self._defaults: dict = {}
        self.ruled_out: set[int] = set()  # overview cards crossed out this round
        self.test: GuidedTest | None = None
        self.rng = random.Random()
        self._clock = time.monotonic
        self._t0 = self._clock()
        self._round_index = 0
        self._round = None
        self.selected: int | None = None
        self.heard: set[int] = set()
        self._heard_for: dict[int, float] = {}
        self._since: float | None = None
        self._level = 0.0
        self._sound = None            # "band" or one of STAGE2_SOUNDS while this view's sound plays
        self.stage2_sound = "pink"    # what the smoothness check plays: one of STAGE2_SOUNDS
        self._starting = False        # starting our own sound: its change notices aren't a stop
        self._eqs: dict[int, tuple] = {}
        self._offsets: dict[int, float] = {}  # dB per option making every card equally loud
        self._eq_generation = 0
        self._applied: int | None = None   # option whose EQ is in the preview
        self._saved_eq_enabled: bool | None = None
        self._other_eq = ("", False)
        self._play_when_ready = False  # a card was clicked before Stage 2's EQs were ready

        self._frame = QTimer(self)
        self._frame.setInterval(FRAME_MS)
        self._frame.timeout.connect(self._tick)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 20, 16, 20)
        outer.addStretch(1)
        self.column = QWidget()
        self.column.setMaximumWidth(720)
        self.column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer.addWidget(self.column, 6)
        outer.addStretch(1)
        column = QVBoxLayout(self.column)
        column.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        column.addWidget(self.pages)
        column.addStretch(1)
        self.intro_page = self._build_intro()
        self.test_page = self._build_test()
        self.unsure_page = self._build_unsure()
        self.done_page = self._build_done()
        self.upper_page = self._build_upper_offer()
        for page in (self.intro_page, self.test_page, self.unsure_page, self.done_page, self.upper_page):
            self.pages.addWidget(page)
        self._restyle()
        audio.on_change(self._audio_changed)

    # ── layout ─────────────────────────────────────────────────────────────
    def _label(self, text: str = "", name: str = "gtText", wrap: bool = True) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(wrap)
        return label

    def _header(self, layout: QVBoxLayout, title: QLabel, right: QLabel | None = None) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.1)
        title.setFont(font)
        row.addWidget(title, 1)
        if right is not None:
            row.addWidget(right, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addLayout(row)
        divider = QFrame()
        divider.setObjectName("gtDivider")
        divider.setFixedHeight(1)
        box = QHBoxLayout()
        box.setContentsMargins(CARD_MARGIN, 6, CARD_MARGIN, 10)
        box.addWidget(divider)
        layout.addLayout(box)

    def _padded(self, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        row.addWidget(widget)
        return row

    def _build_intro(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.intro_title = self._label(tr("Before you start"), "gtTitle")
        self._header(layout, self.intro_title)
        tips = [
            tr("Put both IEMs in as you normally wear them, with the ear tips you were measured with."),
            tr("Turn your volume low: some sounds can be piercing. Press Space at any time to stop the sound."),
        ]
        for text in tips:
            layout.addLayout(self._padded(self._label("•  " + text)))
        self.intro_steps = self._label()
        layout.addLayout(self._padded(self.intro_steps))
        self.intro_other_eq = self._label()
        layout.addLayout(self._padded(self.intro_other_eq))
        pause_row = QHBoxLayout()
        pause_row.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        self.intro_pause = QPushButton(tr("Pause it while I tune"))
        self.intro_pause.clicked.connect(self.apoPauseRequested)
        self.intro_pause.hide()
        pause_row.addWidget(self.intro_pause)
        pause_row.addStretch(1)
        layout.addLayout(pause_row)
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.start_button = QPushButton(tr("Start the test"))
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.begin)
        row.addWidget(self.start_button)
        self.intro_cancel = QPushButton(tr("Cancel"))
        self.intro_cancel.clicked.connect(self.leave_for_manual)
        row.addWidget(self.intro_cancel)
        row.addStretch(1)
        layout.addLayout(row)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.begin))
        layout.addStretch(1)
        return page

    def _build_test(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = self._label("", "gtTitle", wrap=False)
        self.stage_label = self._label("", "gtMuted", wrap=False)
        self._header(layout, self.title, self.stage_label)
        self.instructions = self._label("", "gtMuted")
        layout.addLayout(self._padded(self.instructions))
        layout.addSpacing(10)

        self.transport = TransportBar()
        self.transport.clicked.connect(self.toggle_play)
        layout.addLayout(self._padded(self.transport))
        layout.addSpacing(6)

        self.sound_row = QWidget()
        sound = QHBoxLayout(self.sound_row)
        sound.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        sound.addWidget(self._label(tr("Listen with"), "gtMuted", wrap=False))
        self.noise_button = QPushButton(tr("Pink noise"))
        self.noise_button.setToolTip(tr("Pink noise through each EQ (N)"))
        self.grey_button = QPushButton(tr("Grey noise"))
        self.grey_button.setToolTip(tr("Grey noise through each EQ: like pink, with the treble lifted the way your "
                                       "hearing needs it, so a harsh or dull spot up high stands out more (G)"))
        self.region_button = QPushButton(tr("Focused noise"))
        self.region_button.setToolTip(tr("Grey noise only around where the EQs differ, so nothing else draws your "
                                         "ear (F)"))
        self.sweep_button = QPushButton(tr("Sweep"))
        self.sweep_button.setToolTip(tr("A slow sweep through each EQ: the right one stays even, with no "
                                        "swell or dip as it passes (W)"))
        self.stage2_buttons = {"pink": self.noise_button, "grey": self.grey_button, "region": self.region_button,
                               "focus": self.sweep_button}
        for kind, button in self.stage2_buttons.items():
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setAccessibleName(tr("Listen with {sound}").format(sound=button.text().lower()))
            button.clicked.connect(lambda _=False, k=kind: self.set_stage2_sound(k))
            sound.addWidget(button)
        sound.addStretch(1)
        self.sound_hint = self._label(tr("Use any of them: switch any time"), "gtMuted", wrap=False)
        sound.addWidget(self.sound_hint)
        layout.addWidget(self.sound_row)
        self.sound_row.hide()
        layout.addSpacing(4)

        self.cards_grid = QGridLayout()
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(0)
        self.cards = [OptionCard(letter, letter) for letter in LETTERS]
        for card in self.cards:
            card.setAccessibleName(tr("Play {n}").format(n=card.letter))
        for index, card in enumerate(self.cards):
            card.clicked.connect(lambda _=False, i=index: self.select(i))
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(lambda _pos, i=index: self.toggle_rule_out(i))
        self._lay_out_cards(3)
        layout.addLayout(self.cards_grid)
        layout.addSpacing(4)

        answers = QHBoxLayout()
        answers.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        answers.setSpacing(12)
        self.answer_button = AnswerButton()
        self.answer_button.clicked.connect(self.answer_selected)
        answers.addWidget(self.answer_button, 3)
        self.rule_out_button = AnswerButton(tr("Rule out"))
        self.rule_out_button.setToolTip(tr("Cross out the selected card: it sounds clearly worse (X, or "
                                           "right-click a card). It stays playable; again to bring it back."))
        self.rule_out_button.clicked.connect(lambda: self.toggle_rule_out(self.selected))
        answers.addWidget(self.rule_out_button, 1)
        self.same_button = AnswerButton()
        self.same_button.clicked.connect(self.answer_same)
        answers.addWidget(self.same_button, 1)
        layout.addLayout(answers)
        layout.addSpacing(8)
        layout.addSpacing(8)
        self.progress = ProgressLine()
        self.progress.setAccessibleName(tr("Progress"))
        layout.addLayout(self._padded(self.progress))
        layout.addSpacing(14)

        footer = QHBoxLayout()
        footer.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        self.back_button = QPushButton(tr("Back"))
        self.back_button.setToolTip(tr("Take back the last answer"))
        self.back_button.clicked.connect(self.back)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        self.stop_button = QPushButton(tr("✕  Stop the test"))
        self.stop_button.setToolTip(tr("Leave the test; what's been found so far is kept"))
        self.stop_button.clicked.connect(self.leave_for_manual)
        footer.addWidget(self.stop_button)
        layout.addLayout(footer)
        layout.addSpacing(10)
        self.keys_hint = self._label(tr("Keys: 1–8 choose · Enter confirms the selected card · X rule out · P play/pause · S same · "
                                        "N pink · G grey · F focused · W sweep · Space play / stop"),
                                     "gtMuted")
        self.keys_hint.setAlignment(Qt.AlignCenter)
        layout.addLayout(self._padded(self.keys_hint))

        for index in range(len(LETTERS)):
            self._shortcut(page, (Qt.Key_1 + index,), lambda i=index: self.select(i))
        self._shortcut(page, (Qt.Key_X,), lambda: self.toggle_rule_out(self.selected))
        self._shortcut(page, (Qt.Key_P,), self.toggle_play)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), self.answer_selected)  # always the selected card
        self._shortcut(page, (Qt.Key_S,), self.answer_same)
        self._shortcut(page, (Qt.Key_N,), lambda: self.set_stage2_sound("pink"))
        self._shortcut(page, (Qt.Key_G,), lambda: self.set_stage2_sound("grey"))
        self._shortcut(page, (Qt.Key_F,), lambda: self.set_stage2_sound("region"))
        self._shortcut(page, (Qt.Key_W,), lambda: self.set_stage2_sound("focus"))
        self._shortcut(page, (Qt.Key_Backspace,), self.back)
        layout.addStretch(1)
        return page

    def _build_unsure(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.unsure_title = self._label("", "gtTitle")
        self._header(layout, self.unsure_title)
        self.unsure_text = self._label(tr(
            "Some answers pointed to different places, so the result for this ear may be off. That happens "
            "when sounds are close, or after a slip of the mouse. You can do this ear again, carry on from "
            "the best guess (the next part checks it with noise), or adjust it by hand."))
        layout.addLayout(self._padded(self.unsure_text))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.redo_button = QPushButton(tr("Do this ear again"))
        self.redo_button.setObjectName("primary")
        self.redo_button.clicked.connect(self.redo_ear)
        row.addWidget(self.redo_button)
        self.use_anyway_button = QPushButton(tr("Use it anyway"))
        self.use_anyway_button.setToolTip(tr("Carry on from the best guess; the next part checks it"))
        self.use_anyway_button.clicked.connect(self.use_estimate)
        row.addWidget(self.use_anyway_button)
        self.unsure_stop = QPushButton(tr("Stop the test"))
        self.unsure_stop.clicked.connect(self.leave_for_manual)
        row.addWidget(self.unsure_stop)
        self.unsure_back = QPushButton(tr("Back"))
        self.unsure_back.clicked.connect(self.back)
        row.addWidget(self.unsure_back)
        row.addStretch(1)
        layout.addLayout(row)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.redo_ear))
        layout.addStretch(1)
        return page

    def _build_done(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.done_title = self._label("", "gtTitle")
        self._header(layout, self.done_title)
        self.result_label = self._label("", "gtResult")
        font = self.result_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        self.result_label.setFont(font)
        layout.addLayout(self._padded(self.result_label))
        self.result_note = self._label("", "gtMuted")
        layout.addLayout(self._padded(self.result_note))
        layout.addLayout(self._padded(self._done_progress()))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.next_button = QPushButton()
        self.next_button.setObjectName("primary")
        self.next_button.clicked.connect(self.next_ear)
        row.addWidget(self.next_button)
        self.done_back = QPushButton(tr("Back"))
        self.done_back.clicked.connect(self.back)
        row.addWidget(self.done_back)
        row.addStretch(1)
        layout.addLayout(row)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.next_ear))
        layout.addStretch(1)
        return page

    def _build_upper_offer(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.upper_title = self._label(tr("Your resonance is found"), "gtTitle")
        self._header(layout, self.upper_title, self._label(tr("Optional"), "gtMuted", wrap=False))
        self.upper_summary = self._label("", "gtResult")
        layout.addLayout(self._padded(self.upper_summary))
        self.upper_text = self._label(tr(
            "Most IEMs have a second, higher peak, about twice your resonance's frequency, that can sound "
            "sharp or hissy. Your calibration already predicts where it is; this fine-tunes it for each ear. "
            "First: can you hear a high sound there clearly? If not, that ear stays as calibrated. Then: "
            "which of three EQs sounds smoothest. About 1–2 minutes."))
        layout.addLayout(self._padded(self.upper_text))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.upper_button = QPushButton(tr("Fine-tune my upper treble"))
        self.upper_button.setObjectName("primary")
        self.upper_button.clicked.connect(self.start_upper)
        row.addWidget(self.upper_button)
        self.skip_upper_button = QPushButton(tr("Skip, I'm done"))
        self.skip_upper_button.clicked.connect(self.skip_upper)
        row.addWidget(self.skip_upper_button)
        self.upper_back = QPushButton(tr("Back"))
        self.upper_back.clicked.connect(self.back)
        row.addWidget(self.upper_back)
        row.addStretch(1)
        layout.addLayout(row)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.start_upper))
        layout.addStretch(1)
        return page

    def _done_progress(self) -> ProgressLine:
        self.done_progress = ProgressLine()
        self.done_progress.setAccessibleName(tr("Progress"))
        return self.done_progress

    def _enter(self, default) -> None:
        """Enter clicks a focused button (Back, Stop the test…), else does the page's main action."""
        focused = QApplication.focusWidget()
        if isinstance(focused, QPushButton) and self.isAncestorOf(focused):
            focused.click()
        else:
            default()

    def _shortcut(self, page: QWidget, keys, slot) -> None:
        for key in keys:
            shortcut = QShortcut(QKeySequence(key), page)
            shortcut.setContext(Qt.WindowShortcut)  # only while its page shows; text fields keep their keys
            shortcut.activated.connect(slot)

    # ── theme ──────────────────────────────────────────────────────────────
    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._restyle()
            self._paint_accents()
        super().changeEvent(event)

    def _restyle(self) -> None:
        c = {k: v.name() if hasattr(v, "name") else v for k, v in stage_colors(self.palette()).items()}
        border = stage_colors(self.palette())["border"]
        self.setStyleSheet(
            f"QLabel#gtTitle, QLabel#gtText, QLabel#gtResult {{ color: {c['text']}; }}"
            f"QLabel#gtMuted {{ color: {c['muted']}; }}"
            f"QLabel#gtWarning {{ color: {c['warning']}; }}"
            f"QFrame#gtDivider {{ background: rgba({border.red()},{border.green()},{border.blue()},"
            f"{border.alpha()}); border: none; }}")

    def set_other_eq_text(self, text: str, warning: bool, can_pause: bool = False) -> None:
        """The same "turn off other EQs" warning Find your resonance shows; ``can_pause``: our own EQ is on
        system-wide, so the intro offers to pause it right there."""
        self._other_eq = (text, warning)
        self.intro_pause.setVisible(can_pause)
        self.intro_other_eq.setText(text)
        self.intro_other_eq.setObjectName("gtWarning" if warning else "gtMuted")
        self.intro_other_eq.style().unpolish(self.intro_other_eq)
        self.intro_other_eq.style().polish(self.intro_other_eq)

    # ── starting and leaving ───────────────────────────────────────────────
    def start(self, sides, default_hz=None, rng: random.Random | None = None) -> None:
        """Show the intro for a new test over ``sides``."""
        self.rng = rng or random.Random()
        self._sides, self._defaults = tuple(sides), dict(default_hz or {})
        self.test = self._new_test({})
        self._prepare_tables()
        self._round_index = 0
        self._saved_eq_enabled = None
        self.pages.setCurrentWidget(self.intro_page)
        self.start_button.setFocus()

    def _new_test(self, tables: dict) -> GuidedTest:
        return GuidedTest(self._sides, dict(self._defaults), self.rng, offer_upper=self.upper_prediction is not None,
                          tables=tables)

    def _prepare_tables(self) -> None:
        """Build the scorecard's tables on a worker; until then (or without them) the smoothness check."""
        self._table_generation += 1
        self._begin_when_ready = False
        self._preparing = self.tables is not None
        self._sync_intro()
        if not self._preparing:
            return
        generation = self._table_generation
        build = self.tables()

        def work():
            return generation, build()

        job = Worker(work)
        job.finished_ok.connect(self._tables_ready)
        job.failed.connect(self._tables_failed)
        job.finished.connect(lambda: _JOBS.discard(job))
        _JOBS.add(job)
        job.start()

    def _tables_ready(self, result) -> None:
        generation, tables = result
        if generation != self._table_generation or self.test is None:
            return
        self._preparing = False
        if self.pages.currentWidget() is self.intro_page or self._begin_when_ready:
            self.test = self._new_test(dict(tables))
        self._sync_intro()
        if self._begin_when_ready:
            self.begin()

    def _tables_failed(self, _message: str) -> None:
        self._preparing = False  # the smoothness check alone, then
        self._sync_intro()
        if self._begin_when_ready:
            self.begin()

    def _sync_intro(self) -> None:
        if self.tables is not None:
            self.intro_steps.setText("•  " + tr(
                "For each ear: first eight versions of the same noise, through different EQs. Listen to them "
                "all, compare any two as often as you like, cross out the ones that clearly sound worse, and "
                "pick the smoothest. Then a few rounds of just two: which is smoother? About 2 minutes per ear."))
        else:
            self.intro_steps.setText("•  " + tr(
                "For each ear you'll hear three versions of the same noise, through slightly different EQs. "
                "Pick the smoothest each time; they get closer as you go. It takes about 2 minutes in all."))
        waiting = self._preparing and self._begin_when_ready
        self.start_button.setText(tr("Getting ready…") if waiting else tr("Start the test"))
        self.start_button.setEnabled(not waiting)

    def begin(self) -> None:
        """Leave the intro: the first round."""
        if self.test is None:
            return
        if self._preparing:
            self._begin_when_ready = True
            self._sync_intro()
            return
        self._begin_when_ready = False
        self._sync_intro()
        self._saved_eq_enabled = self.audio.eq_enabled
        self.pages.setCurrentWidget(self.test_page)  # first: the round's buttons depend on the page
        self._new_round(keep_playing=False)
        self.transport.setFocus()

    def stop(self) -> None:
        """Silence this view's sound and put the EQ preview back as it was."""
        self._frame.stop()
        self._eq_generation += 1
        if self._sound is not None:
            self._sound = None
            self.audio.stop_all()
        self._set_card_gain(0.0)
        if self._saved_eq_enabled is not None:
            self.audio.set_eq_enabled(self._saved_eq_enabled)
            self._saved_eq_enabled = None
        self._sync_cards()

    def found(self) -> dict[str, dict[str, float]]:
        """What's been found so far, per target and side: finished ears, Stage 2's current guess, or
        the louder test's estimate once it's narrow enough."""
        if self.test is None:
            return {PRIMARY: {}, UPPER: {}}
        found = {}
        for target in (PRIMARY, UPPER):
            guesses = {side: self.test.best_guess(side, target) for side in self.test.sides}
            found[target] = {side: hz for side, hz in guesses.items() if hz is not None}
        return found

    def leave_for_manual(self) -> None:
        found = self.found()
        self.stop()
        self.adjustByHand.emit(found)

    # ── rounds ─────────────────────────────────────────────────────────────
    @property
    def playing(self) -> bool:
        return self._sound is not None

    def _new_round(self, keep_playing: bool) -> None:
        test = self.test
        self._round = test.options()
        count = len(self._round.options)
        self.heard, self._heard_for = set(), {}
        self._since = None
        self._applied = None
        self._eqs = {}
        self._offsets = {}
        self._play_when_ready = False
        self.ruled_out = set()
        self._lay_out_cards(count)
        for card in self.cards:
            card.set_heard(False)
            card.set_ruled_out(False)
            card.set_selected(False, animate=False)
            card.clear_shape()
        self.selected = None
        self._paint_accents()
        louder = test.kind == LOUDER
        upper = test.target == UPPER
        self.title.setText(_ear_title(test.side, test.round_number))
        if upper:  # the upper treble has no louder test (see guided_test)
            self.stage_label.setText(tr("Upper treble: overview") if test.overview
                                     else tr("Upper treble: smoothness check"))
        elif test.overview:
            self.stage_label.setText(tr("Smoothness check: overview"))
        else:
            self.stage_label.setText(tr("Louder test") if louder else tr("Smoothness check"))
        self.rule_out_button.setVisible(test.overview)
        if upper and test.overview:
            self.instructions.setText(tr(
                "Eight EQs that differ only in the highest treble, from an upper peak set low to one set high. "
                "Compare them as often as you like and cross out the ones that clearly sound worse (X or "
                "right-click). With the sweep: pick the one that fades most evenly as it rises (a high sweep "
                "fading is just your hearing). With noise: no sharp or hissy edge. If they all sound the same, "
                "answer Can't tell them apart: this ear's upper treble then stays as calibrated."))
        elif test.overview:
            self.instructions.setText(tr(
                "Eight EQs, from a resonance set low to one set high. Listen to them all and compare any two as "
                "often as you like. Cross out the ones that clearly sound worse (X or right-click; they stay "
                "playable), then pick the smoothest of the rest: no harsh, hissy or dull spot in the treble."))
        elif test.kind == PICK:
            self.instructions.setText(tr("Two EQs. Which sounds smoother? With noise: no harsh, hissy or dull "
                                         "spot in the treble. With the sweep: it stays even, with no swell or "
                                         "dip as it passes. Switch as often as you like."))
        elif louder:
            self.instructions.setText(tr("Two sounds in your {ear}. Which one is louder? Click a card (or "
                                         "press 1 or 2) to switch between them as often as you like.").format(
                ear=_ear(test.side).lower()))
        elif upper:
            self.instructions.setText(tr("Three EQs that differ only in the highest treble. Which sounds "
                                         "smoothest? With the sweep: it fades evenly as it rises, with no swell "
                                         "or dip as it passes (a high sweep fading is just your hearing). With "
                                         "noise: no sharp or hissy edge."))
        else:
            self.instructions.setText(tr("Three EQs. Which sounds smoothest? With noise: no harsh, hissy or dull "
                                         "spot in the treble. With the sweep: it stays even, with no swell or dip "
                                         "as it passes. Switch as often as you like."))
        self.sound_row.setVisible(not louder)
        self._sync_sound_buttons()
        if louder:
            self.same_button.setText(tr("About the same"))
            self._enter_louder_stage()
        else:
            self.same_button.setText(tr("Can't tell them apart"))
            self._enter_noise_stage()
        self.progress.set_value(test.progress())
        self.back_button.setEnabled(self._can_undo())
        if keep_playing and self._sound is not None:
            self.select(0)
        elif self._sound is not None:
            self._pause()
        self._sync_answers()
        self._sync_transport()

    def _lay_out_cards(self, count: int) -> None:
        """Show ``count`` cards, up to four to a row."""
        for card in self.cards:
            self.cards_grid.removeWidget(card)
            card.hide()
        columns = min(count, CARDS_PER_ROW)
        for index in range(count):
            self.cards_grid.addWidget(self.cards[index], index // CARDS_PER_ROW, index % CARDS_PER_ROW)
            self.cards[index].show()
        for column in range(CARDS_PER_ROW):
            self.cards_grid.setColumnStretch(column, 1 if column < columns else 0)

    def toggle_rule_out(self, index: int | None) -> None:
        """Overview only: cross a card out (it sounds clearly worse), or bring it back."""
        if (self.test is None or not self.test.overview or self._round is None or index is None
                or not 0 <= index < len(self._round.options) or self.pages.currentWidget() is not self.test_page):
            return
        self.ruled_out ^= {index}
        self.cards[index].set_ruled_out(index in self.ruled_out)
        self._sync_cards()
        self._sync_answers()

    @property
    def _banded(self) -> bool:
        """This round plays noise bands with the EQ off (the louder test)."""
        return self.test is not None and self.test.kind == LOUDER

    def _can_undo(self) -> bool:
        test = self.test
        return test is not None and (len(test.stages) > 1 or bool(test.stage.search.answers))

    def _paint_accents(self) -> None:
        if self._round is None:
            return
        count = len(self._round.options)
        colors = accent_colors(self.palette(), count)
        for i, (card, color) in enumerate(zip(self.cards, colors)):
            card.set_accent(color)
            card.set_position(i / (count - 1) if count > 1 else None)
        self._sync_answers()

    def _enter_louder_stage(self) -> None:
        if self._saved_eq_enabled is None:
            self._saved_eq_enabled = self.audio.eq_enabled
        if self.audio.eq_enabled:
            self.audio.set_eq_enabled(False)  # the louder test is judged without the EQ
        if self._sound in STAGE2_SOUNDS:  # Stage 2 → (back to) Stage 1: switch the sound type
            self._sound = None
            self.audio.stop_all()

    def _enter_noise_stage(self) -> None:
        if self._sound == "band":
            self._sound = None
            self.audio.stop_all()
        elif self._sound in CENTRED_SOUNDS:
            self._start_stage2_sound()  # the sweep or focused noise moves to this round's options
        self._design_eqs()

    def _sweep_center(self) -> float:
        """The sweep and focused noise are centred on the round's middle option and span all three."""
        return sorted(self._round.options)[len(self._round.options) // 2]

    def _start_stage2_sound(self) -> bool:
        """(Re)start the smoothness check's sound — noise or sweep — through the applied EQ."""
        sound = self.stage2_sound
        self._sound = sound
        self._starting = True  # routing notifies listeners before the new sound is playing
        try:
            if sound == "focus":
                self.audio.play_focus_sweep(self._sweep_center(), self.test.side)
            elif sound == "region":
                self.audio.play_region(self._sweep_center(), self.test.side)
            else:
                self.audio.play_noise(sound, self.test.side)
        finally:
            self._starting = False
        if self.audio.playing != sound:  # the quiet-start warning said no
            self._sound = None
            return False
        return True

    def set_stage2_sound(self, kind: str) -> None:
        """Noise or sweep for the smoothness check; a playing sound switches at once (same EQ)."""
        if kind not in STAGE2_SOUNDS or self.pages.currentWidget() is not self.test_page:
            return
        self.stage2_sound = kind
        self._sync_sound_buttons()
        self._level_match()
        if self._sound in STAGE2_SOUNDS and self._applied is not None:
            self._set_card_gain(self._offsets.get(self._applied, 0.0))
        if self.test is not None and not self._banded and self._sound is not None and self._sound != kind:
            self._account()
            if self._start_stage2_sound():
                self._since = self._clock()
            else:
                self._frame.stop()
            self._sync_cards()
            self._sync_transport()

    def _sync_sound_buttons(self) -> None:
        for kind, button in self.stage2_buttons.items():
            button.setChecked(self.stage2_sound == kind)

    def _design_eqs(self) -> None:
        """Stage 2: the three EQs and their preview filters, on a worker."""
        self._eq_generation += 1
        if self.option_eqs is None:
            return
        generation = self._eq_generation
        build = self.option_eqs(self.test.side, list(self._round.options), self.test.target)

        def work():
            return generation, build()

        job = Worker(work)
        job.finished_ok.connect(self._eqs_ready)
        job.failed.connect(self._eqs_failed)
        job.finished.connect(lambda: _JOBS.discard(job))
        _JOBS.add(job)
        job.start()
        self._sync_transport()

    def _eqs_ready(self, result) -> None:
        generation, built = result
        if generation != self._eq_generation or self.test is None:
            return
        self._eqs = dict(enumerate(built))
        self._level_match()
        if self._sound is not None and self.selected is not None:
            self._apply_option(self.selected)
        elif self._play_when_ready and self.pages.currentWidget() is self.test_page:
            self.play()
        self._sync_transport()

    def _eqs_failed(self, message: str) -> None:
        self.instructions.setText(tr("The sounds couldn't be prepared: {reason}").format(reason=message))

    @property
    def ready(self) -> bool:
        """The current round's sounds can play (Stage 2 needs its EQs)."""
        if self.test is None or self._round is None:
            return False
        return self._banded or len(self._eqs) == len(self._round.options)

    # ── listening ──────────────────────────────────────────────────────────
    def toggle_play(self) -> None:
        if self.pages.currentWidget() is not self.test_page:
            return
        if self._sound is not None:
            self._pause()
        else:
            self.play()

    def play(self) -> None:
        if self.test is None or self._round is None or not self.ready:
            return
        if self.selected is None:
            self.selected = 0
            self._sync_cards()
        side, index = self.test.side, self.selected
        if self._banded:
            self._set_card_gain(0.0)
            self.audio.switch_band(self._round.options[index], self._round.band, side)
            sound = "band"
        else:
            self._apply_option(index)
            if not self.audio.eq_enabled:
                self.audio.set_eq_enabled(True)
            if not self._start_stage2_sound():
                self._sync_transport()
                return
            sound = self._sound
        if self.audio.playing != sound:  # the quiet-start warning said no
            self._sync_transport()
            return
        self._sound = sound
        self._play_when_ready = False
        self._since = self._clock()
        self._frame.start()
        self._sync_cards()
        self._sync_transport()
        self._sync_answers()  # Play selects the first card: it can be the answer

    def _pause(self) -> None:
        self._account()
        self._sound = None
        self._play_when_ready = False
        self._frame.stop()
        self.audio.stop_all()
        self._sync_cards()
        self._sync_transport()

    def select(self, index: int) -> None:
        """Select an option and hear it: the sound starts, or switches to it at once."""
        if self._round is None or not 0 <= index < len(self._round.options):
            return
        if self.pages.currentWidget() is not self.test_page:
            return
        self._account()
        self.selected = index
        if self._sound is None:
            self._play_when_ready = True  # Stage 2's EQs may still be on their way
            self.play()
            self._sync_answers()
            return
        if self._sound == "band":
            self.audio.switch_band(self._round.options[index], self._round.band, self.test.side)
        elif self._sound in STAGE2_SOUNDS:
            self._apply_option(index)  # the noise or sweep keeps going; only the EQ changes
        self._since = self._clock() if self._sound is not None else None
        self._sync_cards()
        self._sync_answers()

    def _apply_option(self, index: int) -> None:
        built = self._eqs.get(index)
        if built is None:
            self._applied = None
            return
        eq, designed = built
        self.audio.offer_firs(eq, designed)
        self.audio.set_eq(eq)
        self._set_card_gain(self._offsets.get(index, 0.0))
        self._applied = index

    def _level_match(self) -> None:
        """Every card as loud as the quietest, for the sound being played (see ``loudness``)."""
        if self.test is None or self._round is None or len(self._eqs) != len(self._round.options):
            self._offsets = {}
            return
        curves = [card_curve(self._eqs[i][0], self.test.side) for i in range(len(self._eqs))]
        self._offsets = dict(enumerate(level_offsets(curves, self.stage2_sound, self._sweep_center())))

    def _set_card_gain(self, db: float) -> None:
        gain = 10 ** (db / 20)
        self.audio.engine.set_lr_gains(gain, gain)

    def _audible(self) -> bool:
        """The selected option is what's playing right now."""
        if self._sound is None or self.selected is None:
            return False
        return self._sound == "band" or self._applied == self.selected

    def _account(self) -> None:
        """Add the time the selected option has been playing to its listening time."""
        now = self._clock()
        if self._since is not None and self._audible():
            heard = self._heard_for.get(self.selected, 0.0) + (now - self._since)
            self._heard_for[self.selected] = heard
            if heard >= HEARD_SECONDS and self.selected not in self.heard:
                self.heard.add(self.selected)
                self.cards[self.selected].set_heard(True)
                self._sync_answers()
        self._since = now if self._sound is not None else None

    def _tick(self) -> None:
        self._account()
        samples = self.audio.engine.pop_recent_samples(LEVEL_SAMPLES)
        self._level += LEVEL_SMOOTHING * (self.measure_level(samples) - self._level)
        if self.selected is not None and self._audible():
            self.cards[self.selected].set_level(self._level, self._clock() - self._t0)

    def measure_level(self, samples) -> float:
        return level_from_samples(samples, self.audio.volume)

    def _audio_changed(self) -> None:
        """Something else stopped the sound (Space, Stop all sound, another player)."""
        if self._starting:
            return
        if self._sound is not None and self.audio.playing != self._sound:
            self._account()
            self._sound = None
            self._frame.stop()
            self._sync_cards()
            self._sync_transport()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Space:  # the window's shortcut usually gets there first
            self.audio.stop_all()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── answering ──────────────────────────────────────────────────────────
    @property
    def can_answer(self) -> bool:
        return self._round is not None and self.pages.currentWidget() is self.test_page

    def answer_selected(self) -> None:
        if self.can_answer and self.selected is not None and self.selected not in self.ruled_out:
            self._answer(self.selected)

    def answer_same(self) -> None:
        if self.can_answer:
            self._answer(None)

    def _answer(self, choice: int | None) -> None:
        self._account()
        target = self.test.target
        results = self.test.upper_results if target == UPPER else self.test.results
        before = set(results)
        if self.test.kind == PICK:
            self.test.answer(choice, sorted(self.ruled_out))
        else:
            self.test.answer(choice)
        finished = [side for side in results if side not in before]
        self._after_change(forward=True, finished=(finished[0], target) if finished else None)

    def back(self) -> None:
        if self.test is None or not self.test.undo():
            return
        self._after_change(forward=False)

    def _after_change(self, forward: bool, finished: tuple[str, str] | None = None) -> None:
        test = self.test
        self._round_index += 1 if forward else -1
        if test.status == "unsure":
            self._pause_if_playing()
            self.unsure_title.setText(tr("{ear}: let's check that").format(ear=_ear(test.side)))
            self.pages.setCurrentWidget(self.unsure_page)
            self.redo_button.setFocus()
            return
        if finished is not None:
            side, target = finished
            self._pause_if_playing()
            if target == UPPER:
                hz = test.upper_results[side]
                self.upperFound.emit(side, hz)
            else:
                hz = test.results[side]
                self.resultFound.emit(side, hz)
            self._show_done(side, hz, target)
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=True)

    def _pause_if_playing(self) -> None:
        if self._sound is not None:
            self._pause()

    def _show_done(self, side: str, hz: float | None, target: str = PRIMARY) -> None:
        test = self.test
        stages = [s for s in test.stages if s.side == side and s.target == target]
        louder = next((s.search for s in reversed(stages) if s.kind == LOUDER), None)
        smooth = next((s.search for s in reversed(stages) if s.kind != LOUDER), None)
        louder_rounds = len(louder.answers) if louder else 0
        smooth_rounds = len(smooth.answers) if smooth else 0
        self.done_title.setText(tr("{ear} done").format(ear=_ear(side)))
        if target == UPPER and hz is None:
            self.result_label.setText(tr("{ear}: upper treble as calibrated").format(ear=_ear(side)))
            note = tr("The eight sounded alike, so this ear's upper treble stays where your calibration "
                      "predicts it. That's common: many people hear little up there.")
        elif target == UPPER:
            self.result_label.setText(tr("{ear} upper treble: {hz} Hz").format(ear=side_name(side),
                                                                                hz=f"{int(round(hz)):,}"))
            note = tr("Found with an overview of eight EQs around where your calibration predicts it, then "
                      "{n} rounds of three. You can fine-tune it by hand later.").format(n=smooth_rounds)
        else:
            self.result_label.setText(tr("{ear} resonance: {hz} Hz").format(ear=side_name(side),
                                                                             hz=f"{int(round(hz)):,}"))
            pick = next((s.search for s in stages if s.kind == PICK), None)
            if pick is not None:
                note = tr("Found with {n} rounds: an overview of eight EQs, then pairs. You can fine-tune it "
                          "by hand later.").format(n=len(pick.answers))
            elif louder is None:
                start = (tr("the other ear's result") if len(stages) == 1 and test.sides.index(side) > 0
                         else tr("your calibration's measurements"))
                note = tr("Found with {n} rounds of the smoothness check, starting from {start}. You can "
                          "fine-tune it by hand later.").format(n=smooth_rounds, start=start)
            elif louder.done and louder.result().fell_back:
                note = tr("The sounds were alike, so this comes from the smoothness check alone "
                          "({n} rounds of noise through your EQ).").format(n=smooth_rounds)
            else:
                note = tr("Found with {louder} rounds of the louder test, then {smooth} rounds of the smoothness "
                          "check. You can fine-tune it by hand later.").format(louder=louder_rounds,
                                                                                smooth=smooth_rounds)
        self.result_note.setText(note)
        self.done_progress.set_value(test.progress())
        if test.status == "finished":
            self.next_button.setText(tr("Done"))
        elif test.status == "upper_offer":
            self.next_button.setText(tr("Next"))
        else:
            self.next_button.setText(tr("Next: {ear}").format(ear=_ear(test.side)))
        self.pages.setCurrentWidget(self.done_page)
        self.next_button.setFocus()

    def next_ear(self) -> None:
        if self.test is None:
            return
        if self.test.status == "finished":
            self.finish()
            return
        if self.test.status == "upper_offer":
            self._show_upper_offer()
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self.transport.setFocus()

    def _show_upper_offer(self) -> None:
        lines = [tr("{ear} resonance: {hz} Hz").format(ear=side_name(side), hz=f"{int(round(hz)):,}")
                 for side, hz in self.test.results.items()]
        self.upper_summary.setText("\n".join(lines))
        self.pages.setCurrentWidget(self.upper_page)
        self.upper_button.setFocus()

    def start_upper(self) -> None:
        if self.test is None or self.test.status != "upper_offer":
            return
        predict = self.upper_prediction or (lambda _side, _hz: None)
        self.test.start_upper({side: predict(side, hz) for side, hz in self.test.results.items()})
        if self.test.status == "finished":  # nothing predicted for any ear
            self.finish()
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self.transport.setFocus()

    def skip_upper(self) -> None:
        if self.test is None or self.test.status != "upper_offer":
            return
        self.test.skip_upper()
        self.finish()

    def redo_ear(self) -> None:
        if self.test is None or self.test.status != "unsure":
            return
        self.test.redo_ear()
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self.transport.setFocus()

    def use_estimate(self) -> None:
        """After an unsure louder test: carry on to the smoothness check from the best guess."""
        if self.test is None or self.test.status != "unsure":
            return
        self.test.use_estimate()
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self.transport.setFocus()

    # ── keeping widgets in step ────────────────────────────────────────────
    def _sync_cards(self) -> None:
        for index, card in enumerate(self.cards):
            card.set_selected(index == self.selected)
            card.set_playing(self._sound is not None and index == self.selected)
            state = []
            if index == self.selected:
                state.append(tr("selected"))
            if index in self.heard:
                state.append(tr("heard"))
            if index in self.ruled_out:
                state.append(tr("ruled out"))
            card.setAccessibleDescription(", ".join(state))

    def _sync_transport(self) -> None:
        playing = self._sound is not None
        self.transport.set_playing(playing, tr("Pause") if playing else tr("Play"))
        self.transport.setEnabled(playing or self.ready)
        if not self.ready and self.test is not None and not self._banded:
            self.transport.setToolTip(tr("Getting the sounds ready…"))
        else:
            self.transport.setToolTip(tr("Play or pause (P)"))

    def _sync_answers(self) -> None:
        if self._round is None:
            return
        letter = LETTERS[self.selected] if self.selected is not None else None
        louder = self.test.kind == LOUDER
        if letter is None:
            self.answer_button.setText(tr("Louder") if louder else tr("Smoothest"))
        elif louder:
            self.answer_button.setText(tr("Louder: {n}").format(n=letter))
        else:
            self.answer_button.setText(tr("Smoothest: {n}").format(n=letter))
        self.answer_button.set_accent(self.cards[self.selected].accent if self.selected is not None else None)
        ok = self.can_answer
        self.answer_button.setEnabled(ok and self.selected is not None and self.selected not in self.ruled_out)
        self.same_button.setEnabled(ok)
        self.rule_out_button.setEnabled(self.selected is not None)
        self.rule_out_button.setText(tr("Bring back") if self.selected in self.ruled_out else tr("Rule out"))

    def finish(self) -> None:
        self.stop()
        self.finished.emit()

