from __future__ import annotations

from PySide6.QtCore import QDir, QModelIndex, Signal
from PySide6.QtWidgets import QFileSystemModel, QTreeView


class FolderTree(QTreeView):
    folder_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = QFileSystemModel(self)
        self._model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        self._model.setRootPath("")
        self.setModel(self._model)

        # Show only the name column; folders only.
        for col in range(1, self._model.columnCount()):
            self.setColumnHidden(col, True)
        self.setHeaderHidden(True)

        self.selectionModel().currentChanged.connect(self._on_current_changed)

    def select_path(self, path: str) -> None:
        idx = self._model.index(path)
        if idx.isValid():
            self.setCurrentIndex(idx)
            self.scrollTo(idx)
            self.expand(idx)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            return
        path = self._model.filePath(current)
        if path:
            self.folder_selected.emit(path)
