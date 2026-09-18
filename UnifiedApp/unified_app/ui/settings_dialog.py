"""设置对话框：配置三个既有工程所在的仓库根目录，以及各自使用的 Python 解释器。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..paths import (
    COMPARE_DIR_NAME,
    J6B_DIR_NAME,
    MMT_DIR_NAME,
    AppSettings,
    default_python_executable,
)


def _browse_row(line_edit: QLineEdit, is_dir: bool, parent: QWidget, title: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(line_edit)
    button = QPushButton("浏览…")

    def _browse() -> None:
        if is_dir:
            path = QFileDialog.getExistingDirectory(parent, title, line_edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(parent, title, line_edit.text())
        if path:
            line_edit.setText(path)

    button.clicked.connect(_browse)
    row.addWidget(button)
    return row


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("环境设置")
        self.resize(680, 320)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"仓库根目录需要同时包含 {J6B_DIR_NAME} / {MMT_DIR_NAME} / {COMPARE_DIR_NAME} 三个文件夹。"
            )
        )

        form = QFormLayout()

        self.repo_root_edit = QLineEdit(settings.repo_root or str(settings.resolved_repo_root()))
        form.addRow("仓库根目录：", self._wrap(self._browse_row(self.repo_root_edit, True, "选择仓库根目录")))

        default_py = default_python_executable()
        self.j6b_python_edit = QLineEdit(settings.j6b_python or default_py)
        form.addRow("J6B 使用的 Python：", self._wrap(self._browse_row(self.j6b_python_edit, False, "选择 Python 解释器")))

        self.mmt_python_edit = QLineEdit(settings.mmt_python or default_py)
        form.addRow("MMT 使用的 Python：", self._wrap(self._browse_row(self.mmt_python_edit, False, "选择 Python 解释器")))

        self.compare_python_edit = QLineEdit(settings.compare_python or default_py)
        form.addRow(
            "对比工具使用的 Python：", self._wrap(self._browse_row(self.compare_python_edit, False, "选择 Python 解释器"))
        )

        layout.addLayout(form)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._validate_repo_root()
        self.repo_root_edit.textChanged.connect(lambda _text: self._validate_repo_root())

    def _wrap(self, row: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(row)
        return widget

    def _browse_row(self, line_edit: QLineEdit, is_dir: bool, title: str) -> QHBoxLayout:
        return _browse_row(line_edit, is_dir, self, title)

    def _validate_repo_root(self) -> None:
        root = Path(self.repo_root_edit.text().strip() or ".")
        missing = [
            name
            for name in (J6B_DIR_NAME, MMT_DIR_NAME, COMPARE_DIR_NAME)
            if not (root / name).is_dir()
        ]
        if missing:
            self.status_label.setText("缺少子目录：" + "、".join(missing))
            self.status_label.setStyleSheet("color: #d93025;")
        else:
            self.status_label.setText("目录检查通过。")
            self.status_label.setStyleSheet("color: #188038;")

    def _on_accept(self) -> None:
        root = Path(self.repo_root_edit.text().strip())
        missing = [
            name
            for name in (J6B_DIR_NAME, MMT_DIR_NAME, COMPARE_DIR_NAME)
            if not (root / name).is_dir()
        ]
        if missing:
            QMessageBox.warning(self, "目录不完整", "以下子目录未找到：\n" + "\n".join(missing))
            return
        self.accept()

    def result_settings(self) -> AppSettings:
        return AppSettings(
            repo_root=self.repo_root_edit.text().strip(),
            j6b_python=self.j6b_python_edit.text().strip(),
            mmt_python=self.mmt_python_edit.text().strip(),
            compare_python=self.compare_python_edit.text().strip(),
            max_concurrent_jobs=self._settings.max_concurrent_jobs,
        )
