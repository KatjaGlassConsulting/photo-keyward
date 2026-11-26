"""Move image files into a ``trash`` subfolder, and put them back.

Qt-free by design: this module is pure filesystem logic so it can be
exercised without a running application.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .constants import SUPPORTED_EXTENSIONS

TRASH_DIR_NAME = "trash"

# Raw and sidecar files that travel with an image when it is trashed.
COMPANION_EXTENSIONS = frozenset({".orf", ".xmp"})

# How many Del presses stay undoable. One press is one step, however many
# images it moved.
UNDO_DEPTH = 3


@dataclass(frozen=True)
class MovedGroup:
    """One image and its companions, as actually moved.

    ``files`` holds ``(original, in-trash)`` for every file in the group,
    the image first. Recording both ends is what makes the move reversible.
    Re-deriving the companion list at undo time would guess instead: a trash
    folder that already held an unrelated ``IMG_001.orf`` would yield a
    group that never existed.
    """

    image: str
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TrashResult:
    """Outcome of one move-to-trash operation.

    groups   — one per image that moved, carrying the renames to reverse
    skipped  — (image path, reason) per image that did not move

    In a ``skipped`` entry the first element is always the *image* path, so
    the caller can match skips against its selection. The reason names the
    file that actually caused the failure, which may be a companion.
    """

    groups: tuple[MovedGroup, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def moved(self) -> tuple[str, ...]:
        """Image paths that moved — what the grid removes."""
        return tuple(g.image for g in self.groups)

    @property
    def companions_moved(self) -> int:
        """Companion files that moved alongside them."""
        return sum(len(g.files) - 1 for g in self.groups)


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of one undo. Mirrors TrashResult's reporting fields."""

    restored: tuple[str, ...] = ()
    companions_restored: int = 0
    skipped: tuple[tuple[str, str], ...] = ()


def companions_for(image: Path) -> list[Path]:
    """Raw and sidecar files belonging to ``image``, in the same folder.

    Two naming conventions are recognised, both case-insensitively:

      * ``<stem><companion ext>``  — e.g. ``IMG_001.orf``, ``IMG_001.xmp``
      * ``<full name>.xmp``        — e.g. ``IMG_001.jpg.xmp`` (darktable, Adobe)

    A file whose own extension is a supported image type is never treated as
    a companion, so trashing ``IMG_001.jpg`` cannot drag ``IMG_001.png``
    along with it.
    """
    stem_lower = image.stem.lower()
    name_lower = image.name.lower()
    try:
        entries = list(image.parent.iterdir())
    except OSError:
        return []

    found: list[Path] = []
    for candidate in entries:
        if candidate == image or not candidate.is_file():
            continue
        suffix = candidate.suffix.lower()
        if suffix in SUPPORTED_EXTENSIONS or suffix not in COMPANION_EXTENSIONS:
            continue
        candidate_stem = candidate.stem.lower()
        if candidate_stem in (stem_lower, name_lower):
            found.append(candidate)
    return found


def move_to_trash(image_paths: Iterable[str]) -> TrashResult:
    """Move each image, plus its companions, into ``<parent>/trash``.

    Images are grouped by parent folder so each one lands in a trash folder
    beside itself. An image and its companions form one atomic group: every
    destination is checked before any file is moved, so a collision on the
    sidecar can never leave the JPG trashed and the ORF behind.
    """
    groups: list[MovedGroup] = []
    skipped: list[tuple[str, str]] = []

    by_parent: dict[Path, list[Path]] = defaultdict(list)
    for raw in image_paths:
        path = Path(raw)
        by_parent[path.parent].append(path)

    for parent, images in by_parent.items():
        trash_dir = parent / TRASH_DIR_NAME
        try:
            trash_dir.mkdir(exist_ok=True)
        except OSError as e:
            for image in images:
                skipped.append((str(image), f"cannot create trash folder: {e}"))
            continue

        for image in images:
            members = [image] + companions_for(image)
            collision = next(
                (m for m in members if (trash_dir / m.name).exists()), None
            )
            if collision is not None:
                skipped.append(
                    (str(image), f"{collision.name} already exists in trash")
                )
                continue
            done: list[tuple[str, str]] = []
            try:
                for member in members:
                    dest = trash_dir / member.name
                    member.rename(dest)
                    done.append((str(member), str(dest)))
            except OSError as e:
                # No rollback: undoing renames that may themselves fail would
                # turn one reported error into two unreported ones. Whatever
                # already moved sits in trash, which is recoverable through
                # the folder tree even though this group never becomes an
                # undo entry.
                skipped.append((str(image), f"move failed: {e}"))
                continue
            groups.append(MovedGroup(image=str(image), files=tuple(done)))

    return TrashResult(groups=tuple(groups), skipped=tuple(skipped))


def restore_from_trash(groups: Iterable[MovedGroup]) -> RestoreResult:
    """Move each group's files back to where they came from.

    The mirror of :func:`move_to_trash`, under the same rules. A group is
    atomic: every trashed file must still exist *and* every original path
    must be free before anything moves. So a file the user copied back by
    hand is never overwritten, and a half-restored pair cannot be produced.

    Both failure modes are ordinary rather than exceptional — the user may
    have emptied ``trash`` in Explorer, or put a different file of the same
    name in place — so each is a skip with a readable reason, not a raise.
    """
    restored: list[str] = []
    skipped: list[tuple[str, str]] = []
    companions_restored = 0

    for group in groups:
        problem: str | None = None
        for original, trashed in group.files:
            if not Path(trashed).exists():
                problem = f"{Path(trashed).name} is no longer in trash"
                break
            if Path(original).exists():
                problem = f"{Path(original).name} already exists"
                break
        if problem is not None:
            skipped.append((group.image, problem))
            continue
        try:
            for original, trashed in group.files:
                Path(trashed).rename(original)
        except OSError as e:
            # Same no-rollback reasoning as move_to_trash.
            skipped.append((group.image, f"restore failed: {e}"))
            continue
        restored.append(group.image)
        companions_restored += len(group.files) - 1

    return RestoreResult(
        restored=tuple(restored),
        companions_restored=companions_restored,
        skipped=tuple(skipped),
    )
