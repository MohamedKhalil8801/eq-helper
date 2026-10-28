"""Test audio: your tuner's suggested links plus your own files and links (whatever you choose).

Local files play inside the app, through your EQ, with an EQ on/off switch
for A/B. Links open in the browser. Formats the app can't decode open in
the system's player.
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from ..playback import SongPlayer, UnsupportedFile, can_play
from .common import Card, hint, tr

AUDIO_FILTER = "Audio files (*.wav *.flac *.mp3 *.ogg *.opus *.aif *.aiff *.m4a *.aac *.wma);;All files (*)"


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class TestSongsPage(QWidget):
    __test__ = False  # not a pytest class

    def __init__(self, ctx, audio, parent=None):
        super().__init__(parent)
        self.ctx, self.audio = ctx, audio
        self.player = SongPlayer(audio.engine)
        self.operator_items: list[dict] = []
        self._pending: Future | None = None
        self._loaded_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(hint(tr("A recording you know well is the best test: whatever you like to listen to. "
                                 "Play it here and switch the EQ on and off, or open your tuner's suggestions "
                                 "in the browser.")))
        card = Card()
        self.list = QListWidget()
        self.list.setAccessibleName(tr("Test audio"))
        self.list.itemDoubleClicked.connect(lambda _i: self.activate_selected())
        self.list.currentRowChanged.connect(self._sync_buttons)
        card.body.addWidget(self.list, 1)
        # nothing added yet: a short invitation, with the buttons in the middle of the card
        self.empty_top = QWidget()
        card.body.addWidget(self.empty_top, 1)
        self.empty_title = QLabel(tr("Nothing here yet"))
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        card.body.addWidget(self.empty_title)
        self.empty_text = hint(tr("Add audio files from your computer, or a web link, to compare with "
                                  "the EQ on and off."))
        self.empty_text.setAlignment(Qt.AlignCenter)
        card.body.addWidget(self.empty_text)
        card.body.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        self.add_files_button = QPushButton(tr("+ Add audio files…"))
        self.add_files_button.setObjectName("primary")
        self.add_files_button.clicked.connect(self._browse)
        self.add_link_button = QPushButton(tr("🔗 Add link…"))
        self.add_link_button.clicked.connect(self._ask_link)
        self.remove_button = QPushButton(tr("Remove"))
        self.remove_button.setToolTip(tr("Remove the selected item from the list (not from your computer)"))
        self.remove_button.clicked.connect(self._remove)
        for button in (self.add_files_button, self.add_link_button, self.remove_button):
            button.setProperty("large", True)
            button.setMinimumHeight(46)
            row.addWidget(button)
        row.addStretch(1)
        card.body.addLayout(row)
        self.empty_bottom = QWidget()
        card.body.addWidget(self.empty_bottom, 1)
        layout.addWidget(card, 1)

        transport = Card(tr("Now playing"))
        self.now = QLabel(tr("Nothing yet. Double-click an item to play it."))
        self.now.setWordWrap(True)
        transport.body.addWidget(self.now)
        row = QHBoxLayout()
        self.play_button = QPushButton(tr("▶ Play"))
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self._play_pause)
        row.addWidget(self.play_button)
        self.stop_button = QPushButton(tr("■ Stop"))
        self.stop_button.clicked.connect(self.audio.stop_all)
        row.addWidget(self.stop_button)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setAccessibleName(tr("Position"))
        self.seek.sliderReleased.connect(lambda: self.player.seek(self.seek.value()))
        row.addWidget(self.seek, 1)
        self.time_label = QLabel("0:00 / 0:00")
        row.addWidget(self.time_label)
        transport.body.addLayout(row)
        self.eq_toggle = QPushButton(tr("EQ off: click to turn it on"))
        self.eq_toggle.setCheckable(True)
        self.eq_toggle.setMinimumHeight(34)
        self.eq_toggle.clicked.connect(lambda on: self.audio.set_eq_enabled(on))
        transport.body.addWidget(self.eq_toggle)
        self.external_button = QPushButton(tr("Open in system player"))
        self.external_button.hide()
        self.external_button.clicked.connect(self._open_external)
        transport.body.addWidget(self.external_button)
        layout.addWidget(transport)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        audio.on_change(self._audio_changed)
        self._fill()
        self._audio_changed()

    # ── items ──────────────────────────────────────────────────────────────
    def _customer_items(self) -> list[dict]:
        return [dict(i) for i in (self.ctx.settings.get("songs") or []) if isinstance(i, dict) and i.get("path")]

    def set_calibration(self, loaded) -> None:
        manifest = loaded.manifest
        operator = manifest.operator.name if manifest.operator and manifest.operator.name else tr("your tuner")
        self.operator_items = [{"label": link.label, "path": link.url, "kind": "link", "from": operator}
                               for link in manifest.test_song_links]
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for item in self.operator_items:
            self._add_row(item, operator=True)
        for item in self._customer_items():
            self._add_row(item, operator=False)
        empty = self.list.count() == 0
        self.list.setVisible(not empty)
        for widget in (self.empty_top, self.empty_title, self.empty_text, self.empty_bottom):
            widget.setVisible(empty)
        self.remove_button.setVisible(not empty)
        self._sync_buttons()

    def _add_row(self, item: dict, operator: bool) -> None:
        icon = "🔗" if item.get("kind") == "link" else "♪"
        text = f"{icon}  {item.get('label') or Path(item['path']).name}"
        if operator:
            text += "  " + tr("(suggested by {name})").format(name=item.get("from"))
        row = QListWidgetItem(text)
        row.setData(Qt.UserRole, (item, operator))
        row.setToolTip(item["path"])
        self.list.addItem(row)

    def labels(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def add_files(self, paths) -> None:
        items = self._customer_items()
        known = {i["path"] for i in items}
        for path in paths:
            if path not in known:
                items.append({"label": Path(path).stem, "path": str(path), "kind": "file"})
        self.ctx.settings.set("songs", items)
        self._fill()

    def add_link(self, label: str, url: str) -> None:
        items = self._customer_items() + [{"label": label or url, "path": url, "kind": "link"}]
        self.ctx.settings.set("songs", items)
        self._fill()

    def _browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add audio files"), "", tr(AUDIO_FILTER))
        if paths:
            self.add_files(paths)

    def _ask_link(self) -> None:
        url, ok = QInputDialog.getText(self, tr("Add link"), tr("Web address (for example a YouTube link):"))
        if ok and url.strip():
            label, _ = QInputDialog.getText(self, tr("Add link"), tr("Name (optional):"))
            self.add_link(label.strip(), url.strip())

    def _selected(self):
        row = self.list.currentItem()
        return row.data(Qt.UserRole) if row is not None else (None, False)

    def _sync_buttons(self, *_):
        item, operator = self._selected()
        self.remove_button.setEnabled(item is not None and not operator)

    def _remove(self) -> None:
        item, operator = self._selected()
        if item is None or operator:
            return
        self.ctx.settings.set("songs", [i for i in self._customer_items() if i["path"] != item["path"]])
        self._fill()

    # ── playing ────────────────────────────────────────────────────────────
    def activate_selected(self) -> None:
        item, _operator = self._selected()
        if item is None:
            return
        if item.get("kind") == "link":
            QDesktopServices.openUrl(QUrl(item["path"]))
            return
        path = Path(item["path"])
        self._loaded_path = path
        self.now.setText(path.name)
        if not can_play(path):
            self.external_button.show()
            self.player.open_externally(path)
            return
        self.external_button.hide()
        self.now.setText(tr("Loading {name}…").format(name=path.name))
        self._pending = self.player.load(path)

    def _tick(self) -> None:
        if self._pending is not None and self._pending.done():
            future, self._pending = self._pending, None
            try:
                song = future.result()
            except UnsupportedFile as exc:
                self.now.setText(str(exc))
                self.external_button.show()
                return
            self.seek.setRange(0, int(song.duration))
            self.now.setText(song.path.name)
            self._start()
        if self.player.song is not None:
            if self.player.finished:
                self.player.stop()
                self.audio.stop_all()
            if not self.seek.isSliderDown():
                self.seek.setValue(int(self.player.position))
            self.time_label.setText(f"{_clock(self.player.position)} / {_clock(self.player.song.duration)}")

    def _start(self) -> None:
        if not self.audio.may_play():
            return
        self.audio.engine.stop()  # tones and sweeps give way to the song
        self.audio.route("both")
        self.player.play()
        self.audio.mark_playing("song")

    def _play_pause(self) -> None:
        if self.player.song is None:
            self.activate_selected()
            return
        if self.player.playing and not self.player.paused:
            self.player.pause()
            self._audio_changed()
        else:
            self._start()

    def _open_external(self) -> None:
        if self._loaded_path is not None:
            self.player.open_externally(self._loaded_path)

    def _audio_changed(self) -> None:
        if self.audio.playing != "song" and self.player.playing:
            self.player.playing = self.player.paused = False  # stopped or replaced elsewhere
        playing = self.player.playing and not self.player.paused
        self.play_button.setText(tr("❚❚ Pause") if playing else tr("▶ Play"))
        on = self.audio.eq_enabled
        self.eq_toggle.setChecked(on)
        self.eq_toggle.setText(tr("EQ on: click to hear without it") if on
                               else tr("EQ off: click to turn it on"))
