from __future__ import annotations

import os

from PySide6.QtCore import QObject, QTimer, Signal

from .constants import SUPPORTED_EXTENSIONS
from .exif_cache import ExifCache
from .exif_reader import ExifReader


def folder_image_paths(folder: str | None) -> list[str]:
    """Every supported image directly in ``folder``, spelled the way
    ``ExifCache`` is keyed.

    The cache is keyed by the exact path string the grid model used to
    populate it, so anything looking an image up has to build the path the
    same way ``ImageFolderModel.set_folder`` does — ``os.path.join`` over
    ``os.listdir``, filtered by extension. Three callers needed that
    construction and each carried its own copy; this is the one they share.

    Subfolders are deliberately not walked: the panel, the Keys tab and the
    grid all operate on one folder's images, and an import that reached
    further would put keywords in the vocabulary that the panel it came from
    cannot explain.
    """
    if not folder:
        return []
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    out: list[str] = []
    for name in entries:
        ext = os.path.splitext(name)[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            out.append(os.path.join(folder, name))
    return out


def keywords_in_cache(cache: ExifCache, paths: list[str]) -> set[str]:
    """Flat and hierarchical keywords carried by whichever of ``paths`` the
    cache currently holds. Uncached paths contribute nothing rather than
    raising, so callers can use this before and after a read alike."""
    found: set[str] = set()
    for path in paths:
        data = cache.get(path)
        if data is None:
            continue
        found |= set(data.keywords) | set(data.hierarchical)
    return found


class KeywordImporter(QObject):
    """Collects every keyword carried by the images directly in one folder,
    reading the ones not already cached.

    Owns its own ``ExifReader`` rather than sharing the application's. The
    shared one drops a waiting request in favour of the newest, which is right
    for folder browsing and wrong here: a folder change mid-import would
    silently cancel the import, or the import would cancel the folder's read.

    Flat and hierarchical keywords both come back, as separate entries. In
    this app they are separate: ticking ``shark`` writes the flat field and
    ``fish|shark`` the hierarchical one, so collapsing them would lose the
    distinction the vocabulary exists to record.
    """

    finished = Signal(set)  # set[str]
    failed = Signal(str)

    def __init__(self, cache: ExifCache, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._reader = ExifReader(self)
        self._reader.read_complete.connect(self._on_read_complete)
        self._reader.read_failed.connect(self._on_read_failed)
        self._paths: list[str] = []
        self._cancelled = False

    def start(self, folder: str) -> int:
        """Begin collecting. Returns how many images have to be read from
        disk first, so the caller can decide whether to show progress at all.

        Either way the result arrives via ``finished`` on a later turn of the
        event loop, never from inside this call — a caller that puts a
        progress dialog up after calling ``start`` cannot miss it.
        """
        self._cancelled = False
        self._paths = folder_image_paths(folder)
        unread = [p for p in self._paths if self._cache.get(p) is None]
        if not unread:
            QTimer.singleShot(0, self._emit_found)
            return 0
        self._reader.read_paths(unread)
        return len(unread)

    def cancel(self) -> None:
        """Stop caring about the result.

        The exiftool pass already running is left to finish: killing it
        mid-file buys nothing, and its results still reach the cache, where
        the folder's own read would have put them anyway.
        """
        self._cancelled = True

    def shutdown(self) -> None:
        """Terminal. Waits out a running read so the thread cannot outlive the
        widget that owns it."""
        self._cancelled = True
        self._reader.shutdown()

    # -- reader slots --------------------------------------------------
    def _on_read_complete(self, results: dict) -> None:
        # Cached even when cancelled: the work is done and paid for, and the
        # grid and Keys tab want it regardless of what this import does next.
        if results:
            self._cache.put_many(results)
        self._emit_found()

    def _on_read_failed(self, message: str) -> None:
        if self._cancelled:
            return
        self.failed.emit(message)

    def _emit_found(self) -> None:
        if self._cancelled:
            return
        self.finished.emit(keywords_in_cache(self._cache, self._paths))
