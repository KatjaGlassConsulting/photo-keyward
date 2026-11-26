from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QPixmap

from .constants import cache_dir

log = logging.getLogger(__name__)

MEMORY_CACHE_PER_SIZE = 500


@dataclass(frozen=True)
class ThumbKey:
    path: str
    mtime_ns: int
    size: int


class _Signals(QObject):
    ready = Signal(str, int, QPixmap)  # path, size, pixmap
    failed = Signal(str, int)           # path, size


class _Worker(QRunnable):
    def __init__(self, key: ThumbKey, signals: _Signals) -> None:
        super().__init__()
        self.key = key
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        key = self.key
        disk_path = _disk_path(key)
        try:
            if disk_path.exists():
                pm = QPixmap(str(disk_path))
                if not pm.isNull():
                    self.signals.ready.emit(key.path, key.size, pm)
                    return

            with Image.open(key.path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((key.size, key.size), Image.Resampling.LANCZOS)
                im = im.convert("RGBA")
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                im.save(disk_path, format="PNG")

            pm = QPixmap(str(disk_path))
            if pm.isNull():
                self.signals.failed.emit(key.path, key.size)
                return
            self.signals.ready.emit(key.path, key.size, pm)
        except (UnidentifiedImageError, OSError, ValueError) as e:
            log.warning("thumb fail %s @ %s: %s", key.path, key.size, e)
            self.signals.failed.emit(key.path, key.size)


def _disk_path(key: ThumbKey) -> Path:
    digest = hashlib.sha1(key.path.encode("utf-8")).hexdigest()
    return cache_dir() / str(key.size) / f"{digest}_{key.mtime_ns}.png"


class ThumbnailCache(QObject):
    """Two-tier (memory + disk) thumbnail cache backed by Pillow workers."""

    ready = Signal(str, int, QPixmap)
    failed = Signal(str, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._memory: dict[int, OrderedDict[str, QPixmap]] = {}
        self._inflight: set[tuple[str, int]] = set()
        self._signals = _Signals()
        self._signals.ready.connect(self._on_ready)
        self._signals.failed.connect(self._on_failed)
        self._pool = QThreadPool.globalInstance()

    def get(self, path: str, size: int) -> QPixmap | None:
        bucket = self._memory.setdefault(size, OrderedDict())
        pm = bucket.get(path)
        if pm is not None:
            bucket.move_to_end(path)
            return pm
        return None

    def request(self, path: str, size: int) -> None:
        if (path, size) in self._inflight:
            return
        try:
            mtime_ns = Path(path).stat().st_mtime_ns
        except OSError:
            self.failed.emit(path, size)
            return
        self._inflight.add((path, size))
        self._pool.start(_Worker(ThumbKey(path, mtime_ns, size), self._signals))

    def _on_ready(self, path: str, size: int, pm: QPixmap) -> None:
        self._inflight.discard((path, size))
        bucket = self._memory.setdefault(size, OrderedDict())
        bucket[path] = pm
        bucket.move_to_end(path)
        while len(bucket) > MEMORY_CACHE_PER_SIZE:
            bucket.popitem(last=False)
        self.ready.emit(path, size, pm)

    def _on_failed(self, path: str, size: int) -> None:
        self._inflight.discard((path, size))
        self.failed.emit(path, size)
