"""The guided "Find my resonance" test, shown in place of step ①'s contents.

Drives a :class:`~resonancefit.guided_test.GuidedTest`. One Play/Pause bar
plays the selected option continuously; clicking a card switches the sound
at once (noise bands crossfade in the engine, EQs crossfade in ``StereoFIR``).
An answer unlocks once every option has been heard for a moment. Option
order is shuffled each round and only letters are shown; the cards'
animation follows the overall level only, so nothing gives the answer away.

Stage 1 is the loudness match when the calibration's model is ready (see
:mod:`resonancefit.shape_match`): two noise bands take turns and a slider
sets band B until it sounds as loud as A (which band is the one being
adjusted, and where the slider starts, are random). Without the model it is
the louder test: narrow noise bands (the search picks their width). Both
play with the EQ preview off. Stage 2
(Smoothness check) plays pink noise, grey noise or a slow focused sweep
(the customer switches between them at will) through three EQs designed on a worker
thread. After both primary resonances the upper treble is offered: no
louder test up there (hearing falls off too steeply to judge loudness), but
one question per ear — is a band at the predicted peak heard clearly? — and
then the smoothness check around the calibration's prediction (the sweep
first; EQs that move only the upper resonance). Last,
an optional balance check: the same noise bands in both ears through the
EQ, each centred by the customer; a lean shaped like a misplaced resonance
leads to a short re-check of that ear (the EQ never changes by itself). The
EQ preview is put back as it was when the test closes.
"""

from __future__ import annotations

import atexit
import random
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..balance_check import CENTRE_RANGE_DB, analyse
from ..guided_test import AUDIBLE, LOUDER, PRIMARY, SHAPE, UPPER, GuidedTest
from ..shape_match import BAND as MATCH_BAND
from ..shape_match import MATCH_RANGE_DB
from .common import Worker, side_name, tr
from .guided_widgets import (
    CARD_MARGIN, AnswerButton, CentreControl, OptionCard, ProgressLine, StageBackground, TransportBar, accent_colors,
    is_dark, level_from_samples, stage_colors,
)

HEARD_SECONDS = 0.5   # an option counts as heard after this long selected while playing
FRAME_MS = 33         # ~30 fps while sound plays
LEVEL_SAMPLES = 1024
LEVEL_SMOOTHING = 0.35
STAGE2_SOUNDS = ("pink", "grey", "focus")  # the smoothness check: pink noise, grey noise, or the sweep
MATCH_HEARD_SECONDS = 2.0  # a match can be answered once the pair has played this long
MATCH_START_DB = 6.0       # the slider starts up to this far off (randomly), so it never starts matched
MATCH_STEPS_PER_DB = 4     # slider resolution: 0.25 dB
MATCH_MODES = ("alternate", "a", "b")

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

    ``shape_models()`` (optional) is called on the GUI thread when a test
    starts and returns a function that, on a worker thread, returns
    ``{side: ShapeModel}``; with it, each ear starts with the loudness match.

    ``balance()`` (optional) is called on the GUI thread when the balance check
    starts and returns ``(bands, work)``: the bands to centre and a function
    that, on a worker thread, returns ``(channel_eq, designed_firs, model)`` for
    the current tuning (``model``: a :class:`~resonancefit.balance_check.BalanceModel`).
    """

    resultFound = Signal(str, float)
    upperFound = Signal(str, object)
    adjustByHand = Signal(object)
    finished = Signal()

    def __init__(self, audio, option_eqs=None, balance=None, upper_prediction=None, shape_models=None,
                 parent=None):
        super().__init__(parent)
        self.audio = audio
        self.option_eqs = option_eqs
        self.shape_models = shape_models
        self._shape_generation = 0
        self._preparing = False        # the shape models are being built
        self._begin_when_ready = False
        self._sides: tuple = ()
        self._defaults: dict = {}
        self._match = None             # the shape match's current question
        self._match_swap = False       # band B (the slider's) is the reference, not the one to adjust
        self._match_heard = 0.0        # seconds this match has played
        self._pair = None              # the playing pair generator
        self.match_mode = "alternate"
        self.balance = balance
        self.upper_prediction = upper_prediction
        self._balance_bands: list = []
        self._balance_leans: list[float] = []
        self._balance_index = 0
        self._balance_model = None
        self._balance_generation = 0
        self.balance_result = None
        self._suggestion = None       # the re-check the last balance check offers
        self._recheck_before = None   # (side, target, Hz) while a re-check runs
        self._rechecks: list[tuple[str, str, float, float]] = []  # (side, target, before, after) since the last check
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
        self._sound = None            # "band" / "pink" / "grey" / "focus" (sweep) while this view's sound plays
        self.stage2_sound = "pink"    # what the smoothness check plays: one of STAGE2_SOUNDS
        self._stage2_target = None    # the part (primary / upper) stage2_sound was last defaulted for
        self._starting = False        # starting our own sound: its change notices aren't a stop
        self._eqs: dict[int, tuple] = {}
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
        self.match_page = self._build_match()
        self.unsure_page = self._build_unsure()
        self.done_page = self._build_done()
        self.upper_page = self._build_upper_offer()
        self.balance_offer_page = self._build_balance_offer()
        self.balance_page = self._build_balance()
        self.balance_result_page = self._build_balance_result()
        for page in (self.intro_page, self.test_page, self.match_page, self.unsure_page, self.done_page,
                     self.upper_page,
                     self.balance_offer_page, self.balance_page, self.balance_result_page):
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
        self.sweep_button = QPushButton(tr("Sweep"))
        self.sweep_button.setToolTip(tr("A slow sweep through each EQ: the right one stays even, with no "
                                        "swell or dip as it passes (W)"))
        self.stage2_buttons = {"pink": self.noise_button, "grey": self.grey_button, "focus": self.sweep_button}
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
        self.keys_hint = self._label(tr("Keys: 1 2 3 choose · P play/pause · Enter answer · S same · "
                                        "N pink · G grey · W sweep · Space stop"),
                                     "gtMuted")
        self.keys_hint.setAlignment(Qt.AlignCenter)
        layout.addLayout(self._padded(self.keys_hint))

        for index, key in enumerate((Qt.Key_1, Qt.Key_2, Qt.Key_3)):
            self._shortcut(page, (key,), lambda i=index: self.select(i))
        self._shortcut(page, (Qt.Key_P,), self.toggle_play)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.answer_selected))
        self._shortcut(page, (Qt.Key_S,), self.answer_same)
        self._shortcut(page, (Qt.Key_N,), lambda: self.set_stage2_sound("pink"))
        self._shortcut(page, (Qt.Key_G,), lambda: self.set_stage2_sound("grey"))
        self._shortcut(page, (Qt.Key_W,), lambda: self.set_stage2_sound("focus"))
        self._shortcut(page, (Qt.Key_Backspace,), self.back)
        layout.addStretch(1)
        return page

    def _build_match(self) -> QWidget:
        """Stage 1 with the model: two bands take turns, a slider sets B as loud as A."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.match_title = self._label("", "gtTitle", wrap=False)
        self.match_stage = self._label(tr("Loudness match"), "gtMuted", wrap=False)
        self._header(layout, self.match_title, self.match_stage)
        self.match_instructions = self._label("", "gtMuted")
        layout.addLayout(self._padded(self.match_instructions))
        layout.addSpacing(10)
        self.match_transport = TransportBar()
        self.match_transport.clicked.connect(self.toggle_match_play)
        layout.addLayout(self._padded(self.match_transport))
        layout.addSpacing(10)

        lights = QHBoxLayout()
        lights.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        lights.setSpacing(12)
        self.match_lights = {}
        for key in ("a", "b"):
            light = QLabel(key.upper())
            light.setObjectName("matchLight")
            light.setAlignment(Qt.AlignCenter)
            light.setMinimumHeight(64)
            light.setProperty("lit", False)
            self.match_lights[key] = light
            lights.addWidget(light, 1)
        layout.addLayout(lights)
        layout.addSpacing(8)

        modes = QHBoxLayout()
        modes.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        modes.addWidget(self._label(tr("Hear"), "gtMuted", wrap=False))
        self.match_mode_buttons = {}
        for mode, text, tip in (("alternate", tr("A and B in turn"), tr("A and B take turns (3)")),
                                ("a", tr("A only"), tr("Only A (1)")), ("b", tr("B only"), tr("Only B (2)"))):
            button = QPushButton(text)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, m=mode: self.set_match_mode(m))
            self.match_mode_buttons[mode] = button
            modes.addWidget(button)
        modes.addStretch(1)
        layout.addLayout(modes)
        layout.addSpacing(12)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        slider_row.addWidget(self._label(tr("B quieter"), "gtMuted", wrap=False))
        self.match_slider = QSlider(Qt.Horizontal)
        span = int(MATCH_RANGE_DB * MATCH_STEPS_PER_DB)
        self.match_slider.setRange(-span, span)
        self.match_slider.setSingleStep(1)
        self.match_slider.setPageStep(MATCH_STEPS_PER_DB)
        self.match_slider.setAccessibleName(tr("Loudness of B"))
        self.match_slider.valueChanged.connect(self._match_gain_changed)
        slider_row.addWidget(self.match_slider, 1)
        slider_row.addWidget(self._label(tr("B louder"), "gtMuted", wrap=False))
        layout.addLayout(slider_row)
        layout.addSpacing(14)

        answers = QHBoxLayout()
        answers.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        self.match_button = QPushButton(tr("They sound equally loud"))
        self.match_button.setObjectName("primary")
        self.match_button.setMinimumHeight(40)
        self.match_button.clicked.connect(self.answer_match)
        answers.addWidget(self.match_button, 1)
        layout.addLayout(answers)
        layout.addSpacing(6)
        self.match_hint = self._label("", "gtMuted", wrap=False)
        self.match_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.match_hint)
        layout.addSpacing(8)
        self.match_progress = ProgressLine()
        self.match_progress.setAccessibleName(tr("Progress"))
        layout.addLayout(self._padded(self.match_progress))
        layout.addSpacing(14)

        footer = QHBoxLayout()
        footer.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        self.match_back = QPushButton(tr("Back"))
        self.match_back.setToolTip(tr("Take back the last answer"))
        self.match_back.clicked.connect(self.back)
        footer.addWidget(self.match_back)
        footer.addStretch(1)
        manual = QPushButton(tr("Adjust by hand"))
        manual.setToolTip(tr("Leave the test and keep what's been found so far"))
        manual.clicked.connect(self.leave_for_manual)
        footer.addWidget(manual)
        layout.addLayout(footer)
        layout.addSpacing(10)
        keys = self._label(tr("Keys: ← → move the slider (↑ ↓ finer) · 1 A only · 2 B only · 3 in turn · "
                              "P play/pause · Enter they match · Space stop"), "gtMuted")
        keys.setAlignment(Qt.AlignCenter)
        layout.addLayout(self._padded(keys))

        self._shortcut(page, (Qt.Key_Left,), lambda: self.nudge_match(-MATCH_STEPS_PER_DB // 2))
        self._shortcut(page, (Qt.Key_Right,), lambda: self.nudge_match(MATCH_STEPS_PER_DB // 2))
        self._shortcut(page, (Qt.Key_Down,), lambda: self.nudge_match(-1))
        self._shortcut(page, (Qt.Key_Up,), lambda: self.nudge_match(1))
        self._shortcut(page, (Qt.Key_1,), lambda: self.set_match_mode("a"))
        self._shortcut(page, (Qt.Key_2,), lambda: self.set_match_mode("b"))
        self._shortcut(page, (Qt.Key_3,), lambda: self.set_match_mode("alternate"))
        self._shortcut(page, (Qt.Key_P,), self.toggle_match_play)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.answer_match))
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

    def _build_balance_offer(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.balance_offer_title = self._label(tr("Check left and right"), "gtTitle")
        self._header(layout, self.balance_offer_title, self._label(tr("Optional"), "gtMuted", wrap=False))
        self.balance_summary = self._label("", "gtResult")
        layout.addLayout(self._padded(self.balance_summary))
        layout.addLayout(self._padded(self._label(tr(
            "If both resonances are right, the same sound in both ears sounds centred at every pitch. You'll "
            "hear a series of short sounds in both ears; if one sounds off to one side, move it back to the "
            "middle. Where a sound leans the way a misplaced resonance would, the app offers to check that "
            "ear again. Your EQ isn't changed by this check. Use your normal listening volume."))))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.balance_button = QPushButton(tr("Check the balance (about 2 minutes)"))
        self.balance_button.setObjectName("primary")
        self.balance_button.clicked.connect(self.start_balance)
        row.addWidget(self.balance_button)
        self.skip_balance_button = QPushButton(tr("Skip, I'm done"))
        self.skip_balance_button.clicked.connect(self.finish)
        row.addWidget(self.skip_balance_button)
        self.balance_offer_back = QPushButton(tr("Back"))
        self.balance_offer_back.clicked.connect(self.back)
        row.addWidget(self.balance_offer_back)
        row.addStretch(1)
        layout.addLayout(row)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.start_balance))
        layout.addStretch(1)
        return page

    def _build_balance(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.balance_title = self._label("", "gtTitle", wrap=False)
        self._header(layout, self.balance_title, self._label(tr("Balance check"), "gtMuted", wrap=False))
        self.balance_instructions = self._label(tr(
            "The same sound plays in both ears. Does it sound in the middle? If it leans to one side, move it "
            "back to the centre (drag, or ← →), then press Next. If it's already centred, just press Next."),
            "gtMuted")
        layout.addLayout(self._padded(self.balance_instructions))
        layout.addSpacing(10)
        self.balance_transport = TransportBar()
        self.balance_transport.clicked.connect(self.toggle_balance_sound)
        layout.addLayout(self._padded(self.balance_transport))
        layout.addSpacing(16)
        self.centre = CentreControl(CENTRE_RANGE_DB)
        self.centre.setAccessibleName(tr("Move the sound left or right"))
        self.centre.valueChanged.connect(self._centre_moved)
        layout.addLayout(self._padded(self.centre))
        hints = QHBoxLayout()
        hints.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        hints.addWidget(self._label(tr("◀ move left"), "gtMuted", wrap=False))
        hints.addStretch(1)
        self.centre_reset = QPushButton(tr("Centre"))
        self.centre_reset.setToolTip(tr("Back to the middle (Home)"))
        self.centre_reset.clicked.connect(lambda: self.centre.set_value(0.0))
        hints.addWidget(self.centre_reset)
        hints.addStretch(1)
        hints.addWidget(self._label(tr("move right ▶"), "gtMuted", wrap=False))
        layout.addLayout(hints)
        layout.addSpacing(16)
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        row.setSpacing(12)
        self.balance_prev = AnswerButton(tr("Previous sound"))
        self.balance_prev.clicked.connect(self.previous_balance_band)
        row.addWidget(self.balance_prev, 1)
        self.balance_next = AnswerButton(tr("Next"))
        self.balance_next.clicked.connect(self.next_balance_band)
        row.addWidget(self.balance_next, 3)
        layout.addLayout(row)
        layout.addSpacing(10)
        self.balance_progress = ProgressLine()
        self.balance_progress.setAccessibleName(tr("Progress"))
        layout.addLayout(self._padded(self.balance_progress))
        layout.addSpacing(14)
        footer = QHBoxLayout()
        footer.setContentsMargins(CARD_MARGIN, 0, CARD_MARGIN, 0)
        footer.addStretch(1)
        self.balance_stop = QPushButton(tr("Stop the check"))
        self.balance_stop.clicked.connect(self._leave_balance)
        footer.addWidget(self.balance_stop)
        layout.addLayout(footer)
        self._shortcut(page, (Qt.Key_P,), self.toggle_balance_sound)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter), lambda: self._enter(self.next_balance_band))
        self._shortcut(page, (Qt.Key_Backspace,), self.previous_balance_band)
        layout.addStretch(1)
        return page

    def _build_balance_result(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.balance_result_title = self._label(tr("Balance"), "gtTitle")
        self._header(layout, self.balance_result_title)
        self.balance_headline = self._label("", "gtResult")
        font = self.balance_headline.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.balance_headline.setFont(font)
        layout.addLayout(self._padded(self.balance_headline))
        self.balance_notes = self._label("", "gtText")
        layout.addLayout(self._padded(self.balance_notes))
        row = QHBoxLayout()
        row.setContentsMargins(CARD_MARGIN, 8, CARD_MARGIN, 0)
        self.recheck_button = QPushButton()
        self.recheck_button.setObjectName("primary")
        self.recheck_button.clicked.connect(self.start_recheck)
        row.addWidget(self.recheck_button)
        self.balance_again = QPushButton(tr("Run the balance check again"))
        self.balance_again.clicked.connect(self.start_balance)
        row.addWidget(self.balance_again)
        self.balance_done = QPushButton(tr("Done"))
        self.balance_done.clicked.connect(self.finish)
        row.addWidget(self.balance_done)
        row.addStretch(1)
        layout.addLayout(row)
        self._shortcut(page, (Qt.Key_Return, Qt.Key_Enter),
                       lambda: self._enter(self.start_recheck if self.recheck_button.isVisible() else self.finish))
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
            f"{border.alpha()}); border: none; }}"
            f"QLabel#matchLight {{ color: {c['muted']}; background: {c['card_top']}; font-size: 22px;"
            f" font-weight: 600; border: 1px solid rgba({border.red()},{border.green()},{border.blue()},"
            f"{border.alpha()}); border-radius: 10px; }}"
            f"QLabel#matchLight[lit=\"true\"] {{ color: {c['card_top']}; background: {c['text']}; }}")

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
        self._sides, self._defaults = tuple(sides), dict(default_hz or {})
        self.test = self._new_test({})
        self._prepare_shape_models()
        self._stage2_target = None
        self._hue = self.rng.uniform(0.0, 360.0)
        self._round_index = 0
        self._saved_eq_enabled = None
        self.pages.setCurrentWidget(self.intro_page)
        self.start_button.setFocus()

    def _new_test(self, shape_models: dict) -> GuidedTest:
        return GuidedTest(self._sides, dict(self._defaults), self.rng, offer_upper=self.upper_prediction is not None,
                          shape_models=shape_models)

    def _prepare_shape_models(self) -> None:
        """Build the loudness match's models on a worker; until then (or without them) the louder test."""
        self._shape_generation += 1
        self._begin_when_ready = False
        self._preparing = self.shape_models is not None
        self._sync_intro()
        if not self._preparing:
            return
        generation = self._shape_generation
        build = self.shape_models()

        def work():
            return generation, build()

        job = Worker(work)
        job.finished_ok.connect(self._shape_models_ready)
        job.failed.connect(self._shape_models_failed)
        job.finished.connect(lambda: _JOBS.discard(job))
        _JOBS.add(job)
        job.start()

    def _shape_models_ready(self, result) -> None:
        generation, models = result
        if generation != self._shape_generation or self.test is None:
            return
        self._preparing = False
        if self.pages.currentWidget() is self.intro_page or self._begin_when_ready:
            self.test = self._new_test(dict(models))
        self._sync_intro()
        if self._begin_when_ready:
            self.begin()

    def _shape_models_failed(self, _message: str) -> None:
        self._preparing = False  # the louder test it is
        self._sync_intro()
        if self._begin_when_ready:
            self.begin()

    def _sync_intro(self) -> None:
        if self.shape_models is not None:
            self.intro_steps.setText("•  " + tr(
                "For each ear: first match how loud two sounds are, a few times; then pick the smoothest of "
                "three EQs. Listen for loudness only, not for which sound is higher. About 3 minutes per ear."))
        else:
            self.intro_steps.setText("•  " + tr(
                "For each ear you'll hear two or three sounds at a time. First pick the louder sound, then "
                "the smoothest noise. It takes about 2 minutes in all."))
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
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)  # the match page, when the test starts with a loudness match
        self._focus_round()

    def stop(self) -> None:
        """Silence this view's sound and put the EQ preview back as it was."""
        self._frame.stop()
        self._eq_generation += 1
        if self._sound is not None:
            self._sound = None
            self.audio.stop_all()
        self._balance_generation += 1
        self.audio.engine.set_lr_gains(1.0, 1.0)
        if self._saved_eq_enabled is not None:
            self.audio.set_eq_enabled(self._saved_eq_enabled)
            self._saved_eq_enabled = None
        self._pair = None
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
        if test.kind == SHAPE:
            self._new_match(keep_playing)
            return
        self._match = None
        self._round = test.options()
        count = len(self._round.options)
        self.heard, self._heard_for = set(), {}
        self._since = None
        self._applied = None
        self._eqs = {}
        self._play_when_ready = False
        for index, card in enumerate(self.cards):
            card.setVisible(index < count)
            card.set_heard(False)
            card.set_selected(False, animate=False)
            card.clear_shape()
        self.selected = None
        self._paint_accents()
        louder = test.kind == LOUDER
        upper = test.target == UPPER
        self.title.setText(_ear_title(test.side, test.round_number))
        if test.kind == AUDIBLE:
            self._new_audible_round()
            return
        if upper:  # the upper treble has no louder test (see guided_test)
            self.stage_label.setText(tr("Upper treble: smoothness check"))
        else:
            self.stage_label.setText(tr("Louder test") if louder else tr("Smoothness check"))
        if louder:
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
        if not louder and test.target != self._stage2_target:
            self._stage2_target = test.target  # the sweep first up high: hearing's fall-off makes noise dull
            self.stage2_sound = "focus" if upper else "pink"
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

    def _focus_round(self) -> None:
        """Keyboard focus on the page now showing: the slider in a match, else the play bar."""
        if self.pages.currentWidget() is self.match_page:
            self.match_slider.setFocus()
        else:
            self.transport.setFocus()

    def _new_match(self, keep_playing: bool) -> None:
        """A loudness match: which band the slider moves, and where it starts, are random."""
        test = self.test
        self._round = None
        self._match = test.options()
        self._match_swap = self.rng.random() < 0.5
        self._match_heard = 0.0
        self._enter_louder_stage()
        self.pages.setCurrentWidget(self.match_page)
        self.match_title.setText(tr("{ear} · Match {n}").format(ear=_ear(test.side), n=test.round_number))
        again = tr(" (once more: the last answer didn't fit the others)") if self._match.repeat else ""
        self.match_instructions.setText(tr(
            "Two sounds take turns in your {ear}: A, then B. Move the slider until B sounds exactly as loud as A. "
            "One sounds higher than the other: ignore that, and listen only to how loud they are.{again}").format(
            ear=_ear(test.side).lower(), again=again))
        span = MATCH_START_DB * MATCH_STEPS_PER_DB
        self.match_slider.blockSignals(True)
        self.match_slider.setValue(int(round(self.rng.uniform(-span, span))))
        self.match_slider.blockSignals(False)
        self.match_progress.set_value(test.progress())
        self.match_back.setEnabled(self._can_undo())
        if self._sound == "pair" and keep_playing:
            self.play_match()
        elif self._sound is not None:
            self._pause()
        self._sync_match()
        self.match_slider.setFocus()

    @property
    def match_bands(self) -> tuple[float, float]:
        """(A, B) in Hz: B is the slider's band."""
        q = self._match
        return (q.adjust, q.reference) if self._match_swap else (q.reference, q.adjust)

    @property
    def match_gain_db(self) -> float:
        """How much the slider raises (or lowers) B, in dB."""
        return self.match_slider.value() / MATCH_STEPS_PER_DB

    def play_match(self) -> None:
        if self._match is None or self.test is None:
            return
        a, b = self.match_bands
        pair = self.audio.play_pair(a, b, MATCH_BAND, self.match_gain_db, self.match_mode, self.test.side)
        if pair is None or self.audio.playing != "pair":  # the quiet-start warning said no
            self._sync_match()
            return
        self._pair = pair
        self._sound = "pair"
        self._since = self._clock()
        self._frame.start()
        self._sync_match()

    def toggle_match_play(self) -> None:
        if self.pages.currentWidget() is not self.match_page:
            return
        if self._sound is not None:
            self._pause()
        else:
            self.play_match()

    def set_match_mode(self, mode: str) -> None:
        if mode not in MATCH_MODES or self.pages.currentWidget() is not self.match_page:
            return
        self.match_mode = mode
        if self._pair is not None and self._sound == "pair":
            self._pair.mode = mode
        elif self._sound is None:
            self.play_match()
        self._sync_match()

    def nudge_match(self, steps: int) -> None:
        if self.pages.currentWidget() is self.match_page:
            self.match_slider.setValue(self.match_slider.value() + steps)

    def _match_gain_changed(self, _value: int) -> None:
        if self._pair is not None and self._sound == "pair":
            self._pair.gain_db = self.match_gain_db

    @property
    def can_match(self) -> bool:
        return self._match is not None and self._match_heard >= MATCH_HEARD_SECONDS

    def answer_match(self) -> None:
        """B now sounds as loud as A: tell the search how much louder the band to adjust was heard."""
        if not self.can_match or self.pages.currentWidget() is not self.match_page:
            return
        gain = self.match_gain_db  # heard(B) + gain = heard(A)
        louder_db = gain if self._match_swap else -gain  # (adjust − reference)
        self._answer(louder_db)

    def _sync_match(self) -> None:
        playing = self._sound == "pair"
        self.match_transport.set_playing(playing, tr("Pause") if playing else tr("Play"))
        self.match_transport.setToolTip(tr("Play or pause (P)"))
        for mode, button in self.match_mode_buttons.items():
            button.setChecked(mode == self.match_mode)
        current = self._pair.current if (playing and self._pair is not None) else None
        for key, light in self.match_lights.items():
            lit = current == key
            if light.property("lit") != lit:
                light.setProperty("lit", lit)
                light.style().unpolish(light)
                light.style().polish(light)
        ok = self.can_match
        self.match_button.setEnabled(ok)
        self.match_hint.setText(" " if ok else tr("Play them first, then move the slider"))

    def _new_audible_round(self) -> None:
        """Before an ear's upper treble: one band at the predicted peak — heard clearly or not?"""
        test = self.test
        self.title.setText(_ear(test.side))
        self.stage_label.setText(tr("Upper treble: can you hear it?"))
        self.instructions.setText(tr(
            "A high sound in your {ear}, at your normal listening volume. Play it: can you hear it clearly? "
            "Many people hear sounds this high only faintly, or not at all. That's normal; the upper treble "
            "then simply stays as calibrated.").format(ear=_ear(test.side).lower()))
        self.sound_row.setVisible(False)
        self.same_button.setText(tr("Barely, or not at all"))
        self._enter_louder_stage()
        self.progress.set_value(test.progress())
        self.back_button.setEnabled(self._can_undo())
        if self._sound is not None:
            self._pause()
        self._sync_answers()
        self._sync_transport()

    @property
    def _banded(self) -> bool:
        """This round plays noise bands with the EQ off (the louder test and the audibility question)."""
        return self.test is not None and self.test.kind in (LOUDER, AUDIBLE)

    def _can_undo(self) -> bool:
        test = self.test
        return test is not None and (len(test.stages) > 1 or bool(test.stage.search.answers))

    def _paint_accents(self) -> None:
        if self._round is None:
            return
        colors = accent_colors(self._hue, self._round_index, len(self._round.options), is_dark(self.palette()))
        for card, color in zip(self.cards, colors):
            card.set_accent(color)
        self._sync_answers()

    def _enter_louder_stage(self) -> None:  # also the loudness match
        if self._saved_eq_enabled is None:
            self._saved_eq_enabled = self.audio.eq_enabled
        if self.audio.eq_enabled:
            self.audio.set_eq_enabled(False)  # the louder test is judged without the EQ
        if self._sound in STAGE2_SOUNDS:  # Stage 2 → (back to) Stage 1: switch the sound type
            self._sound = None
            self.audio.stop_all()

    def _enter_noise_stage(self) -> None:
        if self._sound in ("band", "pair"):
            self._sound = None
            self.audio.stop_all()
        elif self._sound == "focus":
            self._start_stage2_sound()  # the sweep moves to this round's options
        self._design_eqs()

    def _sweep_center(self) -> float:
        """The sweep is centred on the round's middle option and spans all three."""
        return sorted(self._round.options)[len(self._round.options) // 2]

    def _start_stage2_sound(self) -> bool:
        """(Re)start the smoothness check's sound — noise or sweep — through the applied EQ."""
        sound = self.stage2_sound
        self._sound = sound
        self._starting = True  # routing notifies listeners before the new sound is playing
        try:
            if sound == "focus":
                self.audio.play_focus_sweep(self._sweep_center(), self.test.side)
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

    def _pause(self) -> None:
        if self._sound == "pair" and self._since is not None:
            self._match_heard += self._clock() - self._since
        self._account()
        self._pair = None
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
        self._applied = index

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
        if self._sound == "pair":
            now = self._clock()
            if self._since is not None:
                self._match_heard += now - self._since
            self._since = now
            self._sync_match()
            return
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
            self._pair = None
            self._frame.stop()
            self._sync_cards()
            self._sync_transport()
            if self._match is not None:
                self._sync_match()
            if self.pages.currentWidget() is self.balance_page:
                self._show_balance_band()

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
        target = self.test.target
        results = self.test.upper_results if target == UPPER else self.test.results
        before = set(results)
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
            before = self._recheck_before
            if before is not None and before[:2] == (side, target):
                after = (test.upper_results if target == UPPER else test.results)[side]
                self._rechecks.append((side, target, before[2], after))
                self._recheck_before = None
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
        shape = next((s.search for s in reversed(stages) if s.kind == SHAPE), None)
        smooth = next((s.search for s in reversed(stages) if s.kind not in (LOUDER, SHAPE)), None)
        louder_rounds = len(louder.answers) if louder else 0
        smooth_rounds = len(smooth.answers) if smooth else 0
        self.done_title.setText(tr("{ear} done").format(ear=_ear(side)))
        if target == UPPER and hz is None:
            self.result_label.setText(tr("{ear}: upper treble as calibrated").format(ear=_ear(side)))
            note = tr("You didn't hear the high sound clearly, so this ear's upper treble stays where your "
                      "calibration predicts it. Nothing to fine-tune there.")
        elif target == UPPER:
            self.result_label.setText(tr("{ear} upper treble: {hz} Hz").format(ear=side_name(side),
                                                                                hz=f"{int(round(hz)):,}"))
            note = tr("Found with {n} rounds of the smoothness check, starting from where your calibration "
                      "predicts it. You can fine-tune it by hand later.").format(n=smooth_rounds)
        else:
            self.result_label.setText(tr("{ear} resonance: {hz} Hz").format(ear=side_name(side),
                                                                             hz=f"{int(round(hz)):,}"))
            if shape is not None:
                note = tr("Found with {matches} loudness matches, then {smooth} rounds of the smoothness check. "
                          "You can fine-tune it by hand later.").format(matches=len(shape.answers),
                                                                         smooth=smooth_rounds)
            elif louder is not None and louder.done and louder.result().fell_back:
                note = tr("The sounds were alike, so this comes from the smoothness check alone "
                          "({n} rounds of noise through your EQ).").format(n=smooth_rounds)
            else:
                note = tr("Found with {louder} rounds of the louder test, then {smooth} rounds of the smoothness "
                          "check. You can fine-tune it by hand later.").format(louder=louder_rounds,
                                                                                smooth=smooth_rounds)
        self.result_note.setText(note)
        self.done_progress.set_value(test.progress())
        if test.status == "finished":
            self.next_button.setText(tr("Next") if self.can_balance else tr("Done"))
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
            self._after_test()
            return
        if self.test.status == "upper_offer":
            self._show_upper_offer()
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self._focus_round()

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
            self._after_test()
            return
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self._focus_round()

    def skip_upper(self) -> None:
        if self.test is None or self.test.status != "upper_offer":
            return
        self.test.skip_upper()
        self._after_test()

    def redo_ear(self) -> None:
        if self.test is None or self.test.status != "unsure":
            return
        self.test.redo_ear()
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self._focus_round()

    def use_estimate(self) -> None:
        """After an unsure louder test: carry on to the smoothness check from the best guess."""
        if self.test is None or self.test.status != "unsure":
            return
        self.test.use_estimate()
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self._focus_round()

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
        if not self.ready and self.test is not None and not self._banded:
            self.transport.setToolTip(tr("Getting the sounds ready…"))
        else:
            self.transport.setToolTip(tr("Play or pause (P)"))

    def _sync_answers(self) -> None:
        if self._round is None:
            return
        letter = "ABC"[self.selected] if self.selected is not None else None
        louder = self.test.kind == LOUDER
        if self.test.kind == AUDIBLE:
            self.answer_button.setText(tr("I hear it clearly"))
        elif letter is None:
            self.answer_button.setText(tr("Louder") if louder else tr("Smoothest"))
        elif louder:
            self.answer_button.setText(tr("Louder: {letter}").format(letter=letter))
        else:
            self.answer_button.setText(tr("Smoothest: {letter}").format(letter=letter))
        self.answer_button.set_accent(self.cards[self.selected].accent if self.selected is not None else None)
        ok = self.can_answer
        self.answer_button.setEnabled(ok and self.selected is not None)
        self.same_button.setEnabled(ok)
        if ok:
            self.listen_hint.setText(" ")
        else:
            self.listen_hint.setText(tr("Play it first") if self.test.kind == AUDIBLE else tr("Listen to each one first"))

    # ── the balance check ──────────────────────────────────────────────────
    @property
    def can_balance(self) -> bool:
        return self.balance is not None and self.test is not None and set(self.test.sides) == {"L", "R"}

    def _after_test(self) -> None:
        """Both ears done (and the upper treble, if wanted): offer the balance check, else finish."""
        if not self.can_balance:
            self.finish()
            return
        self._pause_if_playing()
        lines = [tr("{ear} resonance: {hz} Hz").format(ear=side_name(side), hz=f"{int(round(hz)):,}")
                 for side, hz in self.test.results.items()]
        lines += [tr("{ear} upper treble: {hz} Hz").format(ear=side_name(side), hz=f"{int(round(hz)):,}")
                  for side, hz in self.test.upper_results.items() if hz is not None]
        self.balance_summary.setText("\n".join(lines))
        again = self.balance_result is not None
        self.balance_offer_title.setText(tr("Check the balance again") if again else tr("Check left and right"))
        self.pages.setCurrentWidget(self.balance_offer_page)
        self.balance_button.setFocus()

    def finish(self) -> None:
        self.stop()
        self.finished.emit()

    def start_balance(self) -> None:
        """Get the current EQ and the bands ready (on a worker), then show the first sound."""
        if not self.can_balance:
            return
        self._pause_if_playing()
        bands, build = self.balance(self.rng)
        self._balance_bands = list(bands)
        self._balance_leans = [0.0] * len(bands)
        self._balance_index = 0
        self._balance_model = None
        self._balance_generation += 1
        generation = self._balance_generation

        def work():
            return generation, build()

        job = Worker(work)
        job.finished_ok.connect(self._balance_ready)
        job.failed.connect(self._eqs_failed)
        job.finished.connect(lambda: _JOBS.discard(job))
        _JOBS.add(job)
        job.start()
        self.pages.setCurrentWidget(self.balance_page)
        self._show_balance_band()
        self.balance_transport.setFocus()

    def _balance_ready(self, result) -> None:
        generation, (eq, designed, model) = result
        if generation != self._balance_generation:
            return
        self._balance_model = model
        if self._saved_eq_enabled is None:
            self._saved_eq_enabled = self.audio.eq_enabled
        self.audio.offer_firs(eq, designed)
        self.audio.set_eq(eq)              # the tuning as found, not a test option
        if not self.audio.eq_enabled:
            self.audio.set_eq_enabled(True)
        self._show_balance_band()
        if self._play_when_ready:
            self.play_balance()

    @property
    def balance_band(self):
        return self._balance_bands[self._balance_index] if self._balance_bands else None

    def _show_balance_band(self) -> None:
        n, i = len(self._balance_bands), self._balance_index
        self.balance_title.setText(tr("Sound {i} of {n}").format(i=i + 1, n=n))
        self.centre.set_value(self._balance_leans[i] if self._balance_leans else 0.0, emit=False)
        self._apply_centre()
        self.balance_progress.set_value(i / max(n, 1))
        self.balance_prev.setEnabled(i > 0)
        self.balance_next.setText(tr("Finish") if i == n - 1 else tr("Next"))
        ready = self._balance_model is not None
        playing = self._sound == "band" and self.pages.currentWidget() is self.balance_page
        self.balance_transport.set_playing(playing, tr("Pause") if playing else tr("Play"))
        self.balance_transport.setEnabled(ready)
        self.balance_transport.setToolTip(tr("Play or pause (P)") if ready else tr("Getting the sounds ready…"))
        if playing:
            band = self.balance_band
            self.audio.switch_band(band.hz, band.width, "both")

    def toggle_balance_sound(self) -> None:
        if self.pages.currentWidget() is not self.balance_page:
            return
        if self._sound is not None:
            self._pause()
            self._show_balance_band()
        else:
            self.play_balance()

    def play_balance(self) -> None:
        if self._balance_model is None:
            self._play_when_ready = True
            return
        band = self.balance_band
        self._starting = True
        try:
            self.audio.switch_band(band.hz, band.width, "both")
        finally:
            self._starting = False
        if self.audio.playing == "band":
            self._sound = "band"
            self._play_when_ready = False
        self._show_balance_band()

    def _centre_moved(self, _db: float) -> None:
        if self._balance_leans:
            self._balance_leans[self._balance_index] = self.centre.value
        self._apply_centre()
        if self._sound is None and self._balance_model is not None:
            self.play_balance()  # moving the control means listening

    def _apply_centre(self) -> None:
        """Positive moves the sound right: the right ear up, the left down, by half each."""
        db = self.centre.value
        self.audio.engine.set_lr_gains(10 ** (-db / 40), 10 ** (db / 40))

    def next_balance_band(self) -> None:
        if self.pages.currentWidget() is not self.balance_page or self._balance_model is None:
            return
        self._balance_leans[self._balance_index] = self.centre.value
        if self._balance_index + 1 < len(self._balance_bands):
            self._balance_index += 1
            self._show_balance_band()
            return
        self._pause_if_playing()
        self.audio.engine.set_lr_gains(1.0, 1.0)
        self._analyse_balance()

    def previous_balance_band(self) -> None:
        if self.pages.currentWidget() is not self.balance_page or self._balance_index == 0:
            return
        self._balance_leans[self._balance_index] = self.centre.value
        self._balance_index -= 1
        self._show_balance_band()

    def _leave_balance(self) -> None:
        self._pause_if_playing()
        self._balance_generation += 1
        self.audio.engine.set_lr_gains(1.0, 1.0)
        self._after_test()

    def _analyse_balance(self) -> None:
        bands, leans, model = list(self._balance_bands), list(self._balance_leans), self._balance_model
        uncertainty = self.test.uncertainty()
        self._balance_generation += 1
        generation = self._balance_generation

        def work():
            return generation, analyse(bands, leans, model, uncertainty)

        job = Worker(work)
        job.finished_ok.connect(self._balance_analysed)
        job.failed.connect(self._eqs_failed)
        job.finished.connect(lambda: _JOBS.discard(job))
        _JOBS.add(job)
        job.start()
        self.balance_title.setText(tr("Reading your answers…"))
        self.balance_next.setEnabled(False)

    def _balance_analysed(self, result) -> None:
        generation, found = result
        if generation != self._balance_generation:
            return
        self.balance_next.setEnabled(True)
        self.balance_result = found
        self._suggestion = self._resolve(found.suggestion)
        self._show_balance_result(found)

    def _resolve(self, suggestion):
        """Balance only sees the difference between the ears. If the suggested ear was just re-checked
        and hardly moved, the other ear is the likelier one: suggest it, the opposite way."""
        if suggestion is None:
            return None
        for side, target, before, after in self._rechecks:
            if side == suggestion.side and target == suggestion.target and abs(after / before - 1) < 0.015:
                other = "R" if side == "L" else "L"
                found = self.test.upper_results if target == UPPER else self.test.results
                if found.get(other) is None:
                    return suggestion
                hz = found[other] * suggestion.current_hz / suggestion.hz
                return type(suggestion)(other, target, found[other], hz, suggestion.evidence)
        return suggestion

    @staticmethod
    def _khz(hz: float) -> str:
        return tr("{khz} kHz").format(khz=f"{hz / 1000:.1f}")

    def _show_balance_result(self, found) -> None:
        notes = []
        suggestion = self._suggestion
        self.recheck_button.setVisible(suggestion is not None)
        if suggestion is not None:
            which = tr("upper treble") if suggestion.target == UPPER else tr("resonance")
            way = tr("higher") if suggestion.hz > suggestion.current_hz else tr("lower")
            self.balance_headline.setText(tr("Your {ear}'s {which} may be a little {way}").format(
                ear=_ear(suggestion.side).lower(), which=which, way=way))
            notes.append(tr("Some sounds leaned the way a misplaced {which} would. A short smoothness check of "
                            "that ear (about 30 seconds) can settle it; nothing changes unless you choose it "
                            "there.").format(which=which))
            self.recheck_button.setText(tr("Check the {ear} again").format(ear=_ear(suggestion.side).lower()))
        elif found.status == "balanced":
            self.balance_headline.setText(tr("Left and right are balanced ✓"))
            notes.append(tr("Every sound was centred within about 2 dB: your resonances agree with each other."))
        else:
            self.balance_headline.setText(tr("Mostly balanced"))
            if found.uneven_hz is not None:
                side = tr("left") if found.leans.get(found.uneven_hz, 0) > 0 else tr("right")
                notes.append(tr("Around {where} the sound leaned to the {side}. That doesn't look like a misplaced "
                                "resonance (more likely the calibration or your hearing there), so your EQ stays "
                                "as it is.").format(where=self._khz(found.uneven_hz), side=side))
        if abs(found.broad_lean_db) > 1.5:
            side = tr("left") if found.broad_lean_db > 0 else tr("right")
            notes.append(tr("Across the treble the sound leans a little to the {side}: most likely your hearing or "
                            "the fit, so it isn't changed.").format(side=side))
        if found.reseat is not None:
            weaker = side_name(found.reseat).lower()
            louder = tr("right") if found.reseat == "L" else tr("left")
            notes.append(tr("The bass leans to the {side}: the {ear} IEM may not be sealed as well. Push it in a "
                            "little and twist, then run the balance check again.").format(side=louder, ear=weaker))
        if found.unsteady:
            notes.append(tr("Your answers to the repeated sounds were rather different, so this check is less "
                            "certain."))
        self.balance_notes.setText("\n\n".join(notes))
        self.pages.setCurrentWidget(self.balance_result_page)
        (self.recheck_button if suggestion is not None else self.balance_done).setFocus()

    def start_recheck(self) -> None:
        suggestion = self._suggestion
        if suggestion is None or self.test is None or self.test.status != "finished":
            return
        self._recheck_before = (suggestion.side, suggestion.target, suggestion.current_hz)
        self.test.recheck(suggestion.side, suggestion.target, suggestion.hz)
        self.pages.setCurrentWidget(self.test_page)
        self._new_round(keep_playing=False)
        self._focus_round()
