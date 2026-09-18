"""主窗口：左侧任务配置 Tab（J6B / MMT / 对比报告），右侧/下方全局任务队列面板。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QFrame,
    QScrollArea,
    QSplitter,
    QTabWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

from ..job_manager import JobManager
from ..paths import AppSettings, load_settings, save_settings
from .compare_form import CompareForm
from .j6b_form import J6BForm
from .job_panel import JobPanel
from .mmt_form import MMTForm
from .settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("统一数据挖掘 & KPI 对比报告工具")
        self.resize(1180, 820)

        self._settings = load_settings()
        self.job_manager = JobManager(max_concurrent=self._settings.max_concurrent_jobs)

        self._build_menu()

        splitter = QSplitter(Qt.Orientation.Vertical)

        tabs = QTabWidget()
        tabs.addTab(self._scrollable(J6BForm(self._get_settings, self.job_manager)), "J6B 挖掘")
        tabs.addTab(self._scrollable(MMTForm(self._get_settings, self.job_manager)), "MMT 挖掘")
        tabs.addTab(self._scrollable(CompareForm(self._get_settings, self.job_manager)), "对比报告")
        splitter.addWidget(tabs)

        self.job_panel = JobPanel(self.job_manager)
        splitter.addWidget(self.job_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.setCentralWidget(splitter)

        if not self._settings.is_valid():
            self._prompt_settings(first_run=True)

    @staticmethod
    def _scrollable(widget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _get_settings(self) -> AppSettings:
        return self._settings

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("设置")
        settings_action = QAction("环境设置…", self)
        settings_action.triggered.connect(lambda: self._prompt_settings(first_run=False))
        menu.addAction(settings_action)

    def _prompt_settings(self, first_run: bool) -> None:
        if first_run:
            QMessageBox.information(
                self,
                "首次配置",
                "未能自动定位 J6B_UnifiedMining / MMT_UnifiedMining / CompareKPI_United，"
                "请先手动指定仓库根目录。",
            )
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec():
            self._settings = dialog.result_settings()
            save_settings(self._settings)
