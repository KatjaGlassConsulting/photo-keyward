from __future__ import annotations

import os

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .constants import PREVIEW_BOX_H, PREVIEW_BOX_W, PREVIEW_MAX_PX
from .exif_cache import ExifCache, ExifData


def _human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


class PreviewStrip(QWidget):
    def __init__(self, exif_cache: ExifCache | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(PREVIEW_BOX_H + 32)

        self._exif_cache = exif_cache
        self._current_path: str | None = None

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setFixedSize(PREVIEW_BOX_W, PREVIEW_BOX_H)
        self._image_label.setStyleSheet("background-color: #222;")

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-weight: bold;")
        self._meta_label = QLabel()
        self._meta_label.setStyleSheet("color: #555;")
        self._path_label = QLabel()
        self._path_label.setStyleSheet("color: #777; font-size: 11px;")
        self._path_label.setWordWrap(True)
        self._date_label = QLabel()
        self._date_label.setStyleSheet("color: #444;")
        self._keywords_label = QLabel()
        self._keywords_label.setStyleSheet("color: #444;")
        self._keywords_label.setWordWrap(True)
        self._hierarchy_label = QLabel()
        self._hierarchy_label.setStyleSheet("color: #444;")
        self._hierarchy_label.setWordWrap(True)

        info = QVBoxLayout()
        info.setContentsMargins(12, 8, 12, 8)
        info.addWidget(self._name_label)
        info.addWidget(self._meta_label)
        info.addWidget(self._path_label)
        info.addWidget(self._date_label)
        info.addWidget(self._keywords_label)
        info.addWidget(self._hierarchy_label)
        info.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._image_label, 0)
        layout.addLayout(info, 1)

        if self._exif_cache is not None:
            self._exif_cache.cache_updated.connect(self._on_cache_updated)

        self.show_empty()

    def show_empty(self) -> None:
        self._current_path = None
        self._image_label.clear()
        self._image_label.setText("Select an image to preview")
        self._image_label.setStyleSheet("background-color: #222; color: #aaa;")
        self._name_label.setText("")
        self._meta_label.setText("")
        self._path_label.setText("")
        self._date_label.setText("")
        self._date_label.setVisible(False)
        self._keywords_label.setText("")
        self._keywords_label.setVisible(False)
        self._hierarchy_label.setText("")
        self._hierarchy_label.setVisible(False)

    def show_multi(self, paths: list[str]) -> None:
        self._current_path = None
        total = 0
        for p in paths:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        self._image_label.clear()
        self._image_label.setText(f"{len(paths)} images")
        self._image_label.setStyleSheet("background-color: #222; color: #ddd;")
        self._name_label.setText(f"{len(paths)} images selected")
        self._meta_label.setText(f"Total size: {_human_size(total)}")
        self._path_label.setText("")
        self._date_label.setVisible(False)
        self._keywords_label.setVisible(False)
        self._hierarchy_label.setVisible(False)

    def show_single(self, path: str) -> None:
        self._current_path = path
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                width, height = im.size
                im.thumbnail((PREVIEW_MAX_PX, PREVIEW_MAX_PX), Image.Resampling.LANCZOS)
                pm = QPixmap.fromImage(ImageQt(im.convert("RGBA")))
        except (UnidentifiedImageError, OSError) as e:
            self._image_label.clear()
            self._image_label.setText("Cannot preview")
            self._image_label.setStyleSheet("background-color: #222; color: #f99;")
            self._name_label.setText(os.path.basename(path))
            self._meta_label.setText(str(e))
            self._path_label.setText(path)
            self._date_label.setVisible(False)
            self._keywords_label.setVisible(False)
            self._hierarchy_label.setVisible(False)
            return

        self._image_label.setStyleSheet("background-color: #222;")
        self._image_label.setPixmap(pm)
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = 0
        self._name_label.setText(os.path.basename(path))
        self._meta_label.setText(f"{width} × {height} · {_human_size(size_bytes)}")
        self._path_label.setText(path)
        self._update_exif_labels(path)

    def update_selection(self, paths: list[str]) -> None:
        if not paths:
            self.show_empty()
        elif len(paths) == 1:
            self.show_single(paths[0])
        else:
            self.show_multi(paths)

    def _update_exif_labels(self, path: str) -> None:
        data: ExifData | None = None
        if self._exif_cache is not None:
            data = self._exif_cache.get(path)
        if data is None or data.date_taken is None:
            self._date_label.setVisible(False)
            self._date_label.setText("")
        else:
            self._date_label.setText(
                f"Taken: {data.date_taken.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self._date_label.setVisible(True)
        if data is None or not data.keywords:
            self._keywords_label.setVisible(False)
            self._keywords_label.setText("")
        else:
            self._keywords_label.setText(
                f"Keywords: {', '.join(data.keywords)}"
            )
            self._keywords_label.setVisible(True)
        if data is None or not data.hierarchical:
            self._hierarchy_label.setVisible(False)
            self._hierarchy_label.setText("")
        else:
            self._hierarchy_label.setText(
                f"Hierarchy: {', '.join(data.hierarchical)}"
            )
            self._hierarchy_label.setVisible(True)

    def _on_cache_updated(self, paths: set) -> None:
        if self._current_path is not None and self._current_path in paths:
            self._update_exif_labels(self._current_path)
