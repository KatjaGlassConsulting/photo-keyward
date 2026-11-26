from __future__ import annotations

import json
import os
import unicodedata
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .constants import path_key


def _fold(text: str) -> str:
    """Lower-case and strip accents, so 'Ä' compares equal to 'A'."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def keyword_sort_key(keyword: str) -> tuple[list[str], str]:
    """Order keywords the way a reader expects rather than by code point.

    Three things a plain ``sorted()`` gets wrong here:

    * Accents. ``Ägypten`` is U+00C4, so code-point order files it after
      ``Zebra`` — at the bottom of a German user's list, which is where they
      would never look for it. Folding puts it under A, as German lists are
      normally ordered (DIN 5007-1).
    * Case. Every capitalised keyword would otherwise sort ahead of every
      lower-case one, splitting ``Beach`` from ``beach``.
    * Hierarchy. Comparing the raw string puts ``fish|shark`` after
      ``fisher``, because ``|`` outranks every letter. Comparing segment by
      segment keeps a parent next to its children, which is how the rows read.

    The raw keyword breaks ties, so two that differ only in case or accent
    still have one stable order rather than swapping around between runs.
    """
    return ([_fold(part) for part in keyword.split("|")], keyword)


class KeywordStore(QObject):
    """Global keyword vocabulary plus per-folder visible subsets, both
    persisted to JSON files in the app config directory.

    The vocabulary is the permanent part: it is written on every change and
    read back at startup, and a folder that has never been configured
    inherits the whole of it (see ``ensure_folder_seeded``). Subsets only
    ever narrow what a folder shows.
    """

    vocab_changed = Signal()
    subset_changed = Signal(str)
    load_error = Signal(str)  # emitted from initialize(); MainWindow logs as warning

    def __init__(self, base_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._vocab_path = base_dir / "keywords.json"
        self._subsets_path = base_dir / "folder_subsets.json"
        self._vocab: list[str] = []
        # keyed by path_key(folder), never by the raw folder string
        self._subsets: dict[str, list[str]] = {}
        self._loaded = False

    def initialize(self) -> None:
        """Read both JSON files. Call after connecting signals so load
        errors can be emitted as warnings."""
        if self._loaded:
            return
        self._loaded = True
        self._load_vocab()
        self._vocab.sort(key=keyword_sort_key)
        self._load_subsets()

    # -- file handling -------------------------------------------------
    def _quarantine(self, path: Path, error: Exception) -> str:
        """Move an unreadable file aside, and describe what happened.

        A failed load leaves the in-memory copy empty, and the next add() or
        remove() would then persist that empty copy over the file — turning a
        vocabulary that was merely unreadable once into one that is genuinely
        gone. Renaming first bounds the damage at "the user has to rename a
        file back", and the message names the file so they can.
        """
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, backup)
        except OSError as e:
            return (
                f"{path.name}: {error}. It could not be moved aside either "
                f"({e}), so it may be overwritten — copy it now if you need it."
            )
        return (
            f"{path.name}: {error}. The file was kept as {backup.name} and the "
            f"list starts empty."
        )

    def _write_json(self, path: Path, payload: object) -> None:
        """Write through a temp sibling so an interrupted write cannot leave a
        half-written file behind — the state that makes the next load fail."""
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)

    def _load_vocab(self) -> None:
        if not self._vocab_path.exists():
            return
        try:
            data = json.loads(self._vocab_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.load_error.emit(self._quarantine(self._vocab_path, e))
            return
        keywords = data.get("keywords", []) if isinstance(data, dict) else []
        if not isinstance(keywords, list):
            return
        seen: set[str] = set()
        for k in keywords:
            if isinstance(k, str) and k and k not in seen:
                self._vocab.append(k)
                seen.add(k)

    def _load_subsets(self) -> None:
        if not self._subsets_path.exists():
            return
        try:
            data = json.loads(self._subsets_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.load_error.emit(self._quarantine(self._subsets_path, e))
            return
        if not isinstance(data, dict):
            return
        # Entries written before folders were keyed by path_key carry the raw
        # spelling they arrived in, so the same folder can appear twice — once
        # from the tree (forward slashes) and once from the path bar
        # (backslashes). Re-key them, and union rather than drop a collision:
        # both halves are keywords the user ticked for this folder.
        migrated = False
        for folder, keys in data.items():
            if not isinstance(folder, str) or not isinstance(keys, list):
                continue
            key = path_key(folder)
            migrated = migrated or key != folder
            clean = [k for k in keys if isinstance(k, str)]
            existing = self._subsets.get(key)
            if existing is None:
                self._subsets[key] = clean
                continue
            migrated = True
            for k in clean:
                if k not in existing:
                    existing.append(k)
        if migrated:
            try:
                self._save_subsets()
            except OSError as e:
                self.load_error.emit(f"folder_subsets.json: {e}")

    def _save_vocab(self) -> None:
        self._write_json(self._vocab_path, {"keywords": self._vocab})

    def _save_subsets(self) -> None:
        self._write_json(self._subsets_path, self._subsets)

    # -- vocab ---------------------------------------------------------
    def add(self, text: str) -> bool:
        """Returns True on success, False if empty or duplicate."""
        text = text.strip()
        if not text or text in self._vocab:
            return False
        self._vocab.append(text)
        self._vocab.sort(key=keyword_sort_key)
        self._save_vocab()
        self.vocab_changed.emit()
        return True

    def add_many(self, keys: list[str]) -> list[str]:
        """Add several keywords in one write. Returns those actually added,
        in the order given, skipping empties and ones already known.

        add() persists and signals per call, which is right for one keyword
        typed into the panel and wrong for an import: forty keywords would
        mean forty rewrites of keywords.json and forty panel rebuilds.
        """
        added: list[str] = []
        for key in keys:
            key = key.strip()
            if not key or key in self._vocab:
                continue
            self._vocab.append(key)
            added.append(key)
        if not added:
            return []
        self._vocab.sort(key=keyword_sort_key)
        self._save_vocab()
        self.vocab_changed.emit()
        return added

    def remove(self, text: str) -> None:
        if text not in self._vocab:
            return
        self._vocab.remove(text)
        self._save_vocab()
        # strip orphan refs from all subsets
        dirty = False
        for folder, keys in self._subsets.items():
            if text in keys:
                self._subsets[folder] = [k for k in keys if k != text]
                dirty = True
        if dirty:
            self._save_subsets()
        self.vocab_changed.emit()

    def list_all(self) -> list[str]:
        return list(self._vocab)

    def contains(self, text: str) -> bool:
        return text in self._vocab

    # -- subsets -------------------------------------------------------
    def get_folder_subset(self, folder: str) -> list[str]:
        keys = self._subsets.get(path_key(folder), [])
        # filter orphans on read so callers never see a deleted keyword; sort
        # on read too, so a file written before ordering existed still shows
        # in order without needing a migration
        return sorted(
            (k for k in keys if k in self._vocab), key=keyword_sort_key
        )

    def set_folder_subset(self, folder: str, keys: list[str]) -> None:
        filtered = sorted(
            (k for k in keys if k in self._vocab), key=keyword_sort_key
        )
        self._subsets[path_key(folder)] = filtered
        self._save_subsets()
        self.subset_changed.emit(folder)

    def add_to_folder_subset(self, folder: str, key: str) -> None:
        if key not in self._vocab:
            return
        current = self._subsets.setdefault(path_key(folder), [])
        if key in current:
            return
        current.append(key)
        current.sort(key=keyword_sort_key)
        self._save_subsets()
        self.subset_changed.emit(folder)

    def ensure_folder_seeded(self, folder: str) -> None:
        """Give a folder showing nothing the whole current vocabulary.

        A folder with no entry has never been configured. A folder with an
        empty entry was either pruned to nothing or written empty by an
        earlier version. Neither state is worth preserving: both render an
        empty panel, which reads as the keyword list having been lost. So both
        get seeded, at the cost of a folder deliberately emptied filling back
        up the next time it is opened.

        Seeding is a snapshot, not a subscription — keywords added later reach
        the folder they were added in, not every folder seeded before.
        """
        if not self._vocab:
            return
        key = path_key(folder)
        if self._subsets.get(key):
            return
        self._subsets[key] = list(self._vocab)
        self._save_subsets()
        self.subset_changed.emit(folder)
