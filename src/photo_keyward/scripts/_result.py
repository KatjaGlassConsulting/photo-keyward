# src/photo_keyward/scripts/_result.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScriptResult:
    """Outcome of running one of the batch-EXIF scripts.

    processed   — number of files successfully modified or exported
    skipped     — files explicitly not processed (no input, no match, etc.)
    warnings    — non-fatal per-file notices; each is one string
    error       — fatal error message if the whole run failed, else None
    """

    processed: int = 0
    skipped: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
