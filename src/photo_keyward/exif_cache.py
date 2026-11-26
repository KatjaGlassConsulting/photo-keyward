from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class ExifData:
    date_taken: datetime | None
    keywords: tuple[str, ...] = ()
    hierarchical: tuple[str, ...] = ()


class ExifCache(QObject):
    """In-memory EXIF cache keyed by path; entries are invalidated when the
    file's mtime changes since the value was stored."""

    cache_updated = Signal(set)  # set[str] of paths just put

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store: dict[str, tuple[int, ExifData]] = {}

    def get(self, path: str) -> ExifData | None:
        entry = self._store.get(path)
        if entry is None:
            return None
        stored_mtime, data = entry
        try:
            current_mtime = os.stat(path).st_mtime_ns
        except OSError:
            return None
        if current_mtime != stored_mtime:
            return None
        return data

    def put_many(self, data: dict[str, ExifData]) -> None:
        updated: set[str] = set()
        for path, ed in data.items():
            try:
                mtime_ns = os.stat(path).st_mtime_ns
            except OSError:
                continue
            self._store[path] = (mtime_ns, ed)
            updated.add(path)
        if updated:
            self.cache_updated.emit(updated)

    def refresh_mtime(self, paths: list[str]) -> None:
        """Re-stat each path and update the stored mtime without touching
        the cached ExifData. Used right after a write so the cache survives
        exiftool's mtime bump."""
        for path in paths:
            entry = self._store.get(path)
            if entry is None:
                continue
            _, data = entry
            try:
                new_mtime = os.stat(path).st_mtime_ns
            except OSError:
                continue
            self._store[path] = (new_mtime, data)
