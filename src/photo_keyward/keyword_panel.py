from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .constants import path_key
from .exif_cache import ExifCache, ExifData
from .keyword_store import KeywordStore
from .keyword_writer import KeywordWriter
from .manage_keywords_dialog import ManageKeywordsDialog

_FLASH_STYLE = "QLineEdit { background-color: #fdd; }"


def _format_keyword(text: str) -> str:
    if "|" in text:
        return " ▸ ".join(text.split("|"))
    return text


class KeywordPanel(QWidget):
    """Right-pane keyword manager: add input, list of checkbox rows (one per
    keyword in the current folder's visible subset), and a Manage button.
    Checkbox state is derived from the current grid selection + ExifCache."""

    def __init__(
        self,
        store: KeywordStore,
        exif_cache: ExifCache,
        writer: KeywordWriter,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._cache = exif_cache
        self._writer = writer
        self._current_folder: str | None = None
        self._selected_paths: list[str] = []
        self._updating = False  # guard against itemChanged recursion

        self._input = QLineEdit()
        self._input.setPlaceholderText("Add keyword (use '|' for hierarchy)")
        self._input.returnPressed.connect(self._on_add)

        self._hint = QLabel("Run Tools → Read EXIF first to see which keywords are assigned.")
        self._hint.setStyleSheet("color: #aa6600; padding: 4px;")
        self._hint.setWordWrap(True)
        self._hint.hide()

        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_item_changed)

        self._manage_btn = QPushButton("Manage…")
        self._manage_btn.clicked.connect(self._on_manage)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._input)
        layout.addWidget(self._hint)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._manage_btn)

        self._store.vocab_changed.connect(self._rebuild_rows)
        self._store.subset_changed.connect(self._on_subset_changed)
        self._cache.cache_updated.connect(self._on_cache_updated)

    # -- guard helper --------------------------------------------------
    @contextmanager
    def _suspend_dispatch(self):
        """Pause dispatching itemChanged → assign/unassign during programmatic
        check-state updates. Re-entrant: a nested call restores the previous
        flag value on exit rather than unconditionally clearing it."""
        prev = self._updating
        self._updating = True
        try:
            yield
        finally:
            self._updating = prev

    # -- public API ----------------------------------------------------
    def set_current_folder(self, folder: str) -> None:
        self._current_folder = folder
        # A folder nobody has configured inherits the whole vocabulary, so the
        # panel is never blank just because this folder is new.
        self._store.ensure_folder_seeded(folder)
        self._rebuild_rows()

    def set_selection(self, paths: list[str]) -> None:
        self._selected_paths = list(paths)
        self._recompute_check_states()

    # -- row management ------------------------------------------------
    def _rebuild_rows(self) -> None:
        with self._suspend_dispatch():
            self._list.clear()
            if self._current_folder is None:
                return
            for kw in self._store.get_folder_subset(self._current_folder):
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
                item.setCheckState(Qt.Unchecked)
                self._list.addItem(item)
        self._recompute_check_states()

    def _recompute_check_states(self) -> None:
        with self._suspend_dispatch():
            any_missing_cache = False
            for i in range(self._list.count()):
                item = self._list.item(i)
                kw = item.data(Qt.UserRole)
                state, missing = self._compute_state(kw)
                item.setCheckState(state)
                any_missing_cache = any_missing_cache or missing
            self._hint.setVisible(
                bool(self._selected_paths) and any_missing_cache
            )

    def _compute_state(self, kw: str) -> tuple[Qt.CheckState, bool]:
        if not self._selected_paths:
            return Qt.Unchecked, False
        is_hierarchy = "|" in kw
        present = 0
        missing_cache = False
        for path in self._selected_paths:
            data = self._cache.get(path)
            if data is None:
                missing_cache = True
                continue
            field = data.hierarchical if is_hierarchy else data.keywords
            if kw in field:
                present += 1
        total = len(self._selected_paths)
        if present == 0:
            state = Qt.Unchecked
        elif present == total:
            state = Qt.Checked
        else:
            state = Qt.PartiallyChecked
        return state, missing_cache

    # -- toggle dispatch ----------------------------------------------
    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        kw = item.data(Qt.UserRole)
        state = item.checkState()
        if state == Qt.Checked:
            self._dispatch_assign(kw)
        elif state == Qt.Unchecked:
            self._dispatch_unassign(kw)

    def _dispatch_assign(self, kw: str) -> None:
        targets = self._partition(kw, want_present=False)
        if not targets:
            return
        self._optimistic_update(kw, targets, add=True)
        self._writer.assign(kw, targets)

    def _dispatch_unassign(self, kw: str) -> None:
        targets = self._partition(kw, want_present=True)
        if not targets:
            return
        self._optimistic_update(kw, targets, add=False)
        self._writer.unassign(kw, targets)

    def _partition(self, kw: str, want_present: bool) -> list[str]:
        """For assign (want_present=False) we want paths that DON'T have the
        keyword. For unassign (want_present=True) we want paths that DO."""
        is_hierarchy = "|" in kw
        out: list[str] = []
        for path in self._selected_paths:
            data = self._cache.get(path)
            if data is None:
                # cache miss: write anyway (exiftool +=/-= is idempotent)
                out.append(path)
                continue
            field = data.hierarchical if is_hierarchy else data.keywords
            has_it = kw in field
            if has_it == want_present:
                out.append(path)
        return out

    def _optimistic_update(
        self, kw: str, paths: list[str], add: bool
    ) -> None:
        is_hierarchy = "|" in kw
        updates: dict[str, ExifData] = {}
        for path in paths:
            data = self._cache.get(path)
            if data is None:
                continue
            if is_hierarchy:
                current = list(data.hierarchical)
            else:
                current = list(data.keywords)
            if add and kw not in current:
                current.append(kw)
            elif not add and kw in current:
                current.remove(kw)
            if is_hierarchy:
                updates[path] = ExifData(
                    date_taken=data.date_taken,
                    keywords=data.keywords,
                    hierarchical=tuple(current),
                )
            else:
                updates[path] = ExifData(
                    date_taken=data.date_taken,
                    keywords=tuple(current),
                    hierarchical=data.hierarchical,
                )
        if updates:
            self._cache.put_many(updates)

    # -- signal slots --------------------------------------------------
    def _on_cache_updated(self, paths: set[str]) -> None:
        if not self._selected_paths:
            return
        if any(p in paths for p in self._selected_paths):
            self._recompute_check_states()

    def _on_subset_changed(self, folder: str) -> None:
        if self._current_folder is None:
            return
        # by key, not by string: the tree, the path bar and a restored
        # last_folder each spell the same folder differently
        if path_key(folder) == path_key(self._current_folder):
            self._rebuild_rows()

    # -- input ---------------------------------------------------------
    def _on_add(self) -> None:
        text = self._input.text().strip()
        if not text:
            self._input.clear()
            return
        ok = self._store.add(text)
        if not ok:
            self._flash_input()
            return
        if self._current_folder is not None:
            self._store.add_to_folder_subset(self._current_folder, text)
        self._input.clear()

    def _flash_input(self) -> None:
        self._input.setStyleSheet(_FLASH_STYLE)
        QTimer.singleShot(600, lambda: self._input.setStyleSheet(""))

    # -- manage (wired in Task 8) -------------------------------------
    def _on_manage(self) -> None:
        dlg = ManageKeywordsDialog(
            self._store, self._current_folder, self._cache, self
        )
        dlg.exec()
