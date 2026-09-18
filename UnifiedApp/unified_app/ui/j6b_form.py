"""J6B_UnifiedMining 挖掘任务的参数表单。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import presets
from ..job_manager import JobManager
from ..job_spec import JOB_KIND_J6B, J6BJobSpec
from ..paths import AppSettings


class J6BForm(QWidget):
    def __init__(self, get_settings: Callable[[], AppSettings], job_manager: JobManager, parent=None) -> None:
        super().__init__(parent)
        self._get_settings = get_settings
        self.job_manager = job_manager

        layout = QVBoxLayout(self)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设："))
        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(False)
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

        template_row = QHBoxLayout()
        self.template_edit = QLineEdit(str(self._default_template_path()))
        template_row.addWidget(self.template_edit)
        template_btn = QPushButton("浏览…")
        template_btn.clicked.connect(self._browse_template)
        template_row.addWidget(template_btn)
        form.addRow("模板配置文件：", self._wrap(template_row))

        self.input_list = QListWidget()
        self.input_list.setMaximumHeight(110)
        input_btn_row = QHBoxLayout()
        add_folder_btn = QPushButton("添加文件夹…")
        add_folder_btn.clicked.connect(self._add_input_folder)
        remove_btn = QPushButton("移除所选")
        remove_btn.clicked.connect(self._remove_selected_input)
        input_btn_row.addWidget(add_folder_btn)
        input_btn_row.addWidget(remove_btn)
        input_col = QVBoxLayout()
        input_col.addWidget(self.input_list)
        input_col.addLayout(input_btn_row)
        form.addRow("输入路径（可多个）：", self._wrap(input_col))

        self.date_range_edit = QLineEdit()
        self.date_range_edit.setPlaceholderText("YYYYMMDD-YYYYMMDD，按日期子文件夹筛选，留空表示不过滤")
        form.addRow("日期范围：", self.date_range_edit)

        output_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        output_row.addWidget(self.output_dir_edit)
        output_btn = QPushButton("浏览…")
        output_btn.clicked.connect(self._browse_output_dir)
        output_row.addWidget(output_btn)
        form.addRow("输出目录：", self._wrap(output_row))

        self.report_name_edit = QLineEdit("Longitudinal_CPI_Report")
        form.addRow("报告名称：", self.report_name_edit)

        self.enabled_metrics_edit = QLineEdit("all")
        self.enabled_metrics_edit.setPlaceholderText("all 或逗号分隔的指标名列表")
        form.addRow("启用指标：", self.enabled_metrics_edit)

        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["all", "debugcan_only", "pcan_ccan_only"])
        form.addRow("信号范围：", self.scope_combo)

        layout.addWidget(form_group)

        compare_group = QGroupBox("挖掘完成后自动生成对比报告")
        compare_form = QFormLayout(compare_group)

        self.auto_compare_check = QCheckBox("挖掘成功后自动以本次报告为 target 生成对比报告")
        self.auto_compare_check.setChecked(True)
        compare_form.addRow(self.auto_compare_check)

        base_row = QHBoxLayout()
        self.compare_base_edit = QLineEdit()
        self.compare_base_edit.setPlaceholderText("可选：作为基线对比的历史报告路径，留空则只展示本次挖掘结果")
        base_row.addWidget(self.compare_base_edit)
        base_btn = QPushButton("浏览…")
        base_btn.clicked.connect(self._browse_compare_base)
        base_row.addWidget(base_btn)
        compare_form.addRow("基线报告（可选）：", self._wrap(base_row))

        self.compare_details_check = QCheckBox("生成对比报告时附带明细 Sheet")
        compare_form.addRow(self.compare_details_check)

        header_group = QGroupBox("对比报告表头覆盖（可选，留空则自动识别）")
        header_grid = QGridLayout(header_group)
        header_grid.addWidget(QLabel(""), 0, 0)
        header_grid.addWidget(QLabel("Base"), 0, 1)
        header_grid.addWidget(QLabel("Target"), 0, 2)
        self.compare_base_vehicle_edit = QLineEdit()
        self.compare_target_vehicle_edit = QLineEdit()
        self.compare_base_date_edit = QLineEdit()
        self.compare_target_date_edit = QLineEdit()
        self.compare_base_version_edit = QLineEdit()
        self.compare_target_version_edit = QLineEdit()
        header_grid.addWidget(QLabel("车型："), 1, 0)
        header_grid.addWidget(self.compare_base_vehicle_edit, 1, 1)
        header_grid.addWidget(self.compare_target_vehicle_edit, 1, 2)
        header_grid.addWidget(QLabel("日期："), 2, 0)
        header_grid.addWidget(self.compare_base_date_edit, 2, 1)
        header_grid.addWidget(self.compare_target_date_edit, 2, 2)
        header_grid.addWidget(QLabel("软件版本："), 3, 0)
        header_grid.addWidget(self.compare_base_version_edit, 3, 1)
        header_grid.addWidget(self.compare_target_version_edit, 3, 2)
        compare_form.addRow(header_group)

        layout.addWidget(compare_group)

        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.run_button = QPushButton("运行 J6B 挖掘")
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

    def _default_template_path(self) -> Path:
        return self._get_settings().j6b_dir() / "config.json"

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择模板 config.json", self.template_edit.text(), "JSON (*.json)")
        if path:
            self.template_edit.setText(path)

    def _add_input_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输入数据文件夹")
        if path:
            self.input_list.addItem(path)

    def _remove_selected_input(self) -> None:
        for item in self.input_list.selectedItems():
            self.input_list.takeItem(self.input_list.row(item))

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text())
        if path:
            self.output_dir_edit.setText(path)

    def _browse_compare_base(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择基线报告文件", self.compare_base_edit.text(), "Excel (*.xlsx)")
        if path:
            self.compare_base_edit.setText(path)

    def _collect_spec(self) -> J6BJobSpec:
        input_paths: List[str] = [self.input_list.item(i).text() for i in range(self.input_list.count())]
        return J6BJobSpec(
            task_name=self.task_name_edit.text(),
            template_config_path=self.template_edit.text().strip(),
            input_paths=input_paths,
            date_range=self.date_range_edit.text().strip(),
            output_dir=self.output_dir_edit.text().strip(),
            report_name=self.report_name_edit.text().strip(),
            enabled_metrics=self.enabled_metrics_edit.text().strip() or "all",
            metric_signal_scope=self.scope_combo.currentText(),
            auto_compare=self.auto_compare_check.isChecked(),
            compare_base_path=self.compare_base_edit.text().strip(),
            compare_details_sheet=self.compare_details_check.isChecked(),
            compare_base_vehicle=self.compare_base_vehicle_edit.text().strip(),
            compare_target_vehicle=self.compare_target_vehicle_edit.text().strip(),
            compare_base_date=self.compare_base_date_edit.text().strip(),
            compare_target_date=self.compare_target_date_edit.text().strip(),
            compare_base_version=self.compare_base_version_edit.text().strip(),
            compare_target_version=self.compare_target_version_edit.text().strip(),
        )

    def _apply_spec(self, spec: J6BJobSpec) -> None:
        self.task_name_edit.setText(spec.task_name)
        if spec.template_config_path:
            self.template_edit.setText(spec.template_config_path)
        self.input_list.clear()
        for path in spec.input_paths:
            self.input_list.addItem(path)
        self.date_range_edit.setText(spec.date_range)
        self.output_dir_edit.setText(spec.output_dir)
        self.report_name_edit.setText(spec.report_name)
        self.enabled_metrics_edit.setText(spec.enabled_metrics)
        index = self.scope_combo.findText(spec.metric_signal_scope)
        if index >= 0:
            self.scope_combo.setCurrentIndex(index)
        self.auto_compare_check.setChecked(spec.auto_compare)
        self.compare_base_edit.setText(spec.compare_base_path)
        self.compare_details_check.setChecked(spec.compare_details_sheet)
        self.compare_base_vehicle_edit.setText(spec.compare_base_vehicle)
        self.compare_target_vehicle_edit.setText(spec.compare_target_vehicle)
        self.compare_base_date_edit.setText(spec.compare_base_date)
        self.compare_target_date_edit.setText(spec.compare_target_date)
        self.compare_base_version_edit.setText(spec.compare_base_version)
        self.compare_target_version_edit.setText(spec.compare_target_version)

    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItems(presets.list_presets(JOB_KIND_J6B))
        index = self.preset_combo.findText(current)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称：", text=self.preset_combo.currentText())
        if not ok or not name.strip():
            return
        try:
            presets.save_preset(JOB_KIND_J6B, name, self._collect_spec().to_dict())
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_presets()

    def _load_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        try:
            data = presets.load_preset(JOB_KIND_J6B, name)
        except OSError as exc:
            QMessageBox.warning(self, "加载失败", str(exc))
            return
        self._apply_spec(J6BJobSpec.from_dict(data))

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        presets.delete_preset(JOB_KIND_J6B, name)
        self._refresh_presets()

    def _run(self) -> None:
        settings = self._get_settings()
        spec = self._collect_spec()
        if not spec.input_paths:
            QMessageBox.warning(self, "参数不完整", "请至少添加一个输入路径。")
            return
        template_path = Path(spec.template_config_path or str(self._default_template_path()))
        try:
            self.job_manager.submit_j6b(
                settings.python_for("j6b"),
                settings.j6b_dir(),
                template_path,
                spec,
                compare_python=settings.python_for("compare"),
                compare_dir=settings.compare_dir(),
            )
        except Exception as exc:  # noqa: BLE001 - surface any config/merge error to the user
            QMessageBox.critical(self, "启动失败", str(exc))
