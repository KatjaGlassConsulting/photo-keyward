from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    APP_NAME,
    APP_ORG,
    DEFAULT_ICON_SIZE,
    LEGACY_ICON_SIZE_KEYS,
    MAX_ICON_SIZE,
    MIN_ICON_SIZE,
    config_dir,
    path_key,
)
from .exif_cache import ExifCache
from .exif_reader import ExifReader
from .folder_tree import FolderTree
from .image_grid import ImageGrid
from .keyword_filter import KeywordFilter
from .keyword_filter_panel import KeywordFilterPanel
from .keyword_panel import KeywordPanel
from .keyword_store import KeywordStore
from .keyword_writer import KeywordWriter
from .path_bar import PathBar
from .preview_strip import PreviewStrip
from .thumbnail_cache import ThumbnailCache
from .trash import (
    UNDO_DEPTH,
    MovedGroup,
    RestoreResult,
    TrashResult,
    move_to_trash,
    restore_from_trash,
)
from .warning_dialog import WarningDialog
from .warning_indicator import WarningIndicator
from .warning_log import WarningLog
from .scripts import (
    export_dates as scripts_export_dates,
    export_metadata as scripts_export_metadata,
    import_dates as scripts_import_dates,
    import_metadata as scripts_import_metadata,
    set_date_from_filename as scripts_set_date_from_filename,
    set_date_from_metadata as scripts_set_date_from_metadata,
)
from .scripts._result import ScriptResult
from .scripts._runner import ScriptRunner


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Photo Keyward")
        self.resize(1280, 800)

        self._settings = QSettings(APP_ORG, APP_NAME)
        self._cache = ThumbnailCache(self)
        self._exif_cache = ExifCache(self)
        self._exif_reader = ExifReader(self)

        self._warning_log = WarningLog(self)
        self._keyword_store = KeywordStore(config_dir(), self)
        self._keyword_writer = KeywordWriter(self)
        self._script_runner = ScriptRunner(self)
        self._current_folder: Path | None = None
        self._script_running_action: QAction | None = None
        self._active_filter: KeywordFilter | None = None
        self._selection_count = 0
        # Outer splitter sizes as they were when the detail area was last
        # hidden, so showing it again does not bring it back at height 0.
        self._preview_sizes: list[int] | None = None
        # One entry per Del press, newest last. Each Ctrl+Z pops exactly one,
        # so three presses step back through three deletes rather than one
        # press reversing all of them. maxlen does the eviction, so bounding
        # the history needs no bookkeeping of its own.
        self._undo_stack: deque[tuple[MovedGroup, ...]] = deque(maxlen=UNDO_DEPTH)
        self._keyword_store.load_error.connect(
            lambda msg: self._warning_log.add(msg, "keyword store")
        )
        self._keyword_store.initialize()

        self._tree = FolderTree()
        self._filter_panel = KeywordFilterPanel(
            self._keyword_store, self._exif_cache
        )
        self._left_tabs = QTabWidget()
        self._left_tabs.addTab(self._tree, "Explorer")
        self._left_tabs.addTab(self._filter_panel, "Keys")
        self._grid = ImageGrid(self._cache)
        self._preview = PreviewStrip(self._exif_cache)
        self._path_bar = PathBar()
        self._keyword_panel = KeywordPanel(
            self._keyword_store, self._exif_cache, self._keyword_writer
        )
        self._warning_indicator = WarningIndicator()

        self._top_splitter = QSplitter(Qt.Horizontal)
        self._top_splitter.addWidget(self._left_tabs)
        self._top_splitter.addWidget(self._grid)
        self._top_splitter.addWidget(self._keyword_panel)
        self._top_splitter.setStretchFactor(0, 0)
        self._top_splitter.setStretchFactor(1, 1)
        self._top_splitter.setStretchFactor(2, 0)
        self._top_splitter.setSizes([280, 720, 280])

        self._outer_splitter = QSplitter(Qt.Vertical)
        self._outer_splitter.addWidget(self._top_splitter)
        self._outer_splitter.addWidget(self._preview)
        self._outer_splitter.setStretchFactor(0, 1)
        self._outer_splitter.setStretchFactor(1, 0)
        self._outer_splitter.setSizes([600, 220])

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._path_bar)
        central_layout.addWidget(self._outer_splitter, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar(self))
        # A permanent widget rather than showMessage: the transient notices
        # this window emits would otherwise wipe the count seconds after every
        # folder change.
        self._count_label = QLabel()
        self.statusBar().addPermanentWidget(self._count_label)
        self.statusBar().addPermanentWidget(self._warning_indicator)
        self._warning_indicator.clicked.connect(self._on_show_warnings)
        self._warning_log.warning_added.connect(self._warning_indicator.set_count)

        self._build_menu()
        self._wire_signals()
        self._restore_state()
        self._update_count_label()

    # -- Setup ---------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = self.menuBar()
        edit_menu = menubar.addMenu("&Edit")
        self._trash_action = QAction("Move to &Trash", self)
        self._trash_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        self._trash_action.setEnabled(False)
        self._trash_action.triggered.connect(self._on_move_to_trash)
        edit_menu.addAction(self._trash_action)

        self._undo_trash_action = QAction("&Undo Move to Trash", self)
        self._undo_trash_action.setShortcut(
            QKeySequence(QKeySequence.StandardKey.Undo)
        )
        self._undo_trash_action.setEnabled(False)
        self._undo_trash_action.triggered.connect(self._on_undo_trash)
        edit_menu.addAction(self._undo_trash_action)

        view_menu = menubar.addMenu("&View")
        self._zoom_in_action = QAction("Zoom &In", self)
        self._zoom_in_action.setShortcuts([
            QKeySequence(QKeySequence.StandardKey.ZoomIn),
            QKeySequence("Ctrl+="),
        ])
        self._zoom_in_action.triggered.connect(self._grid.zoom_in)
        view_menu.addAction(self._zoom_in_action)

        self._zoom_out_action = QAction("Zoom &Out", self)
        self._zoom_out_action.setShortcuts([
            QKeySequence(QKeySequence.StandardKey.ZoomOut),
            QKeySequence("Ctrl+-"),
        ])
        self._zoom_out_action.triggered.connect(self._grid.zoom_out)
        view_menu.addAction(self._zoom_out_action)

        self._reset_zoom_action = QAction("&Reset Zoom", self)
        self._reset_zoom_action.setShortcut(QKeySequence("Ctrl+0"))
        self._reset_zoom_action.triggered.connect(self._grid.reset_zoom)
        view_menu.addAction(self._reset_zoom_action)

        view_menu.addSeparator()
        self._toggle_preview_action = QAction("Show &Details Panel", self)
        self._toggle_preview_action.setCheckable(True)
        self._toggle_preview_action.setChecked(True)
        self._toggle_preview_action.setShortcut(QKeySequence("Ctrl+D"))
        self._toggle_preview_action.toggled.connect(self._on_toggle_preview)
        view_menu.addAction(self._toggle_preview_action)

        tools_menu = menubar.addMenu("&Tools")
        self._read_exif_action = QAction("Read EXIF", self)
        self._read_exif_action.setShortcut(QKeySequence("Ctrl+E"))
        self._read_exif_action.triggered.connect(self._on_read_exif)
        tools_menu.addAction(self._read_exif_action)

        scripts_menu = menubar.addMenu("&Scripts")
        self._script_actions: dict[str, QAction] = {}

        action_specs = [
            ("export_dates",            "Export Dates from ORF",      scripts_export_dates.run),
            ("import_dates",            "Import Dates to JPG",        scripts_import_dates.run),
            ("export_metadata",         "Export Metadata",            scripts_export_metadata.run),
            ("import_metadata",         "Import Metadata",            scripts_import_metadata.run),
            ("set_date_from_filename",  "Set Date from Filename",     scripts_set_date_from_filename.run),
            ("set_date_from_metadata",  "Set Date from Metadata",     scripts_set_date_from_metadata.run),
        ]
        for key, label, fn in action_specs:
            act = QAction(label, self)
            act.triggered.connect(lambda _checked=False, k=key, f=fn, l=label: self._on_script_triggered(k, l, f))
            scripts_menu.addAction(act)
            self._script_actions[key] = act

    def _wire_signals(self) -> None:
        # Order matters: Qt calls slots in connection order, and
        # _on_folder_selected reads grid.all_paths() to start the auto-read,
        # so the grid must have loaded the folder first. The panel's reset
        # then re-emits an unfiltered filter, which is a no-op.
        self._tree.folder_selected.connect(self._grid.set_folder)
        self._tree.folder_selected.connect(self._filter_panel.set_folder)
        self._tree.folder_selected.connect(self._on_folder_selected)
        self._tree.folder_selected.connect(self._path_bar.set_current_path)

        self._path_bar.path_entered.connect(self._on_path_entered)

        self._grid.selection_changed.connect(self._preview.update_selection)
        self._grid.selection_changed.connect(self._on_selection_for_trash)
        self._grid.selection_changed.connect(self._on_selection_for_count)
        self._grid.icon_size_changed.connect(self._on_icon_size_changed)

        self._exif_reader.read_complete.connect(self._on_exif_done)
        self._exif_reader.read_failed.connect(self._on_exif_failed)

        self._tree.folder_selected.connect(self._keyword_panel.set_current_folder)
        self._grid.selection_changed.connect(self._keyword_panel.set_selection)

        self._keyword_writer.write_started.connect(self._on_write_started)
        self._keyword_writer.write_complete.connect(self._on_write_complete)
        self._keyword_writer.write_failed.connect(self._on_write_failed)

        self._exif_reader.read_failed.connect(
            lambda msg: self._warning_log.add(msg, "exif read")
        )

        self._script_runner.progress.connect(self._on_script_progress)
        self._script_runner.complete.connect(self._on_script_complete)
        self._script_runner.failed.connect(self._on_script_failed)

        self._filter_panel.filter_changed.connect(self._on_filter_changed)
        # Connected after _on_filter_changed, and separately from it: Qt runs
        # slots in connection order, and _on_filter_changed returns from
        # several places, only some of which touched the grid.
        self._filter_panel.filter_changed.connect(
            lambda _flt: self._update_count_label()
        )
        self._exif_cache.cache_updated.connect(self._on_cache_updated)

    # -- Settings ------------------------------------------------------
    def _load_icon_size(self) -> int:
        """Resolve the stored thumbnail size. Installations predating
        continuous zoom stored a name ("medium") rather than a pixel count."""
        raw = self._settings.value("view/icon_size", DEFAULT_ICON_SIZE)
        if isinstance(raw, str) and raw in LEGACY_ICON_SIZE_KEYS:
            return LEGACY_ICON_SIZE_KEYS[raw]
        try:
            px = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_ICON_SIZE
        return max(MIN_ICON_SIZE, min(MAX_ICON_SIZE, px))

    def _load_preview_visible(self) -> bool:
        """QSettings hands booleans back as the string "true"/"false" on the
        Windows registry backend, so isinstance is not enough on its own."""
        raw = self._settings.value("view/preview_visible", True)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in ("false", "0", "")

    def _restore_state(self) -> None:
        geom = self._settings.value("window/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = self._settings.value("window/state")
        if state is not None:
            self.restoreState(state)

        sizes_value = self._settings.value("view/top_splitter_sizes")
        if sizes_value:
            try:
                sizes = [int(x) for x in sizes_value]
            except (TypeError, ValueError):
                sizes = []
            if len(sizes) == 3 and all(s > 0 for s in sizes):
                self._top_splitter.setSizes(sizes)

        self._grid.set_icon_size(self._load_icon_size())

        # setChecked only emits toggled when the value actually changes, so
        # the visible default costs nothing and the hidden case runs the same
        # handler a menu click would.
        self._toggle_preview_action.setChecked(self._load_preview_visible())

        last_folder = self._settings.value("view/last_folder", "")
        if last_folder and Path(last_folder).is_dir():
            self._tree.select_path(last_folder)
            # select_path normally emits folder_selected, which runs the whole
            # chain including the read. It stays silent when QFileSystemModel
            # has not resolved the path yet, so fall back explicitly -- but
            # only then, or startup runs a second full exiftool pass.
            if self._current_folder is None:
                self._open_folder(last_folder)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())
        self._settings.setValue(
            "view/top_splitter_sizes", self._top_splitter.sizes()
        )
        self._keyword_writer.shutdown()
        self._script_runner.shutdown()
        self._exif_reader.shutdown()
        super().closeEvent(event)

    # -- Setup helpers ---------------------------------------------------
    def _open_folder(self, path: str) -> None:
        """Everything a folder change entails, for the paths that do not come
        through the tree's own signal. Ordered like the signal chain: grid
        and filter panel first, so all_paths() is populated for the read,
        then the rest in the order folder_selected's own connections run."""
        self._grid.set_folder(path)
        self._filter_panel.set_folder(path)
        self._current_folder = Path(path)
        self._settings.setValue("view/last_folder", path)
        self._exif_reader.read_paths(self._grid.all_paths())
        self._path_bar.set_current_path(path)
        self._keyword_panel.set_current_folder(path)
        self._update_count_label()

    # -- Slots ---------------------------------------------------------
    def _on_folder_selected(self, path: str) -> None:
        self._current_folder = Path(path) if path else None
        self._settings.setValue("view/last_folder", path)
        # Keyword filtering is only meaningful once the folder's EXIF is known,
        # and requiring Ctrl+E first would make the Keys tab look broken.
        # ExifReader queues this if a read is already running.
        self._exif_reader.read_paths(self._grid.all_paths())
        self._update_count_label()

    def _on_path_entered(self, path: str) -> None:
        # Typing the already-open folder and pressing Enter: setCurrentIndex
        # on the already-current index emits nothing, so the `is` check below
        # would hold and _open_folder would run the whole chain for no
        # reason -- resetting the Keys panel to all-checked and starting a
        # full exiftool pass, silently discarding an active filter. Compare
        # by path_key (not the raw string: the tree, the path bar and
        # _current_folder can each spell the same folder differently) and do
        # nothing when it is the folder already open.
        if self._current_folder is not None and path_key(path) == path_key(
            str(self._current_folder)
        ):
            return
        before = self._current_folder
        self._tree.select_path(path)
        # select_path emits folder_selected when the path resolves, which
        # reassigns _current_folder to a new Path object via
        # _on_folder_selected -- an `is` check tells that apart from the path
        # staying unresolved, where nothing ran and the grid would otherwise
        # be showing the new folder under the previous folder's predicate.
        if self._current_folder is before:
            self._open_folder(path)

    def _on_icon_size_changed(self, px: int) -> None:
        self._settings.setValue("view/icon_size", px)
        self.statusBar().showMessage(f"Thumbnail size: {px} px", 1500)

    def _on_toggle_preview(self, visible: bool) -> None:
        """Hiding a widget inside a QSplitter collapses its pane. Rather than
        trust the splitter to remember the height across the round trip, the
        sizes are captured going out and re-applied coming back."""
        if visible:
            self._preview.setVisible(True)
            if self._preview_sizes is not None:
                self._outer_splitter.setSizes(self._preview_sizes)
        else:
            sizes = self._outer_splitter.sizes()
            if len(sizes) == 2 and sizes[1] > 0:
                self._preview_sizes = sizes
            self._preview.setVisible(False)
        self._settings.setValue("view/preview_visible", visible)

    def _on_selection_for_count(self, paths: list) -> None:
        self._selection_count = len(paths)
        self._update_count_label()

    def _update_count_label(self) -> None:
        total = self._grid.total_count()
        if total == 0:
            self._count_label.setText("No images")
            return
        if self._selection_count:
            self._count_label.setText(
                f"{self._selection_count} of {total} selected"
            )
            return
        # Compared against the total rather than reading _active_filter: what
        # matters is whether the user is being shown less than the folder
        # holds, which an all-selected filter is not.
        shown = self._grid.visible_count()
        if shown != total:
            self._count_label.setText(f"{shown} of {total} images")
        else:
            self._count_label.setText(f"{total} images")

    def _on_filter_changed(self, flt: KeywordFilter) -> None:
        # set_filter resets the model, which drops the selection and scrolls
        # to the top. Anything that rebuilds the panel re-emits, so an
        # unchanged filter must not reach the grid.
        prev = self._active_filter
        self._active_filter = flt
        # What reaches the grid is the predicate, not the filter's identity.
        # Every all-selected filter maps to None however its key set differs,
        # and the auto-read routinely produces a new key set with
        # all_selected still true -- so comparing the KeywordFilters
        # themselves would reset the model moments after every folder
        # change, once the read populates keywords the panel didn't know
        # about yet.
        if flt.all_selected:
            if prev is None or not prev.all_selected:
                # None rather than an always-true predicate: the model then
                # skips its rebuild loop entirely, so unfiltered browsing
                # stays as fast as it was before the filter existed.
                self._grid.set_filter(None)
            return
        if flt == prev:
            return
        self._grid.set_filter(lambda p: flt.accepts(self._exif_cache.get(p)))
        shown = len(self._grid.paths())
        total = len(self._grid.all_paths())
        self.statusBar().showMessage(
            f"Filter: {shown} of {total} images", 4000
        )

    def _on_cache_updated(self, paths: set[str]) -> None:
        """Keep the grid consistent when keywords change under it. Incremental,
        so the view does not jump while the user is tagging."""
        self._grid.refilter_paths(list(paths))
        self._update_count_label()

    def _on_read_exif(self) -> None:
        paths = self._grid.all_paths()
        if not paths:
            self.statusBar().showMessage("Read EXIF: no images in this folder", 4000)
            return
        self.statusBar().showMessage(f"Reading EXIF for {len(paths)} images…")
        self._exif_reader.read_paths(paths)

    def _on_exif_done(self, results: dict) -> None:
        populated = sum(
            1 for d in results.values()
            if d.date_taken is not None or d.keywords
        )
        self._exif_cache.put_many(results)
        self.statusBar().showMessage(
            f"EXIF: {populated} / {len(results)} images", 4000
        )

    def _on_exif_failed(self, reason: str) -> None:
        self.statusBar().showMessage(reason, 8000)

    def _on_write_started(self, op_id: int, keyword: str, n: int) -> None:
        self.statusBar().showMessage(f"Writing '{keyword}' to {n} image(s)…")

    def _on_write_complete(
        self, op_id: int, keyword: str, paths: list
    ) -> None:
        self._exif_cache.refresh_mtime(paths)
        self.statusBar().showMessage(
            f"Wrote '{keyword}' to {len(paths)} image(s)", 4000
        )

    def _on_write_failed(
        self, op_id: int, keyword: str, paths: list, reason: str
    ) -> None:
        self._warning_log.add(f"{keyword}: {reason}", "keyword write")
        self.statusBar().showMessage(
            f"Failed to write '{keyword}': {reason[:120]}", 8000
        )
        # Re-read to restore truth into the cache. Known gap: under
        # newest-wins (ExifReader.read_paths), a folder change can supersede
        # this request before it runs, leaving the cache holding keywords
        # that are not actually on disk -- and the filter now acts on
        # exactly that cache. Not fixed here; noted so it is not mistaken
        # for accidental.
        if paths:
            self._exif_reader.read_paths(paths)

    def _on_show_warnings(self) -> None:
        WarningDialog(self._warning_log, self).exec()

    # -- Trash ---------------------------------------------------------
    def _on_selection_for_trash(self, paths: list) -> None:
        self._trash_action.setEnabled(bool(paths))

    def _on_move_to_trash(self) -> None:
        paths = self._grid.selected_paths()
        if not paths:
            return
        result = move_to_trash(paths)
        if result.groups:
            self._grid.remove_paths(list(result.moved))
            # Only pushed when something actually moved, so a press that
            # skipped everything leaves Ctrl+Z pointing at the last delete
            # that did something.
            self._undo_stack.append(result.groups)
            self._undo_trash_action.setEnabled(True)
        for path, reason in result.skipped:
            self._warning_log.add(f"{Path(path).name}: {reason}", "trash")
        # Explicit: a delete whose rows the filter was hiding changes the total
        # without the grid emitting anything.
        self._update_count_label()
        self.statusBar().showMessage(self._trash_message(result), 5000)

    def _on_undo_trash(self) -> None:
        """Reverse exactly one Del press — the most recent still on the
        stack. A second press reaches the one before it."""
        if not self._undo_stack:
            return
        # Popped whatever the outcome. Leaving a partially restored entry on
        # the stack would make a second Ctrl+Z retry the same failures rather
        # than step back to the previous delete, so how far back the history
        # reached would depend on how many collisions occurred.
        groups = self._undo_stack.pop()
        self._undo_trash_action.setEnabled(bool(self._undo_stack))
        result = restore_from_trash(groups)
        if result.restored:
            self._grid.insert_paths(list(result.restored))
        for path, reason in result.skipped:
            self._warning_log.add(f"{Path(path).name}: {reason}", "trash")
        self._update_count_label()
        self.statusBar().showMessage(self._restore_message(result), 5000)

    @staticmethod
    def _trash_message(result: TrashResult) -> str:
        if not result.moved and not result.skipped:
            return "Nothing to move to trash"
        if not result.moved:
            return f"Moved nothing — skipped {len(result.skipped)}, see warnings"
        msg = f"Moved {len(result.moved)} image(s)"
        if result.companions_moved:
            msg += f" (+{result.companions_moved} companion file(s))"
        msg += " to trash"
        if result.skipped:
            msg += f", skipped {len(result.skipped)} — see warnings"
        return msg

    @staticmethod
    def _restore_message(result: RestoreResult) -> str:
        if not result.restored and not result.skipped:
            return "Nothing to restore"
        if not result.restored:
            return f"Restored nothing — skipped {len(result.skipped)}, see warnings"
        folders = {Path(p).parent.name for p in result.restored}
        where = folders.pop() if len(folders) == 1 else "their original folders"
        msg = f"Restored {len(result.restored)} image(s)"
        if result.companions_restored:
            msg += f" (+{result.companions_restored} companion file(s))"
        msg += f" to {where}"
        if result.skipped:
            msg += f", skipped {len(result.skipped)} — see warnings"
        return msg

    # -- Scripts -------------------------------------------------------
    def _on_script_triggered(self, key: str, label: str, fn) -> None:
        if self._current_folder is None or not self._current_folder.is_dir():
            self.statusBar().showMessage("Scripts: no folder selected", 4000)
            return
        if self._script_runner.busy:
            self.statusBar().showMessage("Scripts: another script is running", 4000)
            return
        action = self._script_actions[key]
        action.setEnabled(False)
        self._script_running_action = action
        self._running_script_label = label
        self._running_script_key = key
        self.statusBar().showMessage(f"Running {label}…")
        self._script_runner.start(fn, self._current_folder)

    def _on_script_progress(self, done: int, total: int) -> None:
        label = getattr(self, "_running_script_label", "Script")
        self.statusBar().showMessage(f"{label}: {done}/{total}")

    def _on_script_complete(self, result: ScriptResult) -> None:
        label = getattr(self, "_running_script_label", "Script")
        key = getattr(self, "_running_script_key", "")
        if result.error is not None:
            self._warning_log.add(f"{label}: {result.error}", "scripts")
            self.statusBar().showMessage(
                f"{label} failed: {result.error[:120]}", 8000
            )
        else:
            self.statusBar().showMessage(
                f"{label} done — {result.processed} processed, "
                f"{result.skipped} skipped, {len(result.warnings)} warnings",
                6000,
            )
        for w in result.warnings:
            self._warning_log.add(f"{label}: {w}", "scripts")

        # If the script wrote to JPGs in the current folder, refresh cache.
        if key in ("import_dates", "import_metadata", "set_date_from_filename"):
            folder = self._current_folder
            if folder is not None and folder.is_dir():
                jpgs = [
                    str(p) for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                ]
                if jpgs:
                    self._exif_reader.read_paths(jpgs)

        if self._script_running_action is not None:
            self._script_running_action.setEnabled(True)
            self._script_running_action = None

    def _on_script_failed(self, msg: str) -> None:
        label = getattr(self, "_running_script_label", "Script")
        self._warning_log.add(f"{label}: {msg}", "scripts")
        self.statusBar().showMessage(f"{label} crashed: {msg[:120]}", 8000)
        if self._script_running_action is not None:
            self._script_running_action.setEnabled(True)
            self._script_running_action = None
