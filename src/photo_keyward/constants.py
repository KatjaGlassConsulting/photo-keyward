from __future__ import annotations

import os
from pathlib import Path

APP_ORG = "code2be"
APP_NAME = "photo-keyward"

SUPPORTED_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp"}
)

MIN_ICON_SIZE = 64
MAX_ICON_SIZE = 1024
DEFAULT_ICON_SIZE = 128

# Geometric factor applied per Ctrl+/Ctrl- press and per wheel notch.
ZOOM_STEP = 1.15

# Thumbnails are rendered and cached only at these sizes. The view scales
# the resulting pixmap to whatever the current display size is.
THUMB_TIERS = (64, 128, 256, 1024)

# Settings written before continuous zoom stored a name, not a pixel size.
LEGACY_ICON_SIZE_KEYS = {
    "small": 64,
    "medium": 128,
    "large": 256,
    "xlarge": 1024,
}

PREVIEW_MAX_PX = 384
PREVIEW_BOX_W = 480
PREVIEW_BOX_H = 320


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "photo_keyward" / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".config")
    path = Path(base) / "photo_keyward"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tier_for(display_px: int) -> int:
    """Smallest render tier that covers ``display_px``.

    Capped at the largest tier, so thumbnails are never rendered above
    MAX_ICON_SIZE. Because MIN_ICON_SIZE equals the smallest tier and
    MAX_ICON_SIZE equals the largest, a thumbnail is never upscaled.
    """
    for tier in THUMB_TIERS:
        if display_px <= tier:
            return tier
    return THUMB_TIERS[-1]


def path_key(path: str) -> str:
    """Comparison key for a filesystem path.

    Paths for the same file legitimately reach the app in different spellings:
    ``QFileSystemModel.filePath()`` yields forward slashes on Windows, while
    ``pathlib`` and ``os.path.normpath`` yield backslashes. A row built by
    ``set_folder`` can therefore read ``C:/pics/trip\\a.jpg`` while ``trash.py``
    hands back ``C:\\pics\\trip\\a.jpg`` for the very same file. Comparing raw
    strings silently fails to match — the file moves but its thumbnail stays on
    screen — so every path comparison goes through this key.
    """
    return os.path.normcase(os.path.normpath(path))
