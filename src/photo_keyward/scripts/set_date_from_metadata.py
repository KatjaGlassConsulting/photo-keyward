# src/photo_keyward/scripts/set_date_from_metadata.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ._common import (
    ExifToolError,
    WriteOp,
    exif_to_iso,
    find_files,
    iso_to_exif,
    read_exif_batch,
    write_exif_batch,
)
from ._result import ScriptResult

ProgressCb = Callable[[int, int], None] | None


def run(folder: Path, progress_cb: ProgressCb = None) -> ScriptResult:
    """Set every JPG's FileCreateDate from its DateTimeOriginal EXIF tag.

    The inverse of set_date_from_filename: here the EXIF tag is the source of
    truth and the filesystem is the destination.

    Files without a readable DateTimeOriginal are skipped with a warning — no
    fallback to other date tags. Files whose FileCreateDate already matches
    are skipped too, which makes a second run a no-op.
    """
    jpgs = find_files(folder, (".jpg", ".jpeg"))
    if not jpgs:
        return ScriptResult(error="no JPG files in folder")

    # FileCreateDate is read in the same batch purely to support the
    # already-correct check, so this stays one read invocation, not two.
    try:
        records = read_exif_batch(jpgs, ["DateTimeOriginal", "FileCreateDate"])
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    warnings: list[str] = []
    skipped = 0
    ops: list[WriteOp] = []

    for jpg in jpgs:
        rec = records.get(jpg, {})
        raw = rec.get("DateTimeOriginal")
        taken_iso = exif_to_iso(raw)
        if taken_iso is None:
            # Absent and unparseable need different fixes from the user, so
            # they get different messages.
            if raw:
                warnings.append(
                    f"unreadable DateTimeOriginal '{raw}' on {jpg.name}"
                )
            else:
                warnings.append(f"no DateTimeOriginal on {jpg.name}")
            skipped += 1
            continue

        current_iso = exif_to_iso(rec.get("FileCreateDate"))
        if current_iso == taken_iso:
            warnings.append(
                f"FileCreateDate already matches DateTimeOriginal for {jpg.name}"
            )
            skipped += 1
            continue

        exif_dt = iso_to_exif(taken_iso)
        ops.append(WriteOp(path=jpg, tag_args=(f"-FileCreateDate={exif_dt}",)))

    written = 0
    for i, op in enumerate(ops, start=1):
        # write_exif_batch already runs one exiftool process per WriteOp
        # internally, so calling it per op is behaviourally identical and
        # lets progress reflect the actual work. ExifToolError here means the
        # binary is missing — a whole-run failure, not a per-file one.
        try:
            wr = write_exif_batch([op])
        except ExifToolError as e:
            return ScriptResult(error=str(e))
        written += len(wr.written)
        for path, msg in wr.errors:
            warnings.append(f"exiftool failed for {path.name}: {msg}")
            skipped += 1
        if progress_cb is not None:
            progress_cb(i, len(ops))

    return ScriptResult(
        processed=written,
        skipped=skipped,
        warnings=tuple(warnings),
    )
