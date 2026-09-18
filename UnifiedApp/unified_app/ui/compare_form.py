"""CompareKPI_United 对比报告任务的参数表单。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import presets
from ..job_manager import JobManager
from ..job_spec import JOB_KIND_COMPARE, CompareJobSpec
from ..paths import AppSettings


class CompareForm(QWidget):
    def __init__(self, get_settings: Callable[[], AppSettings], job_manager: JobManager, parent=None) -> None:
        super().__init__(parent)
        self._get_settings = get_settings
        self.job_manager = job_manager

        layout = QVBoxLayout(self)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设："))
        self.preset_combo = QComboBox()
        preset_row.addWidget(self.preset_combo, 1)
        load_btn = QPushButton("加载")
        load_btn.clicked.connect(self._load_preset)
        save_btn = QPushButton("保存为预设")
        save_btn.clicked.connect(self._save_preset)
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self._delete_preset)
        preset_row.addWidget(load_btn)
        preset_row.addWidget(save_btn)
        preset_row.addWidget(delete_btn)
        layout.addLayout(preset_row)

        form_group = QGroupBox("运行参数")
        form = QFormLayout(form_group)

        self.task_name_edit = QLineEdit()
        form.addRow("任务名称：", self.task_name_edit)

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["J6B", "MMT"])
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        form.addRow("报告配置：", self.profile_combo)

        base_row = QHBoxLayout()
        self.base_path_edit = QLineEdit()
        self.base_path_edit.setPlaceholderText("留空则只展示 target 结果")
        base_row.addWidget(self.base_path_edit)
        base_btn = QPushButton("浏览…")
        base_btn.clicked.connect(lambda: self._browse_path(self.base_path_edit))
        base_row.addWidget(base_btn)
        form.addRow("Base 报告：", self._wrap(base_row))

        target_row = QHBoxLayout()
        self.target_path_edit = QLineEdit()
        target_row.addWidget(self.target_path_edit)
        target_btn = QPushButton("浏览…")
        target_btn.clicked.connect(lambda: self._browse_path(self.target_path_edit))
        target_row.addWidget(target_btn)
        form.addRow("Target 报告：", self._wrap(target_row))

        template_row = QHBoxLayout()
        self.template_path_edit = QLineEdit()
        self.template_path_edit.setPlaceholderText("留空使用 templates/template_<配置>.xlsx")
        template_row.addWidget(self.template_path_edit)
        template_btn = QPushButton("浏览…")
        template_btn.clicked.connect(lambda: self._browse_path(self.template_path_edit, is_dir=False))
        template_row.addWidget(template_btn)
        form.addRow("模板文件：", self._wrap(template_row))

        output_row = QHBoxLayout()
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setPlaceholderText("留空自动命名，写入 reports/ 目录")
        output_row.addWidget(self.output_path_edit)
        output_btn = QPushButton("浏览…")
        output_btn.clicked.connect(self._browse_output)
        output_row.addWidget(output_btn)
        form.addRow("输出文件：", self._wrap(output_row))

        self.details_checkbox = QCheckBox("保留“信号对照表”明细 sheet")
        form.addRow("", self.details_checkbox)

        layout.addWidget(form_group)

        self.advanced_group = QGroupBox("高级：表头覆盖（可选）")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_form = QFormLayout(self.advanced_group)
        self.base_vehicle_edit = QLineEdit()
        self.target_vehicle_edit = QLineEdit()
        self.base_date_edit = QLineEdit()
        self.target_date_edit = QLineEdit()
        self.base_version_edit = QLineEdit()
        self.target_version_edit = QLineEdit()
        self.base_source_date_edit = QLineEdit()
        self.target_source_date_edit = QLineEdit()
        advanced_form.addRow("Base 车型：", self.base_vehicle_edit)
        advanced_form.addRow("Target 车型：", self.target_vehicle_edit)
        advanced_form.addRow("Base 日期：", self.base_date_edit)
        advanced_form.addRow("Target 日期：", self.target_date_edit)
        advanced_form.addRow("Base 软件版本：", self.base_version_edit)
        advanced_form.addRow("Target 软件版本：", self.target_version_edit)
        advanced_form.addRow("Base 来源日期组：", self.base_source_date_edit)
        advanced_form.addRow("Target 来源日期组：", self.target_source_date_edit)
        layout.addWidget(self.advanced_group)

        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.run_button = QPushButton("运行对比报告生成")
        self.run_button.setMinimumHeight(36)
        self.run_button.clicked.connect(self._run)
        run_row.addWidget(self.run_button)
        layout.addLayout(run_row)
        layout.addStretch(1)

        self._refresh_presets()

    # ------------------------------------------------------------------
    def _wrap(self, inner_layout) -> QWidget:
        widget = QWidget()
        widget.setLayout(inner_layout)
        return widget

    def _on_profile_changed(self, profile: str) -> None:
        compare_dir = self._get_settings().compare_dir()
        if not self.base_path_edit.text().strip():
            self.base_path_edit.setPlaceholderText(f"留空则默认读取 base/{profile}/")
        if not self.target_path_edit.text().strip():
            self.target_path_edit.setText(str(compare_dir / "target" / profile))

    def _browse_path(self, line_edit: QLineEdit, is_dir: bool = True) -> None:
        if is_dir:
            path = QFileDialog.getExistingDirectory(self, "选择目录", line_edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", line_edit.text(), "Excel (*.xlsx)")
        if path:
            line_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", self.output_path_edit.text(), "Excel (*.xlsx)")
        if path:
            self.output_path_edit.setText(path)

    def _collect_spec(self) -> CompareJobSpec:
        return CompareJobSpec(
            task_name=self.task_name_edit.text(),
            report_profile=self.profile_combo.currentText(),
            base_path=self.base_path_edit.text().strip(),
            target_path=self.target_path_edit.text().strip(),
            template_path=self.template_path_edit.text().strip(),
            output_path=self.output_path_edit.text().strip(),
            details_sheet=self.details_checkbox.isChecked(),
            base_vehicle=self.base_vehicle_edit.text().strip(),
            target_vehicle=self.target_vehicle_edit.text().strip(),
            base_date=self.base_date_edit.text().strip(),
            target_date=self.target_date_edit.text().strip(),
            base_version=self.base_version_edit.text().strip(),
            target_version=self.target_version_edit.text().strip(),
            base_source_date=self.base_source_date_edit.text().strip(),
            target_source_date=self.target_source_date_edit.text().strip(),
        )

    def _apply_spec(self, spec: CompareJobSpec) -> None:
        self.task_name_edit.setText(spec.task_name)
        index = self.profile_combo.findText(spec.report_profile)
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        self.base_path_edit.setText(spec.base_path)
        self.target_path_edit.setText(spec.target_path)
        self.template_path_edit.setText(spec.template_path)
        self.output_path_edit.setText(spec.output_path)
        self.details_checkbox.setChecked(spec.details_sheet)
        self.base_vehicle_edit.setText(spec.base_vehicle)
        self.target_vehicle_edit.setText(spec.target_vehicle)
        self.base_date_edit.setText(spec.base_date)
        self.target_date_edit.setText(spec.target_date)
        self.base_version_edit.setText(spec.base_version)
        self.target_version_edit.setText(spec.target_version)
        self.base_source_date_edit.setText(spec.base_source_date)
        self.target_source_date_edit.setText(spec.target_source_date)

    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItems(presets.list_presets(JOB_KIND_COMPARE))
        index = self.preset_combo.findText(current)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称：", text=self.preset_combo.currentText())
        if not ok or not name.strip():
            return
        try:
            presets.save_preset(JOB_KIND_COMPARE, name, self._collect_spec().to_dict())
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_presets()

    def _load_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        try:
            data = presets.load_preset(JOB_KIND_COMPARE, name)
        except OSError as exc:
            QMessageBox.warning(self, "加载失败", str(exc))
            return
        self._apply_spec(CompareJobSpec.from_dict(data))

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        presets.delete_preset(JOB_KIND_COMPARE, name)
        self._refresh_presets()

    def _run(self) -> None:
        settings = self._get_settings()
        spec = self._collect_spec()
        if not spec.target_path:
            QMessageBox.warning(self, "参数不完整", "请填写 Target 报告路径。")
            return
        try:
            self.job_manager.submit_compare(
                settings.python_for("compare"),
                settings.compare_dir(),
                spec,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "启动失败", str(exc))
