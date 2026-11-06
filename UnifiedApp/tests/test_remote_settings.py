import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from UnifiedApp.unified_app.ui.remote_settings_dialog import RemoteSettingsDialog


class RemoteSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_persists_remote_paths_without_overwriting_bot_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            report_dir = root / "reports"
            input_dir.mkdir()
            output_dir.mkdir()
            report_dir.mkdir()
            report_file = root / "report.xlsx"
            report_file.write_bytes(b"xlsx fixture")
            config_path = root / "feishu_bot.json"
            original = {
                "enabled": True,
                "owner_open_id": "ou_private",
                "chat_id": "oc_private",
                "profile": "local-profile",
                "remote": {"machine_name": "old-machine", "path_aliases": {}},
            }
            config_path.write_text(json.dumps(original), encoding="utf-8")

            dialog = RemoteSettingsDialog(config_file=config_path)
            dialog.machine_name_edit.setText("mine")
            dialog._add_alias("incoming", str(input_dir), "input")
            dialog._add_alias("outgoing", str(output_dir), "output")
            dialog._add_alias("reports", str(report_dir), "report")
            dialog._add_alias("workbook", str(report_file), "report")
            dialog._on_accept()

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved,
                {
                    **original,
                    "remote": {
                        "machine_name": "mine",
                        "path_aliases": {
                            "incoming": {"path": str(input_dir), "access": "input"},
                            "outgoing": {"path": str(output_dir), "access": "output"},
                            "reports": {"path": str(report_dir), "access": "report"},
                            "workbook": {"path": str(report_file), "access": "report"},
                        },
                    },
                },
            )
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            displayed_text = " ".join(
                widget.text() for widget in dialog.findChildren(QWidget)
                if hasattr(widget, "text")
            )
            self.assertNotIn("ou_private", displayed_text)
            self.assertNotIn("oc_private", displayed_text)

    def test_rejects_empty_or_duplicate_names_relative_paths_and_invalid_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_dir = root / "valid"
            valid_dir.mkdir()
            valid_file = root / "report.xlsx"
            valid_file.write_bytes(b"xlsx fixture")
            dialog = RemoteSettingsDialog(config_file=root / "config.json")

            dialog._add_alias("", str(valid_dir), "input")
            self.assertIn("不能为空", dialog.validation_error())
            dialog.alias_table.removeRow(0)

            dialog._add_alias("same", str(valid_dir), "input")
            dialog._add_alias(" SAME ", str(valid_dir), "output")
            self.assertIn("重复", dialog.validation_error())
            dialog.alias_table.removeRow(1)

            dialog.alias_table.item(0, 1).setText("relative/path")
            self.assertIn("绝对路径", dialog.validation_error())
            dialog.alias_table.item(0, 1).setText(str(valid_dir))

            invalid_access = dialog.alias_table.cellWidget(0, 2)
            invalid_access.addItem("invalid")
            invalid_access.setCurrentText("invalid")
            self.assertIn("访问类型无效", dialog.validation_error())
            dialog.alias_table.cellWidget(0, 2).setCurrentText("input")

            dialog.alias_table.item(0, 1).setText(str(root / "missing"))
            self.assertIn("必须是已存在的目录", dialog.validation_error())
            dialog.alias_table.item(0, 1).setText(str(valid_dir))
            dialog.alias_table.cellWidget(0, 2).setCurrentText("output")
            dialog.alias_table.item(0, 1).setText(str(root / "missing-output"))
            self.assertIn("output 路径必须是已存在的目录", dialog.validation_error())

            dialog.alias_table.cellWidget(0, 2).setCurrentText("report")
            dialog.alias_table.item(0, 1).setText(str(root / "report.csv"))
            self.assertIn("XLSX 文件或目录", dialog.validation_error())
            dialog.alias_table.item(0, 1).setText(str(valid_file))
            self.assertEqual(dialog.validation_error(), "")

    def test_invalid_configuration_does_not_save_or_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "feishu_bot.json"
            original = {"enabled": True, "owner_open_id": "private"}
            config_path.write_text(json.dumps(original), encoding="utf-8")
            dialog = RemoteSettingsDialog(config_file=config_path)
            dialog._add_alias("relative", "relative/path", "input")
            with patch("UnifiedApp.unified_app.ui.remote_settings_dialog.QMessageBox.warning"):
                dialog._on_accept()
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), original)
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)


class _PresetForm(QWidget):
    def __init__(self):
        super().__init__()
        self.unsaved_value = "typed but not saved"
        self.refresh_count = 0
        self.apply_count = 0

    def _refresh_presets(self):
        self.refresh_count += 1

    def _apply_spec(self, _spec):
        self.apply_count += 1


class _Bridge(QObject):
    log = Signal(str)
    presets_changed = Signal(str)

    def start(self):
        return False


class MainWindowRemotePresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_remote_preset_signal_refreshes_only_matching_form(self):
        from UnifiedApp.unified_app.ui import main_window

        j6b_form = _PresetForm()
        mmt_form = _PresetForm()
        settings = type("Settings", (), {"max_concurrent_jobs": 1, "is_valid": lambda _self: True})()
        with patch.object(main_window, "load_settings", return_value=settings), \
                patch.object(main_window, "J6BForm", return_value=j6b_form), \
                patch.object(main_window, "MMTForm", return_value=mmt_form), \
                patch.object(main_window, "CompareForm", return_value=QWidget()), \
                patch.object(main_window, "JobPanel", return_value=QWidget()), \
                patch.object(main_window, "FeishuBridge", return_value=_Bridge()):
            window = main_window.MainWindow()
            try:
                self.app.processEvents()
                bridge = window.feishu_bridge
                bridge.presets_changed.emit("mmt")
                self.assertEqual(j6b_form.refresh_count, 0)
                self.assertEqual(mmt_form.refresh_count, 1)
                bridge.presets_changed.emit("j6b")
                self.assertEqual(j6b_form.refresh_count, 1)
                self.assertEqual(mmt_form.refresh_count, 1)
                self.assertEqual(j6b_form.unsaved_value, "typed but not saved")
                self.assertEqual(mmt_form.unsaved_value, "typed but not saved")
                self.assertEqual(j6b_form.apply_count, 0)
                self.assertEqual(mmt_form.apply_count, 0)
            finally:
                window.close()
                window.deleteLater()


if __name__ == "__main__":
    unittest.main()
