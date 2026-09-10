"""Dark theme.

Not decoration. Crate sits next to a DAW, and a bright window in a dark
studio session is the reason a tool gets closed. The palette is low-contrast
grey with one accent, so the waveform is the brightest thing on screen.
"""

STYLESHEET = """
QWidget {
    background: #1b1c20;
    color: #d6d8dc;
    font-size: 13px;
}
QMainWindow, QStatusBar { background: #17181b; }

QLineEdit, QComboBox {
    background: #232429;
    border: 1px solid #303238;
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: #2f7d68;
}
QLineEdit:focus, QComboBox:focus { border-color: #5ac8a8; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #232429;
    border: 1px solid #303238;
    selection-background-color: #2f7d68;
}

QPushButton {
    background: #262830;
    border: 1px solid #34363e;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover { background: #2e313a; }
QPushButton:pressed { background: #22242b; }

QTableView {
    background: #1e1f24;
    alternate-background-color: #202127;
    border: 1px solid #2a2c31;
    border-radius: 4px;
    gridline-color: transparent;
    selection-background-color: #2f7d68;
    selection-color: #ffffff;
}
QTableView::item { padding: 2px 6px; }
QHeaderView::section {
    background: #232429;
    color: #9aa0a8;
    border: none;
    border-right: 1px solid #2a2c31;
    border-bottom: 1px solid #2a2c31;
    padding: 5px 6px;
}

QCheckBox { spacing: 6px; }
QLabel#detail { color: #8b9098; }

QProgressBar {
    background: #232429;
    border: 1px solid #303238;
    border-radius: 4px;
    height: 14px;
    text-align: center;
}
QProgressBar::chunk { background: #2f7d68; }

QScrollBar:vertical { background: #1b1c20; width: 11px; margin: 0; }
QScrollBar::handle:vertical {
    background: #3a3d45;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #4a4e58; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenu {
    background: #232429;
    border: 1px solid #303238;
}
QMenu::item:selected { background: #2f7d68; }
"""
