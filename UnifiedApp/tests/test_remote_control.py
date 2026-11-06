from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from UnifiedApp.unified_app import presets
from UnifiedApp.unified_app.job_manager import Job
from UnifiedApp.unified_app.job_spec import J6BJobSpec, MMTJobSpec, validate_job_spec
from UnifiedApp.unified_app.paths import AppSettings
from UnifiedApp.unified_app.remote_control import RemoteController


class RemoteControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / "input"
        self.outputs = self.root / "output"
        self.inputs.mkdir()
        self.outputs.mkdir()
        for name in ("J6B_UnifiedMining", "MMT_UnifiedMining", "CompareKPI_United"):
            engine = self.root / name
            engine.mkdir()
            (engine / "DataMining.py").write_text("# fixture\n", encoding="utf-8")
            (engine / "config.json").write_text(json.dumps({
                "output_dir": str(self.outputs),
                "pipelines": [{"type": "can"}, {"type": "topic"}],
                "signals": {"unchanged": "local template mapping"},
            }), encoding="utf-8")
        filters = self.root / "J6B_UnifiedMining" / "filters"
        filters.mkdir()
        (filters / "follow_start_latency.py").write_text("", encoding="utf-8")
        self.settings = AppSettings(repo_root=str(self.root))
        self.config_path = self.root / "feishu_bot.json"
        self.config = {
            "enabled": True, "owner_open_id": "ou_owner", "chat_id": "oc_chat",
            "remote": {"machine_name": "test-machine", "path_aliases": {
                "数据": {"path": str(self.inputs), "access": "input"},
                "报告": {"path": str(self.outputs), "access": "output"},
                "基线": {"path": str(self.outputs), "access": "report"},
            }},
        }
        self.write_config()
        self.manager = Mock()
        self.manager.jobs.return_value = []
        self.manager.running_count.return_value = 0
        self.manager.submit_mmt.return_value = "a" * 32
        self.manager.submit_j6b.return_value = "b" * 32
        self.changed, self.resend = Mock(), Mock()
        self.settings_patch = patch("UnifiedApp.unified_app.remote_control.load_settings", return_value=self.settings)
        self.presets_patch = patch("UnifiedApp.unified_app.presets.presets_dir", return_value=self.root / "presets")
        self.settings_patch.start()
        self.presets_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.presets_patch.stop)
        self.controller = self.new_controller()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def new_controller(self):
        return RemoteController(self.manager, self.config_path, self.changed, self.resend)

    def create_draft(self, kind="MMT"):
        self.controller.handle(f"新建预设 {kind} daily")
        self.controller.handle("修改草稿\n输入目录=数据\n输出目录=报告\n车型=E2LB\n"
                               "报告软件版本=V1\n日期=20261101-20261105\n动力形式=PHEV")

    def confirm(self, action):
        return self.controller.handle(f"确认{action} {self.controller.pending['code']}")

    def test_draft_atomic_updates_persistence_and_alias_resolution(self):
        self.create_draft()
        before = deepcopy(self.controller.draft)
        with self.assertRaisesRegex(ValueError, "动力形式"):
            self.controller.handle("修改草稿\n车型=OTHER\n动力形式=hybrid")
        self.assertEqual(self.controller.draft, before)
        self.controller.handle("修改草稿\n软件筛选=V_filter\nCAN流水线=关闭\n扫描深度=8")
        self.assertEqual(self.controller.draft["data"]["software_version"], "V1")
        self.assertEqual(self.controller.draft["data"]["query_software"], "V_filter")
        self.assertEqual(self.new_controller().draft, self.controller.draft)
        preview = self.controller.handle("运行草稿")
        self.assertIn("test-machine", preview)
        self.manager.submit_mmt.assert_not_called()
        self.confirm("运行")
        spec = self.manager.submit_mmt.call_args.args[3]
        self.assertEqual(spec.source_path, str(self.inputs.resolve()))
        self.assertEqual(spec.output_dir, str(self.outputs.resolve()))
        self.assertFalse(spec.enable_can_pipeline)
        self.assertEqual(spec.query_software, "V_filter")
        self.assertEqual(spec.software_version, "V1")
        with self.assertRaisesRegex(ValueError, "没有待确认"):
            self.controller.handle("确认运行 WRONG")
        self.manager.submit_mmt.assert_called_once()

    def test_confirm_expiry_restart_wrong_code_and_edit_invalidation(self):
        self.create_draft()
        self.controller.handle("运行草稿")
        code = self.controller.pending["code"]
        with self.assertRaisesRegex(ValueError, "确认码"):
            self.controller.handle("确认运行 WRONG")
        self.assertEqual(code, self.controller.pending["code"])
        with self.assertRaisesRegex(ValueError, "重启"):
            self.new_controller().handle(f"确认运行 {code}")
        self.controller.handle("修改 日期=20261106")
        self.assertIsNone(self.controller.pending)
        self.controller.handle("运行草稿")
        with patch("UnifiedApp.unified_app.remote_control.time.time", return_value=time.time() + 301):
            with self.assertRaisesRegex(ValueError, "过期"):
                self.confirm("运行")
        self.manager.submit_mmt.assert_not_called()

    def test_run_revalidates_template_environment_paths_and_binding(self):
        self.create_draft()
        template = self.root / "MMT_UnifiedMining" / "config.json"
        self.controller.handle("运行草稿")
        template.write_text('{"output_dir": "different", "pipelines": [{"type": "can"}]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "变更"):
            self.confirm("运行")
        self.controller.handle("运行草稿")
        self.config["remote"]["path_aliases"].pop("数据")
        self.write_config()
        with self.assertRaises(ValueError):
            self.confirm("运行")
        self.config["owner_open_id"] = "ou_new"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "绑定"):
            self.controller.handle("任务")
        self.assertIsNone(self.new_controller().draft)
        self.manager.submit_mmt.assert_not_called()

    def test_remote_paths_cannot_escape_authorized_roots(self):
        self.create_draft()
        for path in (str(self.root), str(self.inputs / ".." / "outside"), "relative", "C:relative"):
            self.controller.handle(f"修改 输入目录={path}")
            with self.assertRaises(ValueError):
                self.controller.handle("运行草稿")
        self.controller.handle("修改 输入目录=数据")
        self.controller.handle(f"修改 输出目录={self.outputs / '..' / 'outside'}")
        with self.assertRaisesRegex(ValueError, "允许"):
            self.controller.handle("运行草稿")
        self.controller.handle("修改 输出目录=报告")
        symlink = self.inputs / "escape"
        try:
            symlink.symlink_to(self.root, target_is_directory=True)
        except OSError:
            pass
        else:
            self.controller.handle(f"修改 输入目录={symlink}")
            with self.assertRaisesRegex(ValueError, "允许"):
                self.controller.handle("运行草稿")
        self.manager.submit_mmt.assert_not_called()

    def test_save_overwrite_conflict_delete_confirmation(self):
        self.create_draft()
        self.controller.handle("保存预设")
        saved = presets.load_preset("mmt", "daily")
        self.assertEqual(saved["source_path"], str(self.inputs.resolve()))
        self.assertEqual(saved["output_dir"], str(self.outputs.resolve()))
        self.changed.assert_called_with("mmt")
        self.controller.handle("修改 日期=20261106")
        self.assertEqual(presets.load_preset("mmt", "daily"), saved)
        self.controller.handle("覆盖预设")
        self.assertIn("日期", self.controller.handle("覆盖预设"))
        presets.save_preset("mmt", "daily", {**saved, "task_name": "desktop changed"})
        with self.assertRaisesRegex(ValueError, "已被修改"):
            self.confirm("覆盖")
        self.assertEqual(presets.load_preset("mmt", "daily")["task_name"], "desktop changed")
        self.controller.handle("另存预设 MMT other")
        self.assertEqual(presets.load_preset("mmt", "other")["query_date"], "20261106")
        self.controller.handle("删除预设 MMT other")
        self.assertIn("other", presets.list_presets("mmt"))
        self.confirm("删除")
        self.assertNotIn("other", presets.list_presets("mmt"))
        self.assertTrue(self.inputs.is_dir())

    def test_successful_overwrite_updates_revision_and_runs_saved_preset(self):
        self.create_draft()
        self.controller.handle("保存预设")
        self.controller.handle("修改 日期=20261106")
        self.controller.handle("覆盖预设")
        self.confirm("覆盖")
        self.assertEqual(presets.load_preset("mmt", "daily")["query_date"], "20261106")
        preview = self.controller.handle("运行 MMT daily")
        self.assertIn("确认运行", preview)
        self.manager.submit_mmt.assert_not_called()
        self.confirm("运行")
        self.manager.submit_mmt.assert_called_once()

    def test_j6b_metrics_baseline_and_multisource_fields(self):
        self.create_draft("J6B")
        self.controller.handle("修改草稿\n启用指标=follow_start_latency\n信号范围=debugcan_only\n信号图=开启")
        baseline = self.outputs / "baseline.xlsx"
        baseline.write_bytes(b"fixture")
        self.controller.handle(f"修改 基线报告={baseline}")
        self.controller.handle("运行草稿")
        self.confirm("运行")
        spec = self.manager.submit_j6b.call_args.args[3]
        self.assertTrue(spec.bad_case_signal_plots)
        self.assertEqual(spec.input_paths, [str(self.inputs.resolve())])
        self.controller.handle("修改 启用指标=not_a_metric")
        with self.assertRaisesRegex(ValueError, "未知指标"):
            self.controller.handle("运行草稿")

    def test_parameter_whitelist_types_and_presets_names(self):
        self.create_draft()
        for text in ("修改 python=evil", "修改 模板配置=evil", "修改 CAN流水线=maybe",
                     "修改 扫描深度=21", "修改 日期=20261107-20261101",
                     "修改 日期=20260230", "修改 文件匹配=../*.h5",
                     "修改 报告名称=../escape", "修改草稿\n日期=20261101\n日期=20261102"):
            with self.assertRaises(ValueError, msg=text):
                self.controller.handle(text)
        for text in ("新建预设 MMT ../../evil", "新建预设 ../mmt name",
                     "新建预设 MMT ..", "保存预设 CON", "另存预设 J6B wrong"):
            with self.assertRaises(ValueError, msg=text):
                self.controller.handle(text)
        with self.assertRaisesRegex(ValueError, "类型"):
            validate_job_spec(MMTJobSpec(enable_can_pipeline="false"))
        with self.assertRaisesRegex(ValueError, "未知预设类型"):
            presets.load_preset("../mmt", "test")

    def test_targeted_job_status_cancel_results_and_log_limits(self):
        self.create_draft()
        bundle = self.outputs / "bundle.zip"
        bundle.write_bytes(b"fixture")
        job = Job("abcdef012345", "mmt", "test", [], self.root, status="运行中",
                  bundle_path=bundle, log_lines=["x" * 1000] * 20)
        self.manager.jobs.return_value = [job]
        self.assertIn(job.id, self.controller.handle("状态 abcdef"))
        self.assertLess(len(self.controller.handle("日志 abcdef")), 3100)
        self.controller.handle("结果 abcdef")
        self.resend.assert_called_once_with(job.id)
        self.controller.handle("取消 abcdef")
        self.manager.cancel.assert_not_called()
        self.confirm("取消")
        self.manager.cancel.assert_called_once_with(job.id)
        second = Job("abcdef999999", "j6b", "second", [], self.root)
        self.manager.jobs.return_value = [job, second]
        with self.assertRaisesRegex(ValueError, "不唯一"):
            self.controller.handle("取消 abcdef")
        self.controller.handle("取消 abcdef0")
        job.status = "成功"
        with self.assertRaisesRegex(ValueError, "结束"):
            self.confirm("取消")

    def test_confirmed_remote_jobs_reach_real_queue_and_generate_current_bundle(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        from UnifiedApp.unified_app.job_manager import JobManager
        from UnifiedApp.tests.test_report_bundle import MINING_SCRIPT
        import sys

        app = QApplication.instance() or QApplication([])
        self.settings.j6b_python = self.settings.mmt_python = sys.executable
        for kind, engine_name in (("J6B", "J6B_UnifiedMining"), ("MMT", "MMT_UnifiedMining")):
            (self.root / engine_name / "DataMining.py").write_text(MINING_SCRIPT, encoding="utf-8")
            self.manager = JobManager(max_concurrent=1)
            self.controller = self.new_controller()
            self.create_draft(kind)
            if kind == "J6B":
                self.controller.handle("修改 延迟检查=关闭")
            self.controller.handle("运行草稿")
            self.assertFalse(self.manager.jobs())
            with patch.dict(os.environ, {"UNIFIED_MINING_DISABLE_PUSH": "1"}):
                response = self.confirm("运行")
                loop = QEventLoop()
                poll = QTimer()
                poll.timeout.connect(lambda: loop.quit() if self.manager.jobs()[-1].bundle_path else None)
                poll.start(20)
                QTimer.singleShot(10000, loop.quit)
                loop.exec()
                poll.stop()
            job = self.manager.jobs()[-1]
            if job.process is not None and job.status == "运行中":
                self.manager.cancel(job.id)
                job.process.waitForFinished(3000)
            self.assertIn(job.id, response)
            self.assertEqual(job.status, "成功", job.log_lines)
            self.assertIsNotNone(job.bundle_path, job.log_lines)
            self.assertEqual(job.bundle_path.parent, self.outputs.resolve())
            with ZipFile(job.bundle_path) as archive:
                self.assertEqual(set(archive.namelist()), {"summary.xlsx", "bad_case_report.xlsx"})
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
