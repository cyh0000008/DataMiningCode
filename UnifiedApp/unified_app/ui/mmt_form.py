"""MMT_UnifiedMining 挖掘任务的参数表单。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import presets
from ..job_manager import JobManager
from ..job_spec import JOB_KIND_MMT, MMTJobSpec
from ..paths import AppSettings


class MMTForm(QWidget):
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

        template_row = QHBoxLayout()
        self.template_edit = QLineEdit(str(self._default_template_path()))
        template_row.addWidget(self.template_edit)
        template_btn = QPushButton("浏览…")
        template_btn.clicked.connect(self._browse_template)
        template_row.addWidget(template_btn)
        form.addRow("模板配置文件：", self._wrap(template_row))

        source_row = QHBoxLayout()
        self.source_path_edit = QLineEdit()
        self.source_path_edit.setPlaceholderText("例如 /mnt/nas/L2pp_MBOX/PL061026，可直接编辑远程路径")
        source_row.addWidget(self.source_path_edit)
        source_btn = QPushButton("浏览…")
        source_btn.clicked.connect(self._browse_source)
        source_row.addWidget(source_btn)
        form.addRow("数据源路径：", self._wrap(source_row))

        self.query_date_edit = QLineEdit()
        self.query_date_edit.setPlaceholderText("YYYYMMDD-YYYYMMDD，留空表示不过滤")
        form.addRow("查询日期：", self.query_date_edit)

        self.query_software_edit = QLineEdit()
        form.addRow("软件版本过滤：", self.query_software_edit)

        self.max_depth_spin = QSpinBox()
        self.max_depth_spin.setRange(0, 20)
        self.max_depth_spin.setValue(4)
        form.addRow("最大扫描深度：", self.max_depth_spin)

        self.suffix_edit = QLineEdit("_done")
        form.addRow("目录后缀：", self.suffix_edit)

        output_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        output_row.addWidget(self.output_dir_edit)
        output_btn = QPushButton("浏览…")
        output_btn.clicked.connect(self._browse_output_dir)
        output_row.addWidget(output_btn)
        form.addRow("输出目录：", self._wrap(output_row))

        self.report_prefix_edit = QLineEdit("Unified_Longitudinal_Report")
        form.addRow("报告前缀：", self.report_prefix_edit)

        self.file_pattern_edit = QLineEdit("*.h5")
        form.addRow("文件匹配模式：", self.file_pattern_edit)

        pipeline_row = QHBoxLayout()
        self.can_checkbox = QCheckBox("CAN 指标（MPI）")
        self.can_checkbox.setChecked(True)
        self.topic_checkbox = QCheckBox("Topic 指标（CPI）")
        self.topic_checkbox.setChecked(True)
        pipeline_row.addWidget(self.can_checkbox)
        pipeline_row.addWidget(self.topic_checkbox)
        form.addRow("启用 Pipeline：", self._wrap(pipeline_row))

        self.resume_combo = QComboBox()
        self.resume_combo.addItems(["auto", "never"])
        self.resume_combo.setToolTip(
            "auto：当配置与代码未变化时自动续跑上次未完成的任务；never：总是从头开始运行"
        )
        form.addRow("续跑模式：", self.resume_combo)

        self.dry_run_checkbox = QCheckBox("仅校验配置，不扫描数据（dry-run）")
        self.dry_run_checkbox.setToolTip("dry-run 不会生成报告文件，即使勾选了自动对比也会被跳过")
        form.addRow("", self.dry_run_checkbox)

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

        archive_row = QHBoxLayout()
        self.raw_report_archive_dir_edit = QLineEdit()
        self.raw_report_archive_dir_edit.setPlaceholderText("可选；按 Target 的车型-日期-版本.xlsx 另存原始报告")
        archive_row.addWidget(self.raw_report_archive_dir_edit)
        archive_btn = QPushButton("浏览…")
        archive_btn.clicked.connect(self._browse_raw_report_archive_dir)
        archive_row.addWidget(archive_btn)
        compare_form.addRow("原始报告额外保存目录：", self._wrap(archive_row))

        layout.addWidget(compare_group)

        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.run_button = QPushButton("运行 MMT 挖掘")
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
        return self._get_settings().mmt_dir() / "config.json"

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择模板 config.json", self.template_edit.text(), "JSON (*.json)")
        if path:
            self.template_edit.setText(path)

    def _browse_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择数据源目录", self.source_path_edit.text())
        if path:
            self.source_path_edit.setText(path)

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text())
        if path:
            self.output_dir_edit.setText(path)

    def _browse_compare_base(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择基线报告文件", self.compare_base_edit.text(), "Excel (*.xlsx)")
        if path:
            self.compare_base_edit.setText(path)

    def _browse_raw_report_archive_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择原始报告额外保存目录", self.raw_report_archive_dir_edit.text()
        )
        if path:
            self.raw_report_archive_dir_edit.setText(path)

    def _collect_spec(self) -> MMTJobSpec:
        return MMTJobSpec(
            task_name=self.task_name_edit.text(),
            template_config_path=self.template_edit.text().strip(),
            source_path=self.source_path_edit.text().strip(),
            query_date=self.query_date_edit.text().strip(),
            query_software=self.query_software_edit.text().strip(),
            max_depth=self.max_depth_spin.value(),
            suffix=self.suffix_edit.text().strip() or "_done",
            output_dir=self.output_dir_edit.text().strip(),
            report_prefix=self.report_prefix_edit.text().strip(),
            file_pattern=self.file_pattern_edit.text().strip() or "*.h5",
            enable_can_pipeline=self.can_checkbox.isChecked(),
            enable_topic_pipeline=self.topic_checkbox.isChecked(),
            resume_mode=self.resume_combo.currentText(),
            dry_run=self.dry_run_checkbox.isChecked(),
            auto_compare=self.auto_compare_check.isChecked(),
            compare_base_path=self.compare_base_edit.text().strip(),
            compare_details_sheet=self.compare_details_check.isChecked(),
            compare_base_vehicle=self.compare_base_vehicle_edit.text().strip(),
            compare_target_vehicle=self.compare_target_vehicle_edit.text().strip(),
            compare_base_date=self.compare_base_date_edit.text().strip(),
            compare_target_date=self.compare_target_date_edit.text().strip(),
            compare_base_version=self.compare_base_version_edit.text().strip(),
            compare_target_version=self.compare_target_version_edit.text().strip(),
            raw_report_archive_dir=self.raw_report_archive_dir_edit.text().strip(),
        )

    def _apply_spec(self, spec: MMTJobSpec) -> None:
        self.task_name_edit.setText(spec.task_name)
        if spec.template_config_path:
            self.template_edit.setText(spec.template_config_path)
        self.source_path_edit.setText(spec.source_path)
        self.query_date_edit.setText(spec.query_date)
        self.query_software_edit.setText(spec.query_software)
        self.max_depth_spin.setValue(spec.max_depth)
        self.suffix_edit.setText(spec.suffix)
        self.output_dir_edit.setText(spec.output_dir)
        self.report_prefix_edit.setText(spec.report_prefix)
        self.file_pattern_edit.setText(spec.file_pattern)
        self.can_checkbox.setChecked(spec.enable_can_pipeline)
        self.topic_checkbox.setChecked(spec.enable_topic_pipeline)
        index = self.resume_combo.findText(spec.resume_mode)
        if index >= 0:
            self.resume_combo.setCurrentIndex(index)
        self.dry_run_checkbox.setChecked(spec.dry_run)
        self.auto_compare_check.setChecked(spec.auto_compare)
        self.compare_base_edit.setText(spec.compare_base_path)
        self.compare_details_check.setChecked(spec.compare_details_sheet)
        self.compare_base_vehicle_edit.setText(spec.compare_base_vehicle)
        self.compare_target_vehicle_edit.setText(spec.compare_target_vehicle)
        self.compare_base_date_edit.setText(spec.compare_base_date)
        self.compare_target_date_edit.setText(spec.compare_target_date)
        self.compare_base_version_edit.setText(spec.compare_base_version)
        self.compare_target_version_edit.setText(spec.compare_target_version)
        self.raw_report_archive_dir_edit.setText(spec.raw_report_archive_dir)

    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItems(presets.list_presets(JOB_KIND_MMT))
        index = self.preset_combo.findText(current)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称：", text=self.preset_combo.currentText())
        if not ok or not name.strip():
            return
        try:
            presets.save_preset(JOB_KIND_MMT, name, self._collect_spec().to_dict())
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh_presets()

    def _load_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        try:
            data = presets.load_preset(JOB_KIND_MMT, name)
        except OSError as exc:
            QMessageBox.warning(self, "加载失败", str(exc))
            return
        self._apply_spec(MMTJobSpec.from_dict(data))

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        presets.delete_preset(JOB_KIND_MMT, name)
        self._refresh_presets()

    def _run(self) -> None:
        settings = self._get_settings()
        spec = self._collect_spec()
        if not spec.source_path:
            QMessageBox.warning(self, "参数不完整", "请填写数据源路径。")
            return
        if not spec.enable_can_pipeline and not spec.enable_topic_pipeline:
            QMessageBox.warning(self, "参数不完整", "请至少启用一个 Pipeline（CAN 或 Topic）。")
            return
        if spec.raw_report_archive_dir and not all(
            (spec.compare_target_vehicle, spec.compare_target_date, spec.compare_target_version)
        ):
            QMessageBox.warning(
                self,
                "参数不完整",
                "启用原始报告额外保存时，请填写 Target 的车型、日期和软件版本。",
            )
            return
        template_path = Path(spec.template_config_path or str(self._default_template_path()))
        try:
            self.job_manager.submit_mmt(
                settings.python_for("mmt"),
                settings.mmt_dir(),
                template_path,
                spec,
                compare_python=settings.python_for("compare"),
                compare_dir=settings.compare_dir(),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "启动失败", str(exc))
