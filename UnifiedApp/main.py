#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UnifiedApp 入口：一体化数据挖掘 & KPI 对比报告桌面工具。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from unified_app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("UnifiedMiningApp")
    app.setDesktopFileName("unified-mining")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "unified-mining.svg")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
