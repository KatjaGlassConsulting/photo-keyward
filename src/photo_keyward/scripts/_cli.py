# src/photo_keyward/scripts/_cli.py
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from ._result import ScriptResult


def _build_parser(prog: str, summary: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=summary)
    parser.add_argument("folder", help="folder to operate on (non-recursive)")
    return parser


def _print_summary(result: ScriptResult, action: str) -> None:
    print(f"{action}: processed={result.processed}, skipped={result.skipped}, "
          f"warnings={len(result.warnings)}")
    for w in result.warnings:
        print(f"  - {w}")
    if result.error is not None:
        print(f"Error: {result.error}", file=sys.stderr)


def _run(prog: str, summary: str, fn: Callable[[Path], ScriptResult]) -> int:
    parser = _build_parser(prog, summary)
    args = parser.parse_args()
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Error: not a directory: {folder}", file=sys.stderr)
        return 2
    result = fn(folder)
    _print_summary(result, prog)
    return 0 if result.error is None else 1


def main_export_dates() -> None:
    from . import export_dates
    sys.exit(_run(
        "keyward-export-dates",
        "Export DateTimeOriginal of every ORF in <folder> to <folder>/dates.json.",
        lambda folder: export_dates.run(folder),
    ))


def main_import_dates() -> None:
    from . import import_dates
    sys.exit(_run(
        "keyward-import-dates",
        "Apply dates from <folder>/dates.json to same-stem JPGs in <folder>.",
        lambda folder: import_dates.run(folder),
    ))


def main_export_metadata() -> None:
    from . import export_metadata
    sys.exit(_run(
        "keyward-export-metadata",
        "Export JPG dates + keywords + hierarchy to <folder>/metadata.json.",
        lambda folder: export_metadata.run(folder),
    ))


def main_import_metadata() -> None:
    from . import import_metadata
    sys.exit(_run(
        "keyward-import-metadata",
        "Apply <folder>/metadata.json to JPGs: dates overwrite, keywords additive.",
        lambda folder: import_metadata.run(folder),
    ))


def main_set_date_from_filename() -> None:
    from . import set_date_from_filename
    sys.exit(_run(
        "keyward-set-date-from-filename",
        "Set JPG DateTimeOriginal + FileCreateDate from YYYY-MM-DD prefix of filename.",
        lambda folder: set_date_from_filename.run(folder),
    ))


def main_set_date_from_metadata() -> None:
    from . import set_date_from_metadata
    sys.exit(_run(
        "keyward-set-date-from-metadata",
        "Set JPG FileCreateDate from the DateTimeOriginal EXIF tag.",
        lambda folder: set_date_from_metadata.run(folder),
    ))
