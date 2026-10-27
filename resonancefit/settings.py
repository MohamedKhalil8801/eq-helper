"""The customer app's own settings file (never ``~/.eq_helper``).

A flat JSON object in the data folder. Unknown keys are kept; a missing or
corrupt file means "defaults". Writes are atomic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data = data
        except (OSError, ValueError):
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def update(self, **values) -> None:
        self._data.update(values)
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # settings are a convenience; never fail the app over them
