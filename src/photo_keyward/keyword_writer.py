from __future__ import annotations

import queue
import subprocess
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from .exiftool_util import (
    EXIFTOOL_MISSING_MESSAGE,
    exiftool_argfile,
    find_exiftool,
)


@dataclass(frozen=True)
class WriteOp:
    op_id: int
    kind: str  # "assign" or "unassign"
    keyword: str
    paths: tuple[str, ...]


class _WriterThread(QThread):
    op_started = Signal(int, str, int)
    op_complete = Signal(int, str, list)
    op_failed = Signal(int, str, list, str)

    def __init__(
        self, op_queue: queue.Queue, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._queue = op_queue
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True
        self._queue.put(None)  # sentinel to wake the run loop

    def run(self) -> None:
        while True:
            op = self._queue.get()
            if op is None or self._stopping:
                return
            self.op_started.emit(op.op_id, op.keyword, len(op.paths))
            try:
                self._run_exiftool(op)
            except _WriteError as e:
                self.op_failed.emit(op.op_id, op.keyword, list(op.paths), str(e))
            else:
                self.op_complete.emit(op.op_id, op.keyword, list(op.paths))

    def _run_exiftool(self, op: WriteOp) -> None:
        exiftool_path = find_exiftool()
        if exiftool_path is None:
            raise _WriteError(EXIFTOOL_MISSING_MESSAGE)

        op_char = "+=" if op.kind == "assign" else "-="
        is_hierarchy = "|" in op.keyword
        if is_hierarchy:
            tag_args = [f"-XMP-lr:HierarchicalSubject{op_char}{op.keyword}"]
        else:
            tag_args = [
                f"-IPTC:Keywords{op_char}{op.keyword}",
                f"-XMP:Subject{op_char}{op.keyword}",
            ]

        # The keyword goes in the argfile, not on the command line: argv
        # reaches exiftool through the Windows ANSI code page, which turns
        # "Ägypten" into "?gypten" or an empty string while still exiting 0.
        # See exiftool_argfile.
        try:
            with exiftool_argfile(
                [*tag_args, *op.paths], prefix="keyward_kw_"
            ) as argfile_path:
                proc = subprocess.run(
                    [
                        exiftool_path,
                        "-overwrite_original",
                        "-charset", "filename=utf8",
                        "-charset", "utf8",
                        "-@", argfile_path,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
        except OSError as e:
            raise _WriteError(f"exiftool subprocess failed: {e}")

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()[:200]
            raise _WriteError(f"exiftool failed: {stderr}")


class _WriteError(Exception):
    pass


class KeywordWriter(QObject):
    """Background writer that runs exiftool to add or remove a keyword on a
    batch of paths. Operations are processed sequentially on a single
    background QThread."""

    write_started = Signal(int, str, int)
    write_complete = Signal(int, str, list)
    write_failed = Signal(int, str, list, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: queue.Queue = queue.Queue()
        self._next_op_id = 0
        self._thread = _WriterThread(self._queue, self)
        self._thread.op_started.connect(self.write_started)
        self._thread.op_complete.connect(self.write_complete)
        self._thread.op_failed.connect(self.write_failed)
        self._thread.start()

    def assign(self, keyword: str, paths: list[str]) -> int:
        return self._enqueue("assign", keyword, paths)

    def unassign(self, keyword: str, paths: list[str]) -> int:
        return self._enqueue("unassign", keyword, paths)

    def _enqueue(self, kind: str, keyword: str, paths: list[str]) -> int:
        op_id = self._next_op_id
        self._next_op_id += 1
        self._queue.put(WriteOp(op_id, kind, keyword, tuple(paths)))
        return op_id

    def shutdown(self) -> None:
        self._thread.stop()
        self._thread.wait(2000)
