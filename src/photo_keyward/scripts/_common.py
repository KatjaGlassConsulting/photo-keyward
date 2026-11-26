# src/photo_keyward/scripts/_common.py
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..exiftool_util import (
    EXIFTOOL_MISSING_MESSAGE,
    exiftool_argfile,
    find_exiftool,
)


def find_files(folder: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Return files in ``folder`` (non-recursive) whose suffix matches one
    of ``extensions`` (case-insensitive, leading dot included).

    Sorted by filename (stem then suffix), case-insensitive.
    """
    if not folder.is_dir():
        return []
    lower_exts = tuple(e.lower() for e in extensions)
    matches = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in lower_exts
    ]
    matches.sort(key=lambda p: (p.stem.lower(), p.suffix.lower()))
    return matches


def load_json(path: Path) -> Any:
    """Read ``path`` as UTF-8 JSON. Raises FileNotFoundError or
    json.JSONDecodeError on failure; callers handle them."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    """Write ``data`` as pretty UTF-8 JSON with sorted top-level keys
    (stable diffs across runs)."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def exif_to_iso(value: str | None) -> str | None:
    """Convert exiftool 'YYYY:MM:DD HH:MM:SS' (possibly with subsec/tz)
    to ISO 'YYYY-MM-DDTHH:MM:SS'. Returns None if value is falsy or
    unparseable."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value[:19], "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return dt.isoformat(timespec="seconds")


def iso_to_exif(value: str | None) -> str | None:
    """Convert ISO 'YYYY-MM-DDTHH:MM:SS' (or 'YYYY-MM-DD HH:MM:SS') to
    exiftool 'YYYY:MM:DD HH:MM:SS'. Returns None if unparseable."""
    if not value:
        return None
    text = value.replace("T", " ")
    try:
        dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return dt.strftime("%Y:%m:%d %H:%M:%S")


class ExifToolError(Exception):
    """Raised by read_exif_batch when exiftool cannot be invoked or its
    batch run fails entirely. Per-file failures during a write are NOT
    raised — they are returned via WriteResult.errors."""


@dataclass(frozen=True)
class WriteOp:
    """A single exiftool invocation: one file plus a list of pre-formatted
    tag arguments. Callers are responsible for choosing ``=`` (replace) or
    ``+=`` (add to list) and embedding the value correctly.

    Examples:
        WriteOp(path=Path("a.jpg"), tag_args=("-DateTimeOriginal=2025:10:23 12:00:00",))
        WriteOp(path=Path("a.jpg"), tag_args=("-IPTC:Keywords+=Tauchen", "-IPTC:Keywords+=Aegypten"))
    """

    path: Path
    tag_args: tuple[str, ...]


@dataclass(frozen=True)
class WriteResult:
    written: tuple[Path, ...]
    errors: tuple[tuple[Path, str], ...]   # (path, error message)


def read_exif_batch(paths: list[Path], fields: list[str]) -> dict[Path, dict]:
    """Batch-read EXIF/XMP fields via a single exiftool call. Uses an
    argfile to bypass the Windows command-line length limit.

    Returns ``{path: {field_name_without_group: value or None}}``. Missing
    fields appear as None. ``paths`` are returned with the exact Path
    object passed in (not exiftool's normalized SourceFile).

    Raises ExifToolError on global failure (binary missing, JSON parse
    error, non-zero exit). Per-file missing data is normal and not an
    error — just maps to None values.
    """
    if not paths:
        return {}
    exe = find_exiftool()
    if exe is None:
        raise ExifToolError(EXIFTOOL_MISSING_MESSAGE)

    field_args = [f"-{name}" for name in fields]
    with exiftool_argfile(
        [*field_args, *(str(p) for p in paths)], prefix="keyward_scripts_"
    ) as argfile_path:
        proc = subprocess.run(
            [
                exe,
                "-charset", "filename=utf8",
                "-charset", "utf8",
                "-j",
                "-@", argfile_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    if proc.returncode != 0:
        raise ExifToolError(
            f"exiftool batch read failed (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:200]}"
        )
    try:
        records = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as e:
        raise ExifToolError(f"exiftool returned invalid JSON: {e}")

    # exiftool reports SourceFile with forward slashes — map back.
    norm_to_path = {str(p).replace("\\", "/"): p for p in paths}

    # Field name as user supplied may carry a group prefix
    # (e.g. "XMP-lr:HierarchicalSubject"); JSON output uses the bare tag
    # name. Build a normalized lookup so callers get consistent keys.
    def short_name(field: str) -> str:
        return field.split(":")[-1]

    short_fields = [short_name(f) for f in fields]
    result: dict[Path, dict] = {}
    for rec in records:
        src = rec.get("SourceFile")
        if not src:
            continue
        path_obj = norm_to_path.get(src)
        if path_obj is None:
            continue
        result[path_obj] = {sn: rec.get(sn) for sn in short_fields}
    # Ensure every input path has an entry (even if exiftool dropped it).
    for p in paths:
        result.setdefault(p, {sn: None for sn in short_fields})
    return result


def write_exif_batch(ops: list[WriteOp]) -> WriteResult:
    """Run one exiftool call per file with the requested tag values.
    Uses ``-overwrite_original`` (no _original.bak left behind).

    Per-file errors are collected and returned. The function never raises
    for per-file failures; ExifToolError is raised only when exiftool
    itself is missing.
    """
    if not ops:
        return WriteResult(written=(), errors=())
    exe = find_exiftool()
    if exe is None:
        raise ExifToolError(EXIFTOOL_MISSING_MESSAGE)

    written: list[Path] = []
    errors: list[tuple[Path, str]] = []
    for op in ops:
        if not op.tag_args:
            continue
        # Both the tag values and the path go in the argfile. On Windows argv
        # reaches exiftool through the ANSI code page, which silently destroys
        # non-ASCII in either -- an imported keyword like "Ägypten" is written
        # as an empty string, and a file under a folder like "2025-10 Ägypten"
        # is not found at all. See exiftool_argfile.
        try:
            with exiftool_argfile(
                [*op.tag_args, str(op.path)], prefix="keyward_scripts_"
            ) as argfile_path:
                proc = subprocess.run(
                    [
                        exe,
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
            errors.append((op.path, f"subprocess error: {e}"))
            continue
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()[:200]
            errors.append((op.path, f"exiftool failed: {stderr}"))
        else:
            written.append(op.path)
    return WriteResult(written=tuple(written), errors=tuple(errors))
