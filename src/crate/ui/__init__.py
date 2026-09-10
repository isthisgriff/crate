"""PySide6 desktop UI.

Nothing outside this package imports Qt, and nothing inside it contains
business logic. The window drives `crate.library`, `crate.audio` and
`crate.persistence`; none of them know it exists.
"""

from __future__ import annotations

import sys

from crate.app import CrateApp


def run(app: CrateApp) -> int:
    """Open the browser window and run the Qt event loop until it closes.

    Imported lazily by the CLI so that `crate scan` on a headless machine does
    not need PySide6 to be installed at all.
    """
    from PySide6.QtWidgets import QApplication

    from crate.ui.main_window import MainWindow
    from crate.ui.theme import STYLESHEET

    existing = QApplication.instance()
    qt_app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    qt_app.setApplicationName("Crate")
    qt_app.setStyleSheet(STYLESHEET)

    window = MainWindow(app)
    window.show()
    return int(qt_app.exec())


__all__ = ["run"]
