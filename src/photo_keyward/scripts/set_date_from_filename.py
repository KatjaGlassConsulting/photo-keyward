# src/photo_keyward/scripts/set_date_from_filename.py
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from ._common import (
    ExifToolError,
    WriteOp,
    exif_to_iso,
    find_files,
    read_exif_batch,
    write_exif_batch,
)
from ._result import ScriptResult

ProgressCb = Callable[[int, int], None] | None

_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[-_. ]|$)")


def _parse_prefix(stem: str) -> datetime | None:
    m = _DATE_PREFIX.match(stem)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0, 0)
    except ValueError:
        return None


def run(folder: Path, progress_cb: ProgressCb = None) -> ScriptResult:
    """For every JPG whose name starts with ``YYYY-MM-DD`` (followed by a
    separator or end of stem), set DateTimeOriginal + FileCreateDate to
    ``YYYY:MM:DD 12:00:00``.

    Idempotence: if the file's existing FileCreateDate's calendar date
    already equals the parsed date, skip with a warning."""
    jpgs = find_files(folder, (".jpg", ".jpeg"))
    total = len(jpgs)
    if total == 0:
        return ScriptResult(error="no JPG files in folder")

    try:
        existing = read_exif_batch(jpgs, ["FileCreateDate"])
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    warnings: list[str] = []
    skipped = 0
    ops: list[WriteOp] = []

    for jpg in jpgs:
        parsed = _parse_prefix(jpg.stem)
        if parsed is None:
            # Match a date-shaped prefix but invalid date? Distinguish.
            if _DATE_PREFIX.match(jpg.stem) is not None:
                warnings.append(f"invalid calendar date in name of {jpg.name}")
            else:
                warnings.append(f"no date prefix on {jpg.name}")
            skipped += 1
            continue

        cur_iso = exif_to_iso(existing.get(jpg, {}).get("FileCreateDate"))
        if cur_iso is not None and cur_iso[:10] == parsed.date().isoformat():
            warnings.append(
                f"FileCreateDate already on {parsed.date().isoformat()} for {jpg.name}"
            )
            skipped += 1
            continue

        exif_dt = parsed.strftime("%Y:%m:%d %H:%M:%S")
        ops.append(WriteOp(
            path=jpg,
            tag_args=(
                f"-DateTimeOriginal={exif_dt}",
                f"-FileCreateDate={exif_dt}",
            ),
        ))

    try:
        wr = write_exif_batch(ops)
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    for i in range(1, len(ops) + 1):
        if progress_cb is not None:
            progress_cb(i, len(ops))
    for path, msg in wr.errors:
        warnings.append(f"exiftool failed for {path.name}: {msg}")

    return ScriptResult(
        processed=len(wr.written),
        skipped=skipped + len(wr.errors),
        warnings=tuple(warnings),
    )
