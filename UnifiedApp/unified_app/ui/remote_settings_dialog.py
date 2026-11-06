"""Local configuration for paths used by Feishu remote-editing commands."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..feishu_bot import config_path, load_bot_config, save_bot_config


ACCESS_TYPES = ("input", "output", "report")


class RemoteSettingsDialog(QDialog):
    def __init__(self, parent=None, *, config_file: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("飞书远程路径设置")
        self.resize(760, 420)
        self._config_file = Path(config_file) if config_file is not None else config_path()

        config = load_bot_config(self._config_file)
        remote = config.get("remote")
        remote = remote if isinstance(remote, dict) else {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("此处仅配置本机路径别名；保存不会重启飞书助手。"))

        form = QFormLayout()
        self.machine_name_edit = QLineEdit(str(remote.get("machine_name", "")))
        form.addRow("本机名称：", self.machine_name_edit)
        layout.addLayout(form)

        self.alias_table = QTableWidget(0, 4)
        self.alias_table.setHorizontalHeaderLabels(["别名", "绝对路径", "访问类型", ""])
        self.alias_table.horizontalHeader().setStretchLastSection(False)
        self.alias_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.alias_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.alias_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.alias_table)

        alias_buttons = QHBoxLayout()
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add_alias)
        remove_button = QPushButton("删除选中")
        remove_button.clicked.connect(self._remove_aliases)
        alias_buttons.addWidget(add_button)
        alias_buttons.addWidget(remove_button)
        alias_buttons.addStretch(1)
        layout.addLayout(alias_buttons)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        aliases = remote.get("path_aliases", {})
        if isinstance(aliases, dict):
            for name, value in aliases.items():
                if isinstance(value, dict):
                    self._add_alias(str(name), str(value.get("path", "")),
                                    str(value.get("access", "input")))

    def _add_alias(self, name: str = "", path: str = "", access: str = "input") -> None:
        row = self.alias_table.rowCount()
        self.alias_table.insertRow(row)
        self.alias_table.setItem(row, 0, QTableWidgetItem(name))
        self.alias_table.setItem(row, 1, QTableWidgetItem(path))
        access_combo = QComboBox()
        access_combo.addItems(ACCESS_TYPES)
        index = access_combo.findText(access)
        access_combo.setCurrentIndex(index if index >= 0 else 0)
        self.alias_table.setCellWidget(row, 2, access_combo)
        browse_button = QPushButton("浏览…")
        browse_button.clicked.connect(
            lambda _checked=False, button=browse_button: self._browse_alias_button(button)
        )
        self.alias_table.setCellWidget(row, 3, browse_button)

    def _browse_alias_button(self, button: QPushButton) -> None:
        row = next(
            (index for index in range(self.alias_table.rowCount())
             if self.alias_table.cellWidget(index, 3) is button),
            -1,
        )
        if row >= 0:
            self._browse_alias(row)

    def _remove_aliases(self) -> None:
        rows = sorted({index.row() for index in self.alias_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.alias_table.removeRow(row)

    def _browse_alias(self, row: int) -> None:
        access = self.alias_table.cellWidget(row, 2).currentText()
        path_item = self.alias_table.item(row, 1)
        current_path = path_item.text() if path_item is not None else ""
        if access in ("input", "output"):
            selected = QFileDialog.getExistingDirectory(self, "选择目录", current_path)
        else:
            choice = QMessageBox(self)
            choice.setWindowTitle("选择报告路径")
            choice.setText("选择一个 XLSX 文件或目录。")
            file_button = choice.addButton("选择 XLSX 文件", QMessageBox.ButtonRole.ActionRole)
            directory_button = choice.addButton("选择目录", QMessageBox.ButtonRole.ActionRole)
            choice.addButton(QMessageBox.StandardButton.Cancel)
            choice.exec()
            clicked = choice.clickedButton()
            if clicked is file_button:
                selected, _ = QFileDialog.getOpenFileName(
                    self, "选择 XLSX 文件", current_path, "Excel 工作簿 (*.xlsx)"
                )
            elif clicked is directory_button:
                selected = QFileDialog.getExistingDirectory(self, "选择报告目录", current_path)
            else:
                selected = ""
        if selected:
            self.alias_table.setItem(row, 1, QTableWidgetItem(selected))

    def validation_error(self) -> str:
        names: set[str] = set()
        for row in range(self.alias_table.rowCount()):
            name_item = self.alias_table.item(row, 0)
            path_item = self.alias_table.item(row, 1)
            name = name_item.text().strip() if name_item is not None else ""
            path_text = path_item.text().strip() if path_item is not None else ""
            access_widget = self.alias_table.cellWidget(row, 2)
            access = access_widget.currentText() if isinstance(access_widget, QComboBox) else ""

            if not name:
                return f"第 {row + 1} 行的别名不能为空。"
            normalized_name = name.casefold()
            if normalized_name in names:
                return f"别名“{name}”重复。"
            names.add(normalized_name)
            if not path_text:
                return f"别名“{name}”的路径不能为空。"
            path = Path(path_text)
            if not path.is_absolute():
                return f"别名“{name}”必须使用绝对路径。"
            if access not in ACCESS_TYPES:
                return f"别名“{name}”的访问类型无效。"
            if access in ("input", "output") and not path.is_dir():
                return f"别名“{name}”的 {access} 路径必须是已存在的目录。"
            if access == "report" and not (
                path.is_dir() or (path.is_file() and path.suffix.lower() == ".xlsx")
            ):
                return f"别名“{name}”必须指向已存在的 XLSX 文件或目录。"
        return ""

    def _remote_config(self) -> dict:
        aliases = {}
        for row in range(self.alias_table.rowCount()):
            name = self.alias_table.item(row, 0).text().strip()
            path = Path(self.alias_table.item(row, 1).text().strip())
            access = self.alias_table.cellWidget(row, 2).currentText()
            aliases[name] = {"path": str(path), "access": access}
        return {
            "machine_name": self.machine_name_edit.text().strip(),
            "path_aliases": aliases,
        }

    def _on_accept(self) -> None:
        error = self.validation_error()
        if error:
            QMessageBox.warning(self, "路径设置无效", error)
            return
        try:
            config = load_bot_config(self._config_file)
            config["remote"] = self._remote_config()
            save_bot_config(self._config_file, config)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()
