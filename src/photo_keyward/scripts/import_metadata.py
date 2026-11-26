# src/photo_keyward/scripts/import_metadata.py
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
    read_exif_batch,
    write_exif_batch,
)
from ._result import ScriptResult

ProgressCb = Callable[[int, int], None] | None


def _coerce_str_list(value, file_name: str, field_name: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{file_name}: {field_name} not a list, treating as empty")
        return []
    out: list[str] = []
    for v in value:
        if isinstance(v, str) and v:
            out.append(v)
        else:
            warnings.append(f"{file_name}: skipping non-string {field_name} entry")
    return out


def _existing_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def _new_only(existing: list[str], incoming: list[str]) -> list[str]:
    """Items in ``incoming`` not already present in ``existing``."""
    seen = set(existing)
    out: list[str] = []
    for kw in incoming:
        if kw not in seen:
            out.append(kw)
            seen.add(kw)
    return out


def run(folder: Path, progress_cb: ProgressCb = None) -> ScriptResult:
    """Read ``<folder>/metadata.json`` and write back to same-stem JPGs.

    - ``date_taken``: overwrites DateTimeOriginal + FileCreateDate (skipped if null)
    - ``keywords``:    additive union with existing Keywords + Subject
    - ``hierarchical``: additive union with existing HierarchicalSubject
    """
    json_path = folder / "metadata.json"
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

    # Pre-read existing keywords + hierarchy so we can compute additive union.
    try:
        existing = read_exif_batch(
            jpgs, ["Keywords", "Subject", "XMP-lr:HierarchicalSubject"]
        )
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    warnings: list[str] = []
    skipped = 0
    ops: list[WriteOp] = []
    for jpg in jpgs:
        entry = data.get(jpg.stem)
        if not isinstance(entry, dict):
            skipped += 1
            warnings.append(f"no entry in metadata.json for {jpg.name}")
            continue

        tag_args: list[str] = []

        # Date: optional. Null/missing → skip date but still process keywords.
        date_iso = entry.get("date_taken")
        if date_iso is not None:
            exif_dt = iso_to_exif(date_iso)
            if exif_dt is None:
                warnings.append(f"invalid date_taken for {jpg.name}, skipping date")
            else:
                tag_args.append(f"-DateTimeOriginal={exif_dt}")
                tag_args.append(f"-FileCreateDate={exif_dt}")

        incoming_kw = _coerce_str_list(entry.get("keywords"), jpg.name, "keywords", warnings)
        incoming_hier = _coerce_str_list(
            entry.get("hierarchical"), jpg.name, "hierarchical", warnings
        )

        existing_rec = existing.get(jpg, {})
        existing_kw = (
            _existing_list(existing_rec.get("Keywords"))
            or _existing_list(existing_rec.get("Subject"))
        )
        existing_hier = _existing_list(existing_rec.get("HierarchicalSubject"))

        # Additive: only emit += for keywords not already present.
        for kw in _new_only(existing_kw, incoming_kw):
            tag_args.append(f"-IPTC:Keywords+={kw}")
            tag_args.append(f"-XMP:Subject+={kw}")
        for h in _new_only(existing_hier, incoming_hier):
            tag_args.append(f"-XMP-lr:HierarchicalSubject+={h}")

        if not tag_args:
            skipped += 1
            warnings.append(f"nothing to write for {jpg.name} (already up to date)")
            continue
        ops.append(WriteOp(path=jpg, tag_args=tuple(tag_args)))

    try:
        wr = write_exif_batch(ops)
    except ExifToolError as e:
        return ScriptResult(error=str(e))

    total = len(ops)
    for i in range(1, total + 1):
        if progress_cb is not None:
            progress_cb(i, total)
    for path, msg in wr.errors:
        warnings.append(f"exiftool failed for {path.name}: {msg}")

    return ScriptResult(
        processed=len(wr.written),
        skipped=skipped + len(wr.errors),
        warnings=tuple(warnings),
    )
