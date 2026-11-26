# src/photo_keyward/scripts/export_metadata.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ._common import ExifToolError, exif_to_iso, find_files, read_exif_batch, save_json
from ._result import ScriptResult

ProgressCb = Callable[[int, int], None] | None


def _coerce_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def run(folder: Path, progress_cb: ProgressCb = None) -> ScriptResult:
    """Export DateTimeOriginal + Keywords (with Subject fallback) +
    HierarchicalSubject for every JPG in ``folder`` to
    ``<folder>/metadata.json``."""
    jpgs = find_files(folder, (".jpg", ".jpeg"))
    total = len(jpgs)
    if total == 0:
        return ScriptResult(error="no JPG files in folder")

    try:
        records = read_exif_batch(
            jpgs,
            ["DateTimeOriginal", "Keywords", "Subject", "XMP-lr:HierarchicalSubject"],
        )
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    out: dict[str, dict] = {}
    for i, jpg in enumerate(jpgs, start=1):
        rec = records.get(jpg, {})
        kw = _coerce_list(rec.get("Keywords")) or _coerce_list(rec.get("Subject"))
        hier = _coerce_list(rec.get("HierarchicalSubject"))
        out[jpg.stem] = {
            "date_taken": exif_to_iso(rec.get("DateTimeOriginal")),
            "keywords": kw,
            "hierarchical": hier,
        }
        if progress_cb is not None:
            progress_cb(i, total)

    try:
        save_json(folder / "metadata.json", out)
    except OSError as e:
        return ScriptResult(error=f"failed to write metadata.json: {e}")

    return ScriptResult(processed=total)
