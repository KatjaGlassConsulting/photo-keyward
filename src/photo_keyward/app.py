from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .constants import APP_NAME, APP_ORG
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
