from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Environment variable — also a key in the project's .env — holding an explicit
# path to the exiftool executable. Set it when exiftool is not on PATH.
EXIFTOOL_PATH_VAR = "EXIFTOOL_PATH"

# Shown wherever a feature needs exiftool and cannot find it. Both routes are
# named, because .env is easy to miss if you only ever read the error.
EXIFTOOL_MISSING_MESSAGE = (
    "exiftool not found. Install it from https://exiftool.org and put it on "
    f"PATH, or set {EXIFTOOL_PATH_VAR} in the project's .env to its full path."
)


@contextmanager
def exiftool_argfile(
    args: Iterable[str], prefix: str = "keyward_"
) -> Iterator[str]:
    """Write exiftool arguments to a temporary UTF-8 argfile, yielding its path
    for use as ``-@ <path>``. The file is removed on exit.

    Every argument that can carry a non-ASCII character — tag *values* and
    file paths alike — must go through here rather than onto the command line.
    On Windows exiftool receives argv through the ANSI code page, so a keyword
    like ``Ägypten`` is mangled to ``?gypten``, or silently written as an empty
    string, before ``-charset utf8`` ever gets to interpret it. exiftool still
    exits 0, so nothing reports the loss. An argfile is read as bytes and does
    honour ``-charset utf8``, so the value survives.

    Tag *names* and switches (``-j``, ``-DateTimeOriginal``) are ASCII by
    construction and are fine either way; passing them here too just keeps each
    call site to one list.
    """
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".args")
    try:
        # newline="\n": exiftool accepts either ending, but writing "\r\n" on
        # Windows would leave a stray CR inside any value that is the last
        # thing on its line.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for arg in args:
                f.write(arg)
                f.write("\n")
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _find_env_file() -> Path | None:
    """Locate the nearest .env, searching upwards from a few sensible roots."""
    # The working directory covers `uv run` from anywhere inside the checkout;
    # the package's own parents cover an installed console script launched
    # from an unrelated folder.
    seen: set[Path] = set()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for directory in (start, *start.parents):
            if directory in seen:
                continue
            seen.add(directory)
            candidate = directory / ".env"
            if candidate.is_file():
                return candidate
    return None


@lru_cache(maxsize=1)
def load_env() -> Path | None:
    """Read the project's .env once per process, returning the file used.

    Real environment variables win over .env entries, so a shell export or a
    CI secret can override the checked-out configuration. Values are cached:
    editing .env takes effect on the next start.
    """
    env_file = _find_env_file()
    if env_file is not None:
        load_dotenv(env_file, override=False)
    return env_file


def configured_exiftool() -> str | None:
    """Return the exiftool path from EXIFTOOL_PATH, or None if unusable."""
    load_env()
    configured = os.environ.get(EXIFTOOL_PATH_VAR, "").strip().strip('"')
    if not configured:
        return None
    expanded = os.path.expanduser(os.path.expandvars(configured))
    if os.path.isfile(expanded):
        return expanded
    # Tolerate a bare command name rather than a full path.
    return shutil.which(expanded)


def find_exiftool() -> str | None:
    """Locate the exiftool executable.

    In order: `EXIFTOOL_PATH` from the environment or .env, then `exiftool` on
    `PATH`, then the usual Windows install locations. A configured path that
    does not exist is ignored rather than fatal, so a stale .env still leaves a
    working PATH installation usable.
    """
    configured = configured_exiftool()
    if configured:
        return configured

    found = shutil.which("exiftool")
    if found:
        return found

    # Some Windows installs put exiftool on the user PATH after the parent
    # shell of this process started, so it's not visible in os.environ['PATH'].
    # Probe a couple of common locations as a fallback.
    for env_key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_key)
        if not base:
            continue
        candidate = os.path.join(base, "exiftool", "exiftool.exe")
        if os.path.isfile(candidate):
            return candidate
    return None
