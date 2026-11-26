from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class WarningEntry:
    when: datetime
    source: str
    message: str


class WarningLog(QObject):
    """In-memory, session-only log of warnings shown in the status-bar
    indicator. Cleared when the user clicks Clear in WarningDialog."""

    warning_added = Signal(int)  # new count

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[WarningEntry] = []

    def add(self, message: str, source: str = "") -> None:
        self._entries.append(WarningEntry(datetime.now(), source, message))
        self.warning_added.emit(len(self._entries))

    def entries(self) -> list[WarningEntry]:
        return list(self._entries)

    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self.warning_added.emit(0)
