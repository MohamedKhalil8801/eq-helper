"""Per-context volume memory: one level for Grey noise, one for everything else.

Grey noise typically needs a much higher volume than tones/sweeps, so the app
remembers the two independently and switches between them automatically. Kept
Qt-free so the bucket logic can be unit-tested in isolation.
"""

from __future__ import annotations


class VolumeMemory:
    """Two remembered volumes — ``grey`` and ``regular`` — selected by context."""

    def __init__(self, regular: float, grey: float) -> None:
        self.regular = float(regular)
        self.grey = float(grey)

    def current(self, is_grey: bool) -> float:
        """The remembered volume for the active context."""
        return self.grey if is_grey else self.regular

    def remember(self, is_grey: bool, value: float) -> None:
        """Store ``value`` into the bucket for the active context."""
        if is_grey:
            self.grey = float(value)
        else:
            self.regular = float(value)
