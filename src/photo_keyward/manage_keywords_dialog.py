from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .exif_cache import ExifCache
from .keyword_import import KeywordImporter
from .keyword_store import KeywordStore, keyword_sort_key

# How many new keywords the confirmation lists by name before summarising.
_PREVIEW_LIMIT = 15

_FLASH_STYLE = "QLineEdit { background-color: #fdd; }"


def _format_keyword(text: str) -> str:
    if "|" in text:
        return " ▸ ".join(text.split("|"))
    return text


class ManageKeywordsDialog(QDialog):
    """Modal: tick which global keywords appear in the current folder, add
    new ones, delete existing ones (vocab-only)."""

    def __init__(
        self,
        store: KeywordStore,
        current_folder: str | None,
        cache: ExifCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Keywords")
        self.resize(500, 600)
        self._store = store
        self._current_folder = current_folder
        self._cache = cache
        self._importer: KeywordImporter | None = None
        self._progress: QProgressDialog | None = None
        if current_folder is not None:
            self._working_subset: set[str] = set(
                store.get_folder_subset(current_folder)
            )
        else:
            self._working_subset = set()

        self._list = QListWidget()
        self._populate()

        self._input = QLineEdit()
        self._input.setPlaceholderText("Add keyword (use '|' for hierarchy)")
        self._input.returnPressed.connect(self._on_add)

        self._import_btn = QPushButton("Import from folder…")
        self._import_btn.clicked.connect(self._on_import)
        if current_folder is None:
            self._import_btn.setEnabled(False)
            self._import_btn.setToolTip("Open a folder first.")
        else:
            self._import_btn.setToolTip(
                "Add every keyword already on this folder's images to the "
                "vocabulary."
            )

        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self._import_btn)
        btns.addStretch(1)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Tick keywords to show in this folder. Click ✕ to delete "
                "from the vocabulary."
            )
        )
        layout.addWidget(self._list, 1)
        layout.addWidget(self._input)
        layout.addLayout(btns)

    def _populate(self) -> None:
        self._list.clear()
        for kw in self._store.list_all():
            self._add_row(kw)

    def _add_row(self, kw: str) -> None:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(4, 2, 4, 2)

        checkbox = QCheckBox()
        checkbox.setChecked(kw in self._working_subset)
        checkbox.toggled.connect(
            lambda checked, k=kw: self._on_check(k, checked)
        )

        label = QLabel(_format_keyword(kw))
        if "|" in kw:
            f = QFont(label.font())
            f.setItalic(True)
            label.setFont(f)

        delete_btn = QPushButton("✕")
        delete_btn.setMaximumWidth(28)
        delete_btn.setToolTip("Delete from vocabulary")
        delete_btn.clicked.connect(lambda _checked=False, k=kw: self._on_delete(k))

        row_layout.addWidget(checkbox)
        row_layout.addWidget(label, 1)
        row_layout.addWidget(delete_btn)

        item = QListWidgetItem()
        item.setSizeHint(row_widget.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, row_widget)

    def _on_check(self, kw: str, checked: bool) -> None:
        if checked:
            self._working_subset.add(kw)
        else:
            self._working_subset.discard(kw)

    def _on_delete(self, kw: str) -> None:
        # Always ask. ✕ deletes from the global vocabulary and strips the
        # keyword from every folder's subset, so it costs the same whether or
        # not this folder happens to show it -- and the button sits one row
        # away from the checkbox.
        ret = QMessageBox.question(
            self,
            "Delete keyword",
            f"Delete '{kw}' from the vocabulary?\n\n"
            "Images that already have this keyword will keep it. "
            "It will simply stop appearing in any folder's panel.",
        )
        if ret != QMessageBox.Yes:
            return
        self._store.remove(kw)
        self._working_subset.discard(kw)
        self._populate()

    def _on_add(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        ok = self._store.add(text)
        if not ok:
            self._flash_input()
            return
        self._working_subset.add(text)
        self._populate()
        self._input.clear()

    def _flash_input(self) -> None:
        self._input.setStyleSheet(_FLASH_STYLE)
        QTimer.singleShot(600, lambda: self._input.setStyleSheet(""))

    # -- import --------------------------------------------------------
    def _on_import(self) -> None:
        if self._current_folder is None:
            return
        self._discard_importer()
        self._import_btn.setEnabled(False)
        self._importer = KeywordImporter(self._cache, self)
        self._importer.finished.connect(self._on_import_finished)
        self._importer.failed.connect(self._on_import_failed)
        unread = self._importer.start(self._current_folder)
        if not unread:
            # Everything is cached; the result lands on the next turn of the
            # event loop, too fast for a progress dialog to be anything but a
            # flicker.
            return
        # A single exiftool pass covers the whole batch, so there is no
        # per-file progress to report -- range 0,0 says "working" honestly
        # rather than inventing a percentage.
        self._progress = QProgressDialog(
            f"Reading EXIF for {unread} image{'' if unread == 1 else 's'}…",
            "Cancel",
            0,
            0,
            self,
        )
        self._progress.setWindowTitle("Import keywords")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.canceled.connect(self._on_import_cancelled)
        self._progress.show()

    def _close_progress(self) -> None:
        if self._progress is None:
            return
        # canceled fires on close() too, and would re-enter the cancel path
        # after a perfectly successful read.
        self._progress.canceled.disconnect(self._on_import_cancelled)
        self._progress.close()
        self._progress = None

    def _discard_importer(self) -> None:
        if self._importer is None:
            return
        self._importer.shutdown()
        self._importer.deleteLater()
        self._importer = None

    def _on_import_cancelled(self) -> None:
        self._progress = None  # QProgressDialog closes itself on cancel
        self._discard_importer()
        self._import_btn.setEnabled(True)

    def _on_import_failed(self, message: str) -> None:
        self._close_progress()
        self._import_btn.setEnabled(True)
        QMessageBox.warning(self, "Import keywords", message)

    def _on_import_finished(self, found: set) -> None:
        self._close_progress()
        self._import_btn.setEnabled(True)
        if not found:
            QMessageBox.information(
                self,
                "Import keywords",
                "No keywords found on this folder's images.",
            )
            return
        new = sorted(
            (k for k in found if not self._store.contains(k)),
            key=keyword_sort_key,
        )
        if not new:
            QMessageBox.information(
                self,
                "Import keywords",
                f"All {len(found)} keywords on this folder's images are "
                "already in the vocabulary.",
            )
            return
        shown = [f"  • {_format_keyword(k)}" for k in new[:_PREVIEW_LIMIT]]
        if len(new) > _PREVIEW_LIMIT:
            shown.append(f"  … and {len(new) - _PREVIEW_LIMIT} more")
        ret = QMessageBox.question(
            self,
            "Import keywords",
            f"Found {len(found)} keywords on this folder's images, "
            f"{len(new)} not yet in the vocabulary:\n\n"
            + "\n".join(shown)
            + "\n\nAdd them to the vocabulary?",
        )
        if ret != QMessageBox.Yes:
            return
        added = self._store.add_many(new)
        # Ticked, not merely added: they came from this folder's own images,
        # so this is the one folder certain to want them on its panel.
        self._working_subset.update(added)
        self._populate()

    def done(self, r: int) -> None:  # type: ignore[override]
        # A read still running owns a thread parented to this dialog. Letting
        # the dialog be destroyed out from under it is a crash, not a leak.
        self._close_progress()
        self._discard_importer()
        super().done(r)

    def _on_ok(self) -> None:
        if self._current_folder is not None:
            ordered = [
                kw for kw in self._store.list_all() if kw in self._working_subset
            ]
            self._store.set_folder_subset(self._current_folder, ordered)
        self.accept()
