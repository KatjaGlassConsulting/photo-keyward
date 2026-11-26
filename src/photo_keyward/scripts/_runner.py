# src/photo_keyward/scripts/_runner.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from ._result import ScriptResult

ScriptFn = Callable[[Path, Callable[[int, int], None] | None], ScriptResult]


class _ScriptThread(QThread):
    progress_emitted = Signal(int, int)
    finished_ok = Signal(object)   # ScriptResult
    failed = Signal(str)

    def __init__(self, fn: ScriptFn, folder: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._folder = folder
        self._last_done = -1

    def _on_progress(self, done: int, total: int) -> None:
        # Throttle: only emit when done changes.
        if done != self._last_done:
            self._last_done = done
            self.progress_emitted.emit(done, total)

    def run(self) -> None:
        try:
            result = self._fn(self._folder, self._on_progress)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.finished_ok.emit(result)


class ScriptRunner(QObject):
    """Runs a script's ``run()`` on a background QThread and forwards
    progress / completion as Qt signals.

    Only one script may be running at a time per ScriptRunner instance.
    """

    progress = Signal(int, int)          # (done, total)
    complete = Signal(object)            # ScriptResult
    failed = Signal(str)                 # unexpected exception text

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: _ScriptThread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, fn: ScriptFn, folder: Path) -> bool:
        """Start ``fn(folder, …)`` on a background thread. Returns True if
        started, False if a script is already running."""
        if self.busy:
            return False
        t = _ScriptThread(fn, folder, self)
        t.progress_emitted.connect(self.progress)
        t.finished_ok.connect(self._on_done)
        t.failed.connect(self._on_failed)
        self._thread = t
        t.start()
        return True

    def shutdown(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(2000)

    def _on_done(self, result: object) -> None:
        self._thread = None
        self.complete.emit(result)

    def _on_failed(self, msg: str) -> None:
        self._thread = None
        self.failed.emit(msg)
