# src/photo_keyward/scripts/export_dates.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ._common import ExifToolError, exif_to_iso, find_files, read_exif_batch, save_json
from ._result import ScriptResult

ProgressCb = Callable[[int, int], None] | None


def run(folder: Path, progress_cb: ProgressCb = None) -> ScriptResult:
    """Export DateTimeOriginal of every ORF in ``folder`` to
    ``<folder>/dates.json``.

    JSON shape:
        { "<filename without extension>": { "date_taken": "YYYY-MM-DDTHH:MM:SS" } }
    """
    orfs = find_files(folder, (".orf",))
    total = len(orfs)
    if total == 0:
        return ScriptResult(error="no ORF files in folder")

    try:
        records = read_exif_batch(orfs, ["DateTimeOriginal"])
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    out: dict[str, dict] = {}
    warnings: list[str] = []
    processed = 0
    skipped = 0
    for i, orf in enumerate(orfs, start=1):
        rec = records.get(orf, {})
        iso = exif_to_iso(rec.get("DateTimeOriginal"))
        if iso is None:
            skipped += 1
            warnings.append(f"no DateTimeOriginal on {orf.name}")
        else:
            out[orf.stem] = {"date_taken": iso}
            processed += 1
        if progress_cb is not None:
            progress_cb(i, total)

    try:
        save_json(folder / "dates.json", out)
    except OSError as e:
        return ScriptResult(error=f"failed to write dates.json: {e}")

    return ScriptResult(
        processed=processed,
        skipped=skipped,
        warnings=tuple(warnings),
    )
