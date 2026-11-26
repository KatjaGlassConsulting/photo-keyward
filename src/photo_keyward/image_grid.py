from __future__ import annotations

import bisect
import os
from typing import Callable, Iterable

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from .constants import (
    DEFAULT_ICON_SIZE,
    MAX_ICON_SIZE,
    MIN_ICON_SIZE,
    SUPPORTED_EXTENSIONS,
    ZOOM_STEP,
    path_key,
    tier_for,
)
from .thumbnail_cache import ThumbnailCache


# Extra cell space around the icon. Square, because no filename label is
# drawn. 12 rather than 8 leaves room for the selection highlight to draw
# around the thumbnail without clipping into the neighbouring cell.
GRID_PADDING = 12


class _IconOnlyDelegate(QStyledItemDelegate):
    """Draws the thumbnail without the filename underneath.

    The model still returns the basename for Qt.DisplayRole, because that is
    what QAbstractItemView's type-ahead search matches against. Only the
    painting of that text is suppressed here.
    """

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        rect = QRect(option.rect)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # A real copy: assigning to opt.icon below would otherwise clobber this,
        # since PySide hands back a reference into the option struct.
        icon = QIcon(opt.icon)

        # Let the style draw the item chrome only -- background, selection
        # highlight, focus rect -- with no text and no decoration of its own.
        opt.text = ""
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        opt.icon = QIcon()
        opt.decorationSize = QSize(0, 0)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        if icon.isNull():
            return
        # Centre the thumbnail ourselves. Left to the style, two things go
        # wrong: initStyleOption sizes decorationSize to the aspect-fitted
        # pixmap and the layout anchors that rect at the top of the cell, so
        # landscape thumbnails ride high with a blank band beneath them; and
        # even with decorationSize forced to the full cell, PM_FocusFrameVMargin
        # leaves the result a couple of pixels off centre.
        # actualSize never exceeds the pixmap's own size, so this cannot upscale.
        target = icon.actualSize(rect.size())
        x = rect.x() + (rect.width() - target.width()) // 2
        y = rect.y() + (rect.height() - target.height()) // 2
        icon.paint(
            painter,
            QRect(QPoint(x, y), target),
            Qt.AlignCenter,
            QIcon.Mode.Normal,
            QIcon.State.Off,
        )

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        side = max(option.decorationSize.width(), option.decorationSize.height())
        return QSize(side, side)


class ImageFolderModel(QAbstractListModel):
    def __init__(self, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._paths: list[str] = []
        # _all_paths is every supported image in the folder; _paths is the
        # subset the view shows. _paths is always a subsequence of _all_paths
        # in the same order, which is what lets insertions be placed with
        # bisect on basenames.
        self._all_paths: list[str] = []
        self._filter: Callable[[str], bool] | None = None
        self._folder: str | None = None
        self._icon_size: int = DEFAULT_ICON_SIZE
        self._tier: int = tier_for(DEFAULT_ICON_SIZE)
        self._placeholder: QPixmap | None = None
        cache.ready.connect(self._on_thumb_ready)

    # -- Public API ----------------------------------------------------
    def set_folder(self, folder: str) -> None:
        # Deliberately does not touch self._filter: the outgoing folder's
        # predicate is briefly applied to the incoming folder's files below,
        # via self._visible(). MainWindow._active_filter is what corrects
        # this before anything is painted -- it resets to an unfiltered (or
        # freshly rebuilt) filter as part of the same folder-change signal
        # chain, ahead of the first paint.
        self.beginResetModel()
        self._folder = folder
        try:
            entries = os.listdir(folder)
        except OSError:
            entries = []
        paths = []
        for name in entries:
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                paths.append(os.path.join(folder, name))
        paths.sort(key=lambda p: os.path.basename(p).lower())
        self._all_paths = paths
        self._paths = self._visible()
        self.endResetModel()

    def all_paths(self) -> list[str]:
        """Every image in the folder, filtered or not. Read EXIF and the
        Scripts menu use this: both act on the folder, and running them
        against the visible subset would make their effect depend on which
        keywords happen to be checked."""
        return list(self._all_paths)

    def set_filter(self, predicate: Callable[[str], bool] | None) -> None:
        """``None`` means unfiltered, and is not the same as a predicate that
        always returns True: it skips the rebuild loop entirely, so browsing
        without the filter costs nothing.

        ``self._filter`` is assigned unconditionally, before the equality
        check below can return early -- later calls to ``refilter_paths``
        must see the new predicate even on a call that turns out to be a
        no-op for the visible list. Do not move the assignment after the
        check.

        A model reset drops the view's selection and scroll position, and
        several callers -- a panel rebuild, the guard in
        ``MainWindow._on_filter_changed`` -- re-emit a filter whose *effect*
        on the visible set is unchanged, e.g. because the newly (un)checked
        keyword has no matching image yet. Comparing the resulting visible
        list, not the predicate or the filter object, catches all of those:
        only reset when what the view would show actually differs.
        """
        self._filter = predicate
        new = self._visible()
        if new == self._paths:
            return
        self.beginResetModel()
        self._paths = new
        self.endResetModel()

    def refilter_paths(self, paths: Iterable[str]) -> None:
        """Re-evaluate only ``paths``, adding or removing individual rows.

        Deliberately not a model reset: this runs whenever a keyword is
        written, and a reset would drop the selection and jump the view to the
        top while the user is still tagging.
        """
        if self._filter is None:
            return
        targets = {path_key(p) for p in paths}
        for path in self._all_paths:
            key = path_key(path)
            if key not in targets:
                continue
            row = self._visible_row_of(key)
            accepted = self._filter(path)
            if accepted and row is None:
                self._insert_visible_row(path)
            elif not accepted and row is not None:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._paths[row]
                self.endRemoveRows()

    def _visible(self) -> list[str]:
        if self._filter is None:
            return list(self._all_paths)
        return [p for p in self._all_paths if self._filter(p)]

    def _visible_row_of(self, key: str) -> int | None:
        for i, p in enumerate(self._paths):
            if path_key(p) == key:
                return i
        return None

    def _insert_visible_row(self, path: str) -> None:
        """Insert into ``_paths`` at the position that keeps it ordered by
        lowercased basename, matching ``_all_paths``."""
        name_key = os.path.basename(path).lower()
        keys = [os.path.basename(p).lower() for p in self._paths]
        row = bisect.bisect_left(keys, name_key)
        self.beginInsertRows(QModelIndex(), row, row)
        self._paths.insert(row, path)
        self.endInsertRows()

    def set_icon_size(self, size: int) -> None:
        """``size`` is the display size in pixels. The render tier is derived
        from it; rows are only invalidated when the tier actually changes,
        because within one tier the same pixmap is reused and the view
        rescales it on its own."""
        if size == self._icon_size:
            return
        self._icon_size = size
        new_tier = tier_for(size)
        if new_tier == self._tier:
            return
        self._tier = new_tier
        if self._paths:
            top = self.index(0, 0)
            bottom = self.index(len(self._paths) - 1, 0)
            self.dataChanged.emit(top, bottom, [Qt.DecorationRole])

    def remove_paths(self, paths: Iterable[str]) -> None:
        """Drop rows for ``paths``. Paths not in the model are ignored.

        Rows are removed in descending order so that the indices of rows not
        yet removed stay valid throughout. This is a real row removal rather
        than a model reset, which is what preserves the view's scroll
        position.

        Both lists are maintained, so deleting a file removes it from the
        folder even when the filter is hiding its row.
        """
        targets = {path_key(p) for p in paths}
        self._all_paths = [
            p for p in self._all_paths if path_key(p) not in targets
        ]
        rows = sorted(
            (i for i, p in enumerate(self._paths) if path_key(p) in targets),
            reverse=True,
        )
        for row in rows:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._paths[row]
            self.endRemoveRows()

    def insert_paths(self, paths: Iterable[str]) -> None:
        """Insert rows for ``paths`` at their sorted position.

        Paths whose parent is not this model's folder are ignored: an undo
        can restore files into a folder that is not the one on screen, and
        those must not appear in this view. Duplicates are ignored too.

        Like ``remove_paths`` this is a real insertion rather than a reset,
        so the view keeps its scroll position across an undo.

        A restored file always rejoins ``_all_paths``; it gains a visible row
        only if the current filter accepts it.
        """
        folder = self._folder
        if not folder:
            return
        folder_key = path_key(folder)
        all_names = [os.path.basename(p).lower() for p in self._all_paths]
        existing = {path_key(p) for p in self._all_paths}
        for path in sorted(paths, key=lambda p: os.path.basename(p).lower()):
            if path_key(os.path.dirname(path)) != folder_key:
                continue
            # Rebuild the path the way set_folder spells it. Incoming paths
            # come from pathlib inside trash.py and are separator-normalised,
            # while _folder comes from the folder tree and may not be; storing
            # the incoming string could leave two spellings of one file in the
            # model, and a later Del would then not match its own row.
            name = os.path.basename(path)
            stored = os.path.join(folder, name)
            if path_key(stored) in existing:
                continue
            key = name.lower()
            row = bisect.bisect_left(all_names, key)
            self._all_paths.insert(row, stored)
            all_names.insert(row, key)
            existing.add(path_key(stored))
            if self._filter is None or self._filter(stored):
                self._insert_visible_row(stored)

    def paths(self) -> list[str]:
        return list(self._paths)

    def total_count(self) -> int:
        """Number of images in the folder, filtered or not. Separate from
        ``all_paths()`` because the status bar asks for this on every selection
        change, and copying a few thousand path strings for a count is waste."""
        return len(self._all_paths)

    def path_at(self, index: QModelIndex) -> str | None:
        if not index.isValid() or index.row() >= len(self._paths):
            return None
        return self._paths[index.row()]

    # -- Qt overrides --------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._paths)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row >= len(self._paths):
            return None
        path = self._paths[row]
        if role == Qt.DisplayRole:
            return os.path.basename(path)
        if role == Qt.DecorationRole:
            pm = self._cache.get(path, self._tier)
            if pm is not None:
                return QIcon(pm)
            self._cache.request(path, self._tier)
            return QIcon(self._placeholder_pixmap())
        if role == Qt.ToolTipRole:
            # Plain text, deliberately not rich text: Qt auto-detects HTML in
            # tooltips and Windows paths legitimately contain "&", which would
            # otherwise be parsed as the start of an entity.
            return f"{os.path.basename(path)}\n{path}"
        return None

    # -- Internals -----------------------------------------------------
    def _on_thumb_ready(self, path: str, size: int, _pm: QPixmap) -> None:
        if size != self._tier:
            return
        try:
            row = self._paths.index(path)
        except ValueError:
            return
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def _placeholder_pixmap(self) -> QPixmap:
        if self._placeholder is None or self._placeholder.width() != self._tier:
            pm = QPixmap(self._tier, self._tier)
            pm.fill(Qt.lightGray)
            self._placeholder = pm
        return self._placeholder


class ImageGrid(QListView):
    selection_changed = Signal(list)         # list[str] of absolute paths
    icon_size_changed = Signal(int)          # display size in pixels

    def __init__(self, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self._model = ImageFolderModel(cache, self)
        self.setModel(self._model)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setSpacing(8)
        self.setSelectionMode(QListView.ExtendedSelection)
        self.setItemDelegate(_IconOnlyDelegate(self))
        # Start at 0 so the first set_icon_size call is never a no-op.
        self._icon_size: int = 0
        self.set_icon_size(DEFAULT_ICON_SIZE)

    # -- Public API ----------------------------------------------------
    def set_folder(self, folder: str) -> None:
        self._model.set_folder(folder)

    def set_icon_size(self, px: int) -> None:
        px = max(MIN_ICON_SIZE, min(MAX_ICON_SIZE, int(px)))
        if px == self._icon_size:
            return
        self._icon_size = px
        self._model.set_icon_size(px)
        self.setIconSize(QSize(px, px))
        self.setGridSize(QSize(px + GRID_PADDING, px + GRID_PADDING))
        self.icon_size_changed.emit(px)

    def icon_size(self) -> int:
        return self._icon_size

    def zoom_in(self) -> None:
        self.set_icon_size(self._stepped(ZOOM_STEP))

    def zoom_out(self) -> None:
        self.set_icon_size(self._stepped(1.0 / ZOOM_STEP))

    def reset_zoom(self) -> None:
        self.set_icon_size(DEFAULT_ICON_SIZE)

    def _stepped(self, factor: float) -> int:
        """Apply ``factor`` to the current size, guaranteeing movement of at
        least one pixel so a step can never round back to where it started."""
        new = round(self._icon_size * factor)
        if new == self._icon_size:
            new = self._icon_size + (1 if factor > 1.0 else -1)
        return new

    def paths(self) -> list[str]:
        return self._model.paths()

    def all_paths(self) -> list[str]:
        return self._model.all_paths()

    def visible_count(self) -> int:
        """Rows the view is showing — the folder minus whatever the filter
        hides. Counterpart to ``total_count()``; see the note there."""
        return self._model.rowCount()

    def total_count(self) -> int:
        return self._model.total_count()

    def set_filter(self, predicate: Callable[[str], bool] | None) -> None:
        self._model.set_filter(predicate)

    def refilter_paths(self, paths: list[str]) -> None:
        self._model.refilter_paths(paths)

    def selected_paths(self) -> list[str]:
        paths = []
        for idx in self.selectionModel().selectedIndexes():
            p = self._model.path_at(idx)
            if p:
                paths.append(p)
        return paths

    def remove_paths(self, paths: list[str]) -> None:
        """Remove ``paths`` from the view and land the selection on whatever
        now occupies the first removed position, so repeated deletes cull
        forward through a folder without touching the mouse."""
        if not paths:
            return
        targets = {path_key(p) for p in paths}
        first_row = min(
            (
                i
                for i, p in enumerate(self._model.paths())
                if path_key(p) in targets
            ),
            default=None,
        )
        self._model.remove_paths(paths)
        remaining = self._model.rowCount()
        if first_row is None or remaining == 0:
            self.clearSelection()
            self.selection_changed.emit([])
            return
        self.setCurrentIndex(self._model.index(min(first_row, remaining - 1), 0))

    def insert_paths(self, paths: list[str]) -> None:
        """Re-insert ``paths`` and select them, so an undo shows what came
        back. Paths outside the current folder are dropped by the model."""
        if not paths:
            return
        self._model.insert_paths(paths)
        wanted = {os.path.normcase(os.path.basename(p)) for p in paths}
        sm = self.selectionModel()
        sm.clearSelection()
        first = None
        for row, p in enumerate(self._model.paths()):
            if os.path.normcase(os.path.basename(p)) not in wanted:
                continue
            idx = self._model.index(row, 0)
            if first is None:
                first = idx
                # NoUpdate, and set before the selecting loop: the view's
                # setCurrentIndex would reset the selection to this one item,
                # discarding the rest of a multi-image undo.
                sm.setCurrentIndex(
                    idx, QItemSelectionModel.SelectionFlag.NoUpdate
                )
            sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
        if first is not None:
            self.scrollTo(first)

    # -- Qt overrides --------------------------------------------------
    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            sm = self.selectionModel()
            if sm is not None and sm.hasSelection():
                # clearCurrentIndex as well, or the focus rectangle stays
                # behind on the last item and the grid still looks as though
                # something is picked. selection_changed([]) follows from the
                # selectionChanged override below, so nothing else needs doing.
                self.clearSelection()
                sm.clearCurrentIndex()
                event.accept()
                return
            # Nothing selected: leave Escape to whatever else may want it.
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                # 120 units == one notch on a conventional mouse wheel.
                # Using the raw delta lets trackpads with finer resolution
                # zoom proportionally instead of jumping a whole step.
                self.set_icon_size(self._stepped(ZOOM_STEP ** (delta / 120.0)))
            event.accept()
            return
        super().wheelEvent(event)

    def selectionChanged(self, selected, deselected) -> None:  # type: ignore[override]
        super().selectionChanged(selected, deselected)
        self.selection_changed.emit(self.selected_paths())
