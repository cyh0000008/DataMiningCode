#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UnifiedApp 入口：一体化数据挖掘 & KPI 对比报告桌面工具。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from unified_app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("UnifiedMiningApp")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
