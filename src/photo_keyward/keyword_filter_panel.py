from __future__ import annotations

import os
from contextlib import contextmanager

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .constants import path_key
from .exif_cache import ExifCache
from .keyword_filter import KeywordFilter
from .keyword_import import folder_image_paths, keywords_in_cache
from .keyword_panel import _format_keyword
from .keyword_store import KeywordStore, keyword_sort_key

NO_KEY_LABEL = "(no keyword)"


class _SelectAllBox(QCheckBox):
    """Tri-state display, two-state interaction.

    Qt's default tri-state click cycles unchecked → partial → checked. Partial
    only ever *describes* a mixed list here — it is not something a user can
    meaningfully ask for — so a click toggles between all and none. A click
    from partial therefore selects everything, which is the more useful of the
    two directions when some rows are off.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Explicit rather than relying on setCheckState(Qt.PartiallyChecked)
        # to set this flag as a side effect -- true today, but not a
        # documented contract worth depending on silently.
        self.setTristate(True)

    def nextCheckState(self) -> None:  # type: ignore[override]
        self.setCheckState(
            Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked
        )


class KeywordFilterPanel(QWidget):
    """Left-pane "Keys" tab: a checkable list of the keywords available in the
    current folder, a "(no keyword)" row for untagged images, and a select-all
    header. Emits the resulting filter whenever it changes."""

    filter_changed = Signal(object)  # KeywordFilter

    def __init__(
        self,
        store: KeywordStore,
        exif_cache: ExifCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._cache = exif_cache
        self._folder: str | None = None
        self._checked: set[str] = set()
        # What the list held at the last rebuild. Without it, a key the user
        # unchecked could not be told apart from one appearing for the first
        # time, and would silently re-check itself on the next rebuild.
        self._known: set[str] = set()
        # Keywords seen in this folder's images, accumulated from
        # cache_updated. Cleared on folder change.
        self._found: set[str] = set()
        self._no_key_checked = True
        self._updating = False

        self._all_box = _SelectAllBox("Select all")
        self._all_box.setCheckState(Qt.Checked)
        self._all_box.clicked.connect(self._on_all_clicked)

        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_item_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._all_box)
        layout.addWidget(self._list, 1)

        store.vocab_changed.connect(self._on_vocab_changed)
        store.subset_changed.connect(self._on_subset_changed)
        exif_cache.cache_updated.connect(self._on_cache_updated)

    # -- guard helper --------------------------------------------------
    @contextmanager
    def _suspend_dispatch(self):
        """Pause itemChanged → state updates while rows are rebuilt. Re-entrant:
        a nested call restores the previous flag rather than clearing it."""
        prev = self._updating
        self._updating = True
        try:
            yield
        finally:
            self._updating = prev

    # -- public API ----------------------------------------------------
    def set_folder(self, folder: str) -> None:
        """A new folder starts unfiltered: keys carry no meaning across
        folders, and a filter left over from the previous one would show a
        half-empty grid with no obvious cause."""
        self._folder = folder or None
        self._found = self._scan_cached_keywords(self._folder)
        self._known = set()
        self._checked = set()
        self._no_key_checked = True
        keys = self._current_keys()
        self._checked = set(keys)
        self._known = set(keys)
        self._populate(keys)
        self._emit()

    def _scan_cached_keywords(self, folder: str | None) -> set[str]:
        """Keywords already sitting in the cache for this folder's images.

        ``_found`` is otherwise only ever grown reactively, from future
        ``cache_updated`` signals. Without this, revisiting a folder that was
        already read earlier in the session would wipe rows for keywords found
        outside the configured subset, even though the data is already in the
        cache — the rows would only reappear once a fresh EXIF read happened
        to touch those files again, which can take seconds on a large folder.

        ``folder_image_paths`` spells the paths the way ``ExifCache`` is
        keyed; see it for why that matters.
        """
        return keywords_in_cache(self._cache, folder_image_paths(folder))

    # -- rows ----------------------------------------------------------
    def _current_keys(self) -> list[str]:
        """The folder's configured subset plus any further keywords actually
        found in its images, in one alphabetical run.

        The subset alone is not enough: an image tagged elsewhere, or tagged
        before the subset was narrowed, would carry a keyword with no row and
        would then be invisible even with everything checked.

        Configured and found keys are sorted together rather than in two
        blocks. Which of the two a row came from is invisible here — both are
        just checkable rows — so grouping by it would only make a key hard to
        find.
        """
        if self._folder is None:
            return []
        subset = self._store.get_folder_subset(self._folder)
        return sorted(set(subset) | self._found, key=keyword_sort_key)

    def _only_no_key_selected(self) -> bool:
        """True when "(no keyword)" is the *sole* checked row — the state the
        grid is in while working through a folder's untagged backlog.

        ``bool(self._known)`` guards a folder with no keywords at all: there,
        this state and "everything checked" are the same thing, the grid is
        unfiltered, and treating it as filtered would make an image vanish the
        moment it is tagged.

        Intersected with ``_known`` because ``_checked`` can still hold keys
        from an earlier rebuild that no longer have a row.
        """
        return (
            bool(self._known)
            and self._no_key_checked
            and not (self._checked & self._known)
        )

    def _rebuild(self, check_new: bool = True) -> None:
        """Rebuild the rows, keeping the user's choices. Keys appearing for the
        first time start checked, so the auto-read completing does not change
        what the grid shows.

        ``check_new=False`` suppresses that for keywords the user just created
        while only "(no keyword)" was checked: adopting them would keep every
        image the user tags on screen, so the backlog would never shrink.
        """
        keys = self._current_keys()
        if check_new:
            self._checked |= set(keys) - self._known
        self._checked &= set(keys)
        self._known = set(keys)
        self._populate(keys)
        self._emit()

    def _populate(self, keys: list[str]) -> None:
        with self._suspend_dispatch():
            self._list.clear()
            no_key = QListWidgetItem(NO_KEY_LABEL)
            # None in UserRole is what marks this row; every other row stores
            # its keyword there.
            no_key.setData(Qt.UserRole, None)
            no_key.setFlags(
                Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
            )
            no_key.setCheckState(
                Qt.Checked if self._no_key_checked else Qt.Unchecked
            )
            self._list.addItem(no_key)
            for kw in keys:
                item = QListWidgetItem(_format_keyword(kw))
                item.setData(Qt.UserRole, kw)
                if "|" in kw:
                    f = QFont(item.font())
                    f.setItalic(True)
                    item.setFont(f)
                item.setFlags(
                    Qt.ItemIsUserCheckable
                    | Qt.ItemIsEnabled
                    | Qt.ItemIsSelectable
                )
                item.setCheckState(
                    Qt.Checked if kw in self._checked else Qt.Unchecked
                )
                self._list.addItem(item)
        self._sync_all_box(keys)

    def _sync_all_box(self, keys: list[str]) -> None:
        total = len(keys) + 1  # + the "(no keyword)" row
        n = len(self._checked & set(keys)) + (1 if self._no_key_checked else 0)
        if n == 0:
            state = Qt.Unchecked
        elif n == total:
            state = Qt.Checked
        else:
            state = Qt.PartiallyChecked
        # setCheckState does not emit clicked, which is why the header is wired
        # to clicked rather than stateChanged: no guard flag is needed here.
        self._all_box.setCheckState(state)

    def _emit(self) -> None:
        keys = self._current_keys()
        all_selected = self._no_key_checked and set(keys) <= self._checked
        self.filter_changed.emit(
            KeywordFilter(
                selected=frozenset(self._checked),
                include_no_key=self._no_key_checked,
                all_selected=all_selected,
            )
        )

    # -- slots ---------------------------------------------------------
    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        kw = item.data(Qt.UserRole)
        checked = item.checkState() == Qt.Checked
        if kw is None:
            self._no_key_checked = checked
        elif checked:
            self._checked.add(kw)
        else:
            self._checked.discard(kw)
        self._sync_all_box(self._current_keys())
        self._emit()

    def _on_all_clicked(self) -> None:
        keys = self._current_keys()
        select = self._all_box.checkState() == Qt.Checked
        self._checked = set(keys) if select else set()
        self._no_key_checked = select
        self._populate(keys)
        self._emit()

    def _on_vocab_changed(self) -> None:
        # Both this and _on_subset_changed are how a keyword the user just
        # created reaches the panel. _on_cache_updated keeps the default: a key
        # surfacing from an EXIF read is not something the user asked for, and
        # hiding images because of it would look like a bug.
        self._rebuild(check_new=not self._only_no_key_selected())

    def _on_subset_changed(self, folder: str) -> None:
        # by key, not by string: the tree, the path bar and a restored
        # last_folder each spell the same folder differently
        if self._folder is None:
            return
        if path_key(folder) == path_key(self._folder):
            self._rebuild(check_new=not self._only_no_key_selected())

    def _on_cache_updated(self, paths: set[str]) -> None:
        """Collect keywords from the paths the signal names, rather than
        rescanning the folder — a rescan would stat every image on every
        keyword write."""
        if self._folder is None:
            return
        folder_key = path_key(self._folder)
        found: set[str] = set()
        for p in paths:
            # A read for the previous folder can land after the user moved on.
            if path_key(os.path.dirname(p)) != folder_key:
                continue
            data = self._cache.get(p)
            if data is None:
                continue
            found |= set(data.keywords) | set(data.hierarchical)
        new = found - self._found
        if not new:
            return
        before = self._current_keys()
        self._found |= new
        if self._current_keys() == before:
            # The keyword already had a row — it is in the folder's configured
            # subset, and this is the first image to carry it. Only our record
            # of what has been seen changed, so there is nothing to rebuild.
            # Rebuilding would re-emit the filter and reset the grid model,
            # discarding the selection while the user is still tagging.
            return
        self._rebuild()
