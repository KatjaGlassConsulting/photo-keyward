from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime

from PySide6.QtCore import QObject, QThread, Signal

from .exif_cache import ExifData
from .exiftool_util import EXIFTOOL_MISSING_MESSAGE, find_exiftool


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # exiftool format: "YYYY:MM:DD HH:MM:SS" (may have trailing tz/subsec)
        return datetime.strptime(value[:19], "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _coerce_keywords(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list):
        return tuple(str(v) for v in value if v)
    return ()


class _ReaderThread(QThread):
    finished_ok = Signal(dict)  # dict[str, ExifData]
    failed = Signal(str)

    def __init__(self, paths: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._paths = paths

    def run(self) -> None:
        if not self._paths:
            self.finished_ok.emit({})
            return
        exiftool_path = find_exiftool()
        if exiftool_path is None:
            self.failed.emit(EXIFTOOL_MISSING_MESSAGE + " Then restart the app.")
            return
        # Pass paths via a temp argfile so we don't hit the Windows
        # command-line length limit (ERROR_FILENAME_EXCED_RANGE) on folders
        # with many or long-named files.
        argfile_path: str | None = None
        proc: subprocess.CompletedProcess[str] | None = None
        try:
            argfile_fd, argfile_path = tempfile.mkstemp(
                prefix="keyward_", suffix=".args"
            )
            with os.fdopen(argfile_fd, "w", encoding="utf-8", newline="\n") as f:
                for p in self._paths:
                    f.write(p)
                    f.write("\n")
            proc = subprocess.run(
                [
                    exiftool_path,
                    "-charset", "filename=utf8",
                    "-charset", "utf8",
                    "-j",
                    "-DateTimeOriginal",
                    "-Keywords",
                    "-Subject",
                    "-XMP-lr:HierarchicalSubject",
                    "-@", argfile_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as e:
            self.failed.emit(f"exiftool subprocess failed: {e}")
            return
        finally:
            if argfile_path is not None:
                try:
                    os.unlink(argfile_path)
                except OSError:
                    pass
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()[:200]
            self.failed.emit(f"exiftool failed: {stderr}")
            return
        try:
            records = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as e:
            self.failed.emit(f"exiftool returned invalid JSON: {e}")
            return

        # exiftool normalizes Windows backslashes to forward slashes in
        # SourceFile. Map back to the exact path string we passed in.
        norm_to_original = {p.replace("\\", "/"): p for p in self._paths}
        results: dict[str, ExifData] = {}
        for rec in records:
            src = rec.get("SourceFile")
            if not src:
                continue
            orig = norm_to_original.get(src, src)
            results[orig] = ExifData(
                date_taken=_parse_datetime(rec.get("DateTimeOriginal")),
                keywords=_coerce_keywords(
                    rec.get("Keywords") or rec.get("Subject")
                ),
                hierarchical=_coerce_keywords(rec.get("HierarchicalSubject")),
            )
        self.finished_ok.emit(results)


class ExifReader(QObject):
    """Runs exiftool on a batch of paths in a one-shot QThread.

    A request arriving while a read is running replaces any other waiting
    request rather than queueing behind it: when the user clicks through five
    folders, only the one they landed on is worth reading, and a real queue
    would make that folder wait behind four they already left.
    """

    read_complete = Signal(dict)  # dict[str, ExifData]
    read_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: _ReaderThread | None = None
        self._pending: list[str] | None = None
        self._shutdown = False

    @property
    def busy(self) -> bool:
        # Deliberately not isRunning(): that goes False as soon as run()
        # returns, while this thread's queued _drain has yet to hand off any
        # pending request. Ownership ends in _drain, so that is what busy
        # must track.
        return self._thread is not None

    def read_paths(self, paths: list[str]) -> None:
        if self._shutdown or not paths:
            return
        if self.busy:
            self._pending = list(paths)  # newest wins
            return
        self._start(list(paths))

    def _start(self, paths: list[str]) -> None:
        thread = _ReaderThread(paths, self)
        thread.finished_ok.connect(self.read_complete)
        thread.failed.connect(self.read_failed)
        # finished, not finished_ok: finished_ok is emitted from inside run(),
        # while the thread is still running, so draining there would find
        # busy still True and drop the queued read.
        #
        # Bound to `thread` explicitly rather than read back via
        # self._thread: this is a queued call, and busy only guards
        # read_paths(). Anything that calls _start() directly instead —
        # e.g. a test bypassing the busy gate — can leave self._thread
        # pointing at a different thread by the time this runs. Draining
        # by identity means this call only ever tears down the thread it
        # belongs to.
        thread.finished.connect(lambda t=thread: self._drain(t))
        # The thread is parented to this object and was previously never
        # freed. That leaked slowly when a read only happened on Ctrl+E; with
        # a read per folder change the child list would grow all session.
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _drain(self, thread: _ReaderThread) -> None:
        if self._thread is not thread:
            return  # a newer read already took ownership; it will drain _pending
        self._thread = None
        pending, self._pending = self._pending, None
        if pending:
            self._start(pending)

    def shutdown(self) -> None:
        """Terminal: clear any pending request, wait for a running read to
        finish, and stop accepting new ones.

        Mirrors KeywordWriter.shutdown(): idle is a harmless no-op, and a
        running thread is bounded so the app can still exit if exiftool
        hangs. busy tracks self._thread rather than isRunning(), and the
        only thing that normally clears it is a matching _drain() — which
        is a queued call that needs the event loop to run again to be
        delivered. We may be shutting the event loop down right now, so
        this clears self._thread directly instead of waiting on that
        delivery. That means a timed-out wait() (exiftool still hanging)
        leaves self._thread cleared while the abandoned thread is still
        alive; without the _shutdown flag, a later read_paths() would see
        busy=False and start a second thread alongside it, and since the
        result relay (finished_ok/failed -> read_complete/read_failed) is
        wired per-thread rather than identity-gated like _drain is,
        whichever of the two finished last would win with a stale result.
        The flag makes read_paths() a no-op after shutdown() regardless of
        whether the wait actually completed, so that can't happen.
        """
        self._shutdown = True
        self._pending = None
        if self._thread is not None:
            self._thread.wait(2000)
            self._thread = None
