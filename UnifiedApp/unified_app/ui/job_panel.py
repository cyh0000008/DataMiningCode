"""任务队列面板：展示所有任务（排队/运行/完成），支持查看日志、取消任务。"""

from __future__ import annotations

import time
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..job_manager import STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCESS, STATUS_CANCELED, JobManager
from .log_dialog import LogDialog


STATUS_COLORS = {
    STATUS_RUNNING: "#1a73e8",
    STATUS_SUCCESS: "#188038",
    STATUS_FAILED: "#d93025",
    STATUS_CANCELED: "#e37400",
}

COL_NAME, COL_KIND, COL_STATUS, COL_PROGRESS, COL_ELAPSED, COL_ACTIONS = range(6)


class JobPanel(QWidget):
    def __init__(self, job_manager: JobManager, parent=None) -> None:
        super().__init__(parent)
        self.job_manager = job_manager
        self._row_by_job: Dict[str, int] = {}
        self._log_dialogs: Dict[str, LogDialog] = {}
        self._progress_bars: Dict[str, QProgressBar] = {}

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        header.addWidget(QLabel("任务队列（全局最多同时运行）"))
        header.addStretch(1)
        header.addWidget(QLabel("最大并发数："))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 3)
        self.concurrency_spin.setValue(job_manager.max_concurrent)
        self.concurrency_spin.valueChanged.connect(self._on_concurrency_changed)
        header.addWidget(self.concurrency_spin)
        self.running_label = QLabel("运行中：0")
        header.addWidget(self.running_label)
        layout.addLayout(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["任务名", "类型", "状态", "进度", "用时", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        job_manager.job_added.connect(self._on_job_added)
        job_manager.job_updated.connect(self._on_job_updated)
        job_manager.job_log.connect(self._on_job_log)
        job_manager.job_finished.connect(self._on_job_updated)

    def _on_concurrency_changed(self, value: int) -> None:
        self.job_manager.set_max_concurrent(value)

    def _on_job_added(self, job_id: str) -> None:
        job = self.job_manager.get_job(job_id)
        if job is None:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_by_job[job_id] = row

        self.table.setItem(row, COL_NAME, QTableWidgetItem(job.title))
        self.table.setItem(row, COL_KIND, QTableWidgetItem(job.kind_label))
        self.table.setItem(row, COL_STATUS, QTableWidgetItem(job.status))

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(job.percent)
        self._progress_bars[job_id] = progress
        self.table.setCellWidget(row, COL_PROGRESS, progress)

        self.table.setItem(row, COL_ELAPSED, QTableWidgetItem("-"))

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(2, 0, 2, 0)
        log_button = QPushButton("查看日志")
        log_button.clicked.connect(lambda _checked=False, jid=job_id: self._show_log(jid))
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(lambda _checked=False, jid=job_id: self.job_manager.cancel(jid))
        actions_layout.addWidget(log_button)
        actions_layout.addWidget(cancel_button)
        self.table.setCellWidget(row, COL_ACTIONS, actions)

        self._refresh_running_label()

    def _on_job_updated(self, job_id: str) -> None:
        job = self.job_manager.get_job(job_id)
        row = self._row_by_job.get(job_id)
        if job is None or row is None:
            return

        status_item = QTableWidgetItem(job.status)
        color = STATUS_COLORS.get(job.status)
        if color:
            status_item.setForeground(Qt.GlobalColor.black)
            status_item.setBackground(_hex_to_color(color, light=True))
        self.table.setItem(row, COL_STATUS, status_item)

        progress = self._progress_bars.get(job_id)
        if progress is not None:
            progress.setValue(job.percent)
            progress.setFormat(f"{job.percent}%" + (f"  ({job.progress_text})" if job.progress_text else ""))

        if job.started_at is not None:
            end_time = job.finished_at if job.finished_at is not None else time.time()
            elapsed = int(end_time - job.started_at)
            self.table.setItem(row, COL_ELAPSED, QTableWidgetItem(_format_elapsed(elapsed)))

        self._refresh_running_label()

    def _on_job_log(self, job_id: str, line: str) -> None:
        dialog = self._log_dialogs.get(job_id)
        if dialog is not None:
            dialog.append_line(line)

    def _show_log(self, job_id: str) -> None:
        job = self.job_manager.get_job(job_id)
        if job is None:
            return
        dialog = self._log_dialogs.get(job_id)
        if dialog is None:
            dialog = LogDialog(job.title, self)
            dialog.append_line("\n".join(job.log_lines))
            self._log_dialogs[job_id] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _refresh_running_label(self) -> None:
        self.running_label.setText(f"运行中：{self.job_manager.running_count()} / {self.job_manager.max_concurrent}")


def _format_elapsed(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _hex_to_color(value: str, light: bool = False):
    from PySide6.QtGui import QColor

    color = QColor(value)
    if light:
        color.setAlpha(60)
    return color
