from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QLineEdit


_FLASH_MS = 600
_FLASH_STYLE = "QLineEdit { background-color: #fdd; }"


class PathBar(QLineEdit):
    path_entered = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Enter a folder path and press Enter")
        self._last_valid: str = ""
        self._base_style: str = self.styleSheet()
        self.returnPressed.connect(self._on_return)

    def set_current_path(self, path: str) -> None:
        """Update displayed path without emitting (called by tree-driven nav)."""
        self._last_valid = path
        self.setText(path)

    def _on_return(self) -> None:
        text = self.text().strip()
        if text and Path(text).is_dir():
            self._last_valid = text
            self.path_entered.emit(text)
        else:
            self._flash_red()

    def _flash_red(self) -> None:
        # Show the rejected text in red briefly, then restore the last valid path.
        self.setStyleSheet(_FLASH_STYLE)
        QTimer.singleShot(_FLASH_MS, self._restore_after_flash)

    def _restore_after_flash(self) -> None:
        self.setStyleSheet(self._base_style)
        self.setText(self._last_valid)
