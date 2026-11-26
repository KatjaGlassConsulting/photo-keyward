# src/photo_keyward/scripts/import_dates.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ._common import (
    ExifToolError,
    WriteOp,
    find_files,
    iso_to_exif,
    load_json,
    write_exif_batch,
)
from ._result import ScriptResult

ProgressCb = Callable[[int, int], None] | None


def run(folder: Path, progress_cb: ProgressCb = None) -> ScriptResult:
    """Read ``<folder>/dates.json`` and write DateTimeOriginal +
    FileCreateDate to same-stem JPGs in ``folder``.

    Dates always overwrite existing values."""
    json_path = folder / "dates.json"
    if not json_path.is_file():
        return ScriptResult(error=f"{json_path.name} not found in {folder}")

    try:
        data = load_json(json_path)
    except (OSError, json.JSONDecodeError) as e:
        return ScriptResult(error=f"failed to read {json_path.name}: {e}")
    if not isinstance(data, dict):
        return ScriptResult(error=f"{json_path.name}: top-level must be an object")

    jpgs = find_files(folder, (".jpg", ".jpeg"))
    if not jpgs:
        return ScriptResult(error="no JPG files in folder")

    ops: list[WriteOp] = []
    warnings: list[str] = []
    skipped = 0
    for jpg in jpgs:
        entry = data.get(jpg.stem)
        if not isinstance(entry, dict):
            skipped += 1
            warnings.append(f"no entry in dates.json for {jpg.name}")
            continue
        exif_dt = iso_to_exif(entry.get("date_taken"))
        if exif_dt is None:
            skipped += 1
            warnings.append(f"invalid date_taken in dates.json for {jpg.name}")
            continue
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

    total = len(ops)
    for i, op in enumerate(ops, start=1):
        if progress_cb is not None:
            progress_cb(i, total)
    for path, msg in wr.errors:
        warnings.append(f"exiftool failed for {path.name}: {msg}")

    return ScriptResult(
        processed=len(wr.written),
        skipped=skipped + len(wr.errors),
        warnings=tuple(warnings),
    )
