from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStyle, QToolButton, QWidget


class WarningIndicator(QToolButton):
    """Bottom-right status-bar widget: warning triangle + count. Hidden
    when the count is zero."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxWarning))
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setToolTip("Click to view warnings")
        self.setText("0")
        self.hide()

    def set_count(self, count: int) -> None:
        if count <= 0:
            self.hide()
            return
        self.setText(str(count))
        self.show()
