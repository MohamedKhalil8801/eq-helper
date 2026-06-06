"""Grey noise via myNoise.net loaded in a hidden Qt WebEngine view.

The page is loaded once in a hidden QWebEngineView. Pressing "P" on the page
toggles play/pause of the grey noise generator. The audio plays through the
system's default output device (managed entirely by the browser engine, not by
our PortAudio stream).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

_GREY_NOISE_URL = (
    "https://mynoise.net/NoiseMachines/greyNoiseGenerator.php"
    "?l=99999999999999999999&a=1&am=s&w=0"
    "&title=Grey%20Noise&c=1"
)


class GreyNoiseWebPlayer:
    """Loads the myNoise grey noise page in a hidden WebEngine view.

    Call :meth:`toggle` to send a "P" keypress which starts or pauses the
    noise on the page. The view is created lazily on the first toggle.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self._parent = parent
        self._view: Optional[QWebEngineView] = None
        self._loaded = False
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing

    def _ensure_view(self) -> QWebEngineView:
        if self._view is None:
            self._view = QWebEngineView(self._parent)
            self._view.setFixedSize(1, 1)
            self._view.hide()
            # Enable audio autoplay.
            settings = self._view.settings()
            from PySide6.QtWebEngineCore import QWebEngineSettings
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,
                False,
            )
            self._view.loadFinished.connect(self._on_load_finished)
        return self._view

    def _on_load_finished(self, ok: bool) -> None:
        self._loaded = ok

    def toggle(self) -> None:
        """Load the page (if needed) and send 'P' to play or pause."""
        view = self._ensure_view()
        if not self._loaded:
            view.load(QUrl(_GREY_NOISE_URL))
            self._playing = True
            return
        self._send_p(view)
        self._playing = not self._playing

    def _send_p(self, view: QWebEngineView) -> None:
        """Inject a 'P' keypress into the page via JavaScript."""
        js = (
            "var e = new KeyboardEvent('keydown', "
            "  {key: 'p', code: 'KeyP', keyCode: 80, which: 80, bubbles: true});"
            "document.dispatchEvent(e);"
        )
        view.page().runJavaScript(js)

    def stop(self) -> None:
        """Pause if currently playing."""
        if self._playing:
            self.toggle()

    def cleanup(self) -> None:
        """Destroy the web view."""
        if self._view is not None:
            self._view.loadFinished.disconnect(self._on_load_finished)
            self._view.page().deleteLater()
            self._view.deleteLater()
            self._view = None
            self._loaded = False
            self._playing = False
