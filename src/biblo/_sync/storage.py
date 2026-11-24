# Generated from src/biblo/_async by scripts/generate.py. Do not edit.
"""Where a client keeps its session (tokens, library, installation id) between runs."""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Self

from .._base import default_session_path


class TokenStorage(ABC):
    """Implement this to keep sessions somewhere else, e.g. Home Assistant's Store."""

    @abstractmethod
    def load(self) -> dict[str, Any] | None: ...

    @abstractmethod
    def save(self, data: dict[str, Any]) -> None: ...


class MemoryTokenStorage(TokenStorage):
    """Keeps the session for the life of the object."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data

    def load(self) -> dict[str, Any] | None:
        return self.data

    def save(self, data: dict[str, Any]) -> None:
        self.data = data


class FileTokenStorage(TokenStorage):
    """A JSON file, written atomically and readable only by its owner."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    @classmethod
    def default(cls) -> Self:
        """The session file the CLI uses (`biblo.default_session_path()`), to reuse its login."""
        return cls(default_session_path())

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp creates the file readable and writable only by its owner (0600).
        fd, name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
