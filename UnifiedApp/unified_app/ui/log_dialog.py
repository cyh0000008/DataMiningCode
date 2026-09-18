"""单个任务的实时日志弹窗。"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout


class LogDialog(QDialog):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"运行日志 - {title}")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))
        self.text.setMaximumBlockCount(20000)
        layout.addWidget(self.text)

    def append_line(self, line: str) -> None:
        if not line:
            return
        scrollbar = self.text.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self.text.appendPlainText(line)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())
