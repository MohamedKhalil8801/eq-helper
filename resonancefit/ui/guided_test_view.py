"""The guided "Find my resonance" test, shown in place of step ①'s contents.

Drives a :class:`~resonancefit.guided_test.GuidedTest`. One Play/Pause bar
plays the selected option continuously; clicking a card switches the sound
at once (tones crossfade in the engine, EQs crossfade in ``StereoFIR``).
An answer unlocks once every option has been heard for a moment. Option
order is shuffled each round and only letters are shown; the cards'
animation follows the overall level only, so nothing gives the answer away.

Stage 1 (Louder test) plays tones with the EQ preview off; Stage 2
(Smoothness check) plays pink noise through three EQs designed on a worker
thread. The EQ preview is put back as it was when the test closes.
"""

from __future__ import annotations

import atexit
import random
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..guided_test import LOUDER, GuidedTest
from .common import Worker, side_name, tr
from .guided_widgets import (
    CARD_MARGIN, AnswerButton, OptionCard, ProgressLine, StageBackground, TransportBar, accent_colors,
    is_dark, level_from_samples, stage_colors,
)

HEARD_SECONDS = 0.5   # an option counts as heard after this long selected while playing
FRAME_MS = 33         # ~30 fps while sound plays
LEVEL_SAMPLES = 1024
LEVEL_SMOOTHING = 0.35

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

    ``option_eqs(side, hz_list)`` is called on the GUI thread and returns a
    function that, run on a worker thread, builds Stage 2's
    ``[(channel_eq, designed_firs), …]`` (one per option, in order). Signals: ``resultFound(side, hz)``
    when an ear is done, ``adjustByHand(found)`` to leave for the manual
    controls (``found``: side → Hz found so far) and ``finished()``.
    """

    resultFound = Signal(str, float)
    adjustByHand = Signal(object)
    finished = Signal()

    def __init__(self, audio, option_eqs=None, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.option_eqs = option_eqs
        self.test: GuidedTest | None = None
        self.rng = random.Random()
        self._clock = time.monotonic
        self._t0 = self._clock()
        self._hue = 0.0
        self._round_index = 0
        self._round = None
        self.selected: int | None = None
        self.heard: set[int] = set()
        self._heard_for: dict[int, float] = {}
        self._since: float | None = None
        self._level = 0.0
        self._sound = None            # "tone" / "pink" while this view's sound plays
        self._eqs: dict[int, tuple] = {}
        self._eq_generation = 0
        self._applied: int | None = None   # option whose EQ is in the preview
        self._saved_eq_enabled: bool | None = None
        self._other_eq = ("", False)

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
        for page in (self.intro_page, self.test_page, self.unsure_page, self.done_page):
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
            tr("Turn your volume low: some tones can be piercing. Press Space at any time to stop the sound."),
            tr("For each ear you'll hear two or three sounds at a time. First pick the louder tone, then "
               "the smoothest noise. It takes about 2 minutes in all."),
        ]
        for text in tips:
            layout.addLayout(self._padded(self._label("•  " + text)))
        self.intro_other_eq = self._label()
        layout.addLayout(self._padded(self.intro_other_eq))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.start_button = QPushButton(tr("Start the test"))
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.begin)
        row.addWidget(self.start_button)
        self.intro_manual = QPushButton(tr("Adjust by hand"))
        self.intro_manual.clicked.connect(self.leave_for_manual)
        row.addWidget(self.intro_manual)
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
        layout.addSpacing(4)

        self.cards_row = QHBoxLayout()
        self.cards_row.setContentsMargins(0, 0, 0, 0)
        self.cards_row.setSpacing(0)
        self.cards = [OptionCard(letter, tr("Play {letter}").format(letter=letter)) for letter in "ABC"]
        for index, card in enumerate(self.cards):
            card.clicked.connect(lambda _=False, i=index: self.select(i))
            self.cards_row.addWidget(card, 1)
        layout.addLayout(self.cards_row)
        layout.addSpacing(4)

        answers = QHBoxLayout()
        answers.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        answers.setSpacing(12)
        self.answer_button = AnswerButton()
        self.answer_button.clicked.connect(self.answer_selected)
        answers.addWidget(self.answer_button, 3)
        self.same_button = AnswerButton()
        self.same_button.clicked.connect(self.answer_same)
        answers.addWidget(self.same_button, 1)
        layout.addLayout(answers)
        layout.addSpacing(8)
        self.listen_hint = self._label(tr("Listen to each one first"), "gtMuted", wrap=False)
        self.listen_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.listen_hint)
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
        self.manual_button = QPushButton(tr("Adjust by hand"))
        self.manual_button.setToolTip(tr("Leave the test and keep what's been found so far"))
        self.manual_button.clicked.connect(self.leave_for_manual)
        footer.addWidget(self.manual_button)
        layout.addLayout(footer)
        layout.addSpacing(10)
        self.keys_hint = self._label(tr("Keys: 1 2 3 choose · P play/pause · Enter answer · S same · Space stop"),
                                     "gtMuted")
        self.keys_hint.setAlignment(Qt.AlignCenter)
        layout.addLayout(self._padded(self.keys_hint))

        for index, key in enumerate((Qt.Key_1, Qt.Key_2, Qt.Key_3)):
            self._shortcut(page, (key,), lambda i=index: self.select(i))
        self._shortcut(page, (Qt.Key_P,), self.toggle_play)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.answer_selected))
        self._shortcut(page, (Qt.Key_S,), self.answer_same)
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
            "One answer near the end didn't match an earlier one: the same two tones were played again, the "
            "other way round, and the other one sounded louder this time. That happens when tones are close "
            "or the volume changed. The result for this ear may be off."))
        layout.addLayout(self._padded(self.unsure_text))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.redo_button = QPushButton(tr("Do this ear again"))
        self.redo_button.setObjectName("primary")
        self.redo_button.clicked.connect(self.redo_ear)
        row.addWidget(self.redo_button)
        self.unsure_manual = QPushButton(tr("Adjust by hand"))
        self.unsure_manual.clicked.connect(self.leave_for_manual)
        row.addWidget(self.unsure_manual)
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

    def _done_progress(self) -> ProgressLine:
        self.done_progress = ProgressLine()
        self.done_progress.setAccessibleName(tr("Progress"))
        return self.done_progress

    def _enter(self, default) -> None:
        """Enter clicks a focused button (Back, Adjust by hand…), else does the page's main action."""
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
            f"QLabel#gtWarning {{ color: {'#FFB366' if is_dark(self.palette()) else '#8A3E00'}; }}"
            f"QFrame#gtDivider {{ background: rgba({border.red()},{border.green()},{border.blue()},"
            f"{border.alpha()}); border: none; }}")

    def set_other_eq_text(self, text: str, warning: bool) -> None:
        """The same "turn off other EQs" warning step ① shows."""
        self._other_eq = (text, warning)
        self.intro_other_eq.setText(text)
        self.intro_other_eq.setObjectName("gtWarning" if warning else "gtMuted")
        self.intro_other_eq.style().unpolish(self.intro_other_eq)
        self.intro_other_eq.style().polish(self.intro_other_eq)

    # ── starting and leaving ───────────────────────────────────────────────
    def start(self, sides, default_hz=None, rng: random.Random | None = None) -> None:
        """Show the intro for a new test over ``sides``."""
        self.rng = rng or random.Random()
        self.test = GuidedTest(tuple(sides), dict(default_hz or {}), self.rng)
        self._hue = self.rng.uniform(0.0, 360.0)
        self._round_index = 0
        self._saved_eq_enabled = None
        self.pages.setCurrentWidget(self.intro_page)
        self.start_button.setFocus()

    def begin(self) -> None:
        """Leave the intro: the first round."""
        if self.test is None:
            return
        self._saved_eq_enabled = self.audio.eq_enabled
        self._new_round(keep_playing=False)
        self.pages.setCurrentWidget(self.test_page)
        self.transport.setFocus()

    def stop(self) -> None:
        """Silence this view's sound and put the EQ preview back as it was."""
        self._frame.stop()
        self._eq_generation += 1
        if self._sound is not None:
            self._sound = None
            self.audio.stop_all()
        if self._saved_eq_enabled is not None:
            self.audio.set_eq_enabled(self._saved_eq_enabled)
            self._saved_eq_enabled = None
        self._sync_cards()

    def found(self) -> dict[str, float]:
        """What's been found so far, per side (finished ears, or Stage 2's current guess)."""
        if self.test is None:
            return {}
        guesses = {side: self.test.best_guess(side) for side in self.test.sides}
        return {side: hz for side, hz in guesses.items() if hz is not None}

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
        for index, card in enumerate(self.cards):
            card.setVisible(index < count)
            card.set_heard(False)
            card.set_selected(False, animate=False)
            card.clear_shape()
        self.selected = None
        self._paint_accents()
        louder = test.kind == LOUDER
        self.title.setText(_ear_title(test.side, test.round_number))
        self.stage_label.setText(tr("Louder test") if louder else tr("Smoothness check"))
        if louder:
            self.instructions.setText(tr("Two tones in your {ear}. Which one sounds louder? Click a card (or "
                                         "press 1 or 2) to switch between them as often as you like.").format(
                ear=_ear(test.side).lower()))
            self.same_button.setText(tr("About the same"))
            self._enter_tone_stage()
        else:
            self.instructions.setText(tr("Noise through three EQs. Which sounds smoothest, with no harsh, hissy "
                                         "or dull spot in the treble? Switch as often as you like."))
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

    def _can_undo(self) -> bool:
        test = self.test
        return test is not None and (len(test.stages) > 1 or bool(test.stage.search.answers)
                                     or getattr(test.stage.search, "check_answer", None) is not None)

    def _paint_accents(self) -> None:
        if self._round is None:
            return
        colors = accent_colors(self._hue, self._round_index, len(self._round.options), is_dark(self.palette()))
        for card, color in zip(self.cards, colors):
            card.set_accent(color)
        self._sync_answers()

    def _enter_tone_stage(self) -> None:
        if self._saved_eq_enabled is None:
            self._saved_eq_enabled = self.audio.eq_enabled
        if self.audio.eq_enabled:
            self.audio.set_eq_enabled(False)  # tones are judged without the EQ
        if self._sound == "pink":  # Stage 2 → (back to) Stage 1: switch the sound type
            self._sound = None
            self.audio.stop_all()

    def _enter_noise_stage(self) -> None:
        if self._sound == "tone":
            self._sound = None
            self.audio.stop_all()
        self._design_eqs()

    def _design_eqs(self) -> None:
        """Stage 2: the three EQs and their preview filters, on a worker."""
        self._eq_generation += 1
        if self.option_eqs is None:
            return
        generation = self._eq_generation
        build = self.option_eqs(self.test.side, list(self._round.options))

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
        if self._sound is not None and self.selected is not None:
            self._apply_option(self.selected)
        self._sync_transport()

    def _eqs_failed(self, message: str) -> None:
        self.instructions.setText(tr("The sounds couldn't be prepared: {reason}").format(reason=message))

    @property
    def ready(self) -> bool:
        """The current round's sounds can play (Stage 2 needs its EQs)."""
        if self.test is None or self._round is None:
            return False
        return self.test.kind == LOUDER or len(self._eqs) == len(self._round.options)

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
        if self.test.kind == LOUDER:
            self.audio.switch_tone(self._round.options[index], side)
            sound = "tone"
        else:
            self._apply_option(index)
            if not self.audio.eq_enabled:
                self.audio.set_eq_enabled(True)
            self.audio.play_pink(side)
            sound = "pink"
        if self.audio.playing != sound:  # the quiet-start warning said no
            self._sync_transport()
            return
        self._sound = sound
        self._since = self._clock()
        self._frame.start()
        self._sync_cards()
        self._sync_transport()

    def _pause(self) -> None:
        self._account()
        self._sound = None
        self._frame.stop()
        self.audio.stop_all()
        self._sync_cards()
        self._sync_transport()

    def select(self, index: int) -> None:
        """Select an option; while playing, the sound switches to it at once."""
        if self._round is None or not 0 <= index < len(self._round.options):
            return
        if self.pages.currentWidget() is not self.test_page:
            return
        self._account()
        self.selected = index
        if self._sound == "tone":
            self.audio.switch_tone(self._round.options[index], self.test.side)
        elif self._sound == "pink":
            self._apply_option(index)
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
        self._applied = index

    def _audible(self) -> bool:
        """The selected option is what's playing right now."""
        if self._sound is None or self.selected is None:
            return False
        return self._sound == "tone" or self._applied == self.selected

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
        return (self._round is not None and self.pages.currentWidget() is self.test_page
                and len(self.heard) == len(self._round.options))

    def answer_selected(self) -> None:
        if self.can_answer and self.selected is not None:
            self._answer(self.selected)

    def answer_same(self) -> None:
        if self.can_answer:
            self._answer(None)

    def _answer(self, choice: int | None) -> None:
        self._account()
        before = set(self.test.results)
        self.test.answer(choice)
        finished = [side for side in self.test.results if side not in before]
        self._after_change(forward=True, finished_side=finished[0] if finished else None)

    def back(self) -> None:
        if self.test is None or not self.test.undo():
            return
        self._after_change(forward=False)

    def _after_change(self, forward: bool, finished_side: str | None = None) -> None:
        test = self.test
        self._round_index += 1 if forward else -1
        if test.status == "unsure":
            self._pause_if_playing()
            self.unsure_title.setText(tr("{ear}: let's check that").format(ear=_ear(test.side)))
            self.pages.setCurrentWidget(self.unsure_page)
            self.redo_button.setFocus()
            return
        if finished_side is not None:
            self._pause_if_playing()
            hz = test.results[finished_side]
            self.resultFound.emit(finished_side, hz)
            self._show_done(finished_side, hz)
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=True)

    def _pause_if_playing(self) -> None:
        if self._sound is not None:
            self._pause()

    def _show_done(self, side: str, hz: float) -> None:
        test = self.test
        stages = [s for s in test.stages if s.side == side]
        louder = next((s.search for s in stages if s.kind == LOUDER), None)
        smooth = next((s.search for s in stages if s.kind != LOUDER), None)
        louder_rounds = len(louder.answers) if louder else 0
        smooth_rounds = len(smooth.answers) if smooth else 0
        self.done_title.setText(tr("{ear} done").format(ear=_ear(side)))
        self.result_label.setText(tr("{ear} resonance: {hz} Hz").format(
            ear=side_name(side), hz=f"{int(round(hz)):,}"))
        if louder is not None and louder.fell_back:
            note = tr("The tones sounded alike, so this comes from the smoothness check alone "
                      "({n} rounds of noise through your EQ).").format(n=smooth_rounds)
        else:
            note = tr("Found with {louder} rounds of the louder test, then {smooth} rounds of the smoothness "
                      "check. You can fine-tune it by hand later.").format(louder=louder_rounds, smooth=smooth_rounds)
        self.result_note.setText(note)
        self.done_progress.set_value(test.progress())
        if test.status == "finished":
            self.next_button.setText(tr("Done"))
        else:
            self.next_button.setText(tr("Next: {ear}").format(ear=_ear(test.side)))
        self.pages.setCurrentWidget(self.done_page)
        self.next_button.setFocus()

    def next_ear(self) -> None:
        if self.test is None:
            return
        if self.test.status == "finished":
            self.stop()
            self.finished.emit()
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self.transport.setFocus()

    def redo_ear(self) -> None:
        if self.test is None or self.test.status != "unsure":
            return
        self.test.redo_ear()
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
            card.setAccessibleDescription(", ".join(state))

    def _sync_transport(self) -> None:
        playing = self._sound is not None
        self.transport.set_playing(playing, tr("Pause") if playing else tr("Play"))
        self.transport.setEnabled(playing or self.ready)
        if not self.ready and self.test is not None and self.test.kind != LOUDER:
            self.transport.setToolTip(tr("Getting the sounds ready…"))
        else:
            self.transport.setToolTip(tr("Play or pause (P)"))

    def _sync_answers(self) -> None:
        if self._round is None:
            return
        letter = "ABC"[self.selected] if self.selected is not None else None
        louder = self.test.kind == LOUDER
        if letter is None:
            self.answer_button.setText(tr("Louder") if louder else tr("Smoothest"))
        elif louder:
            self.answer_button.setText(tr("Louder: {letter}").format(letter=letter))
        else:
            self.answer_button.setText(tr("Smoothest: {letter}").format(letter=letter))
        self.answer_button.set_accent(self.cards[self.selected].accent if self.selected is not None else None)
        ok = self.can_answer
        self.answer_button.setEnabled(ok and self.selected is not None)
        self.same_button.setEnabled(ok)
        self.listen_hint.setText(tr("Listen to each one first") if not ok else " ")
