from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .warning_log import WarningLog


class WarningDialog(QDialog):
    """Modal listing every warning since app start. Clear empties the log
    and closes; Close just closes."""

    def __init__(self, log: WarningLog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Warnings")
        self.resize(600, 400)
        self._log = log

        self._list = QListWidget()
        self._populate()

        clear_btn = QPushButton("Clear")
        close_btn = QPushButton("Close")
        clear_btn.clicked.connect(self._on_clear)
        close_btn.clicked.connect(self.accept)

        btns = QHBoxLayout()
        btns.addWidget(clear_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addLayout(btns)

    def _populate(self) -> None:
        self._list.clear()
        for entry in reversed(self._log.entries()):
            src = f"[{entry.source}] " if entry.source else ""
            self._list.addItem(
                f"{entry.when.strftime('%H:%M:%S')} {src}{entry.message}"
            )

    def _on_clear(self) -> None:
        self._log.clear()
        self.accept()
