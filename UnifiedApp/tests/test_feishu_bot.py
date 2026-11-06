import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from UnifiedApp.unified_app.feishu_bot import (
    FeishuBridge, authorized_event, load_bot_config, normalize_event,
)
from UnifiedApp.unified_app.job_manager import Job, JobManager


class FeishuBotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def event(self, **overrides):
        return dict({"chat_type": "p2p", "sender_type": "user", "message_type": "text",
                     "sender_id": "ou_owner", "chat_id": "oc_private", "message_id": "om_once",
                     "create_time": str(int(time.time() * 1000)), "content": "帮助"}, **overrides)

    def config(self):
        return {"enabled": True, "owner_open_id": "ou_owner", "chat_id": "oc_private"}

    def test_desktop_starts_bot_and_registers_application_exit_cleanup(self):
        from UnifiedApp.unified_app.ui.main_window import MainWindow

        module = "UnifiedApp.unified_app.ui.main_window"
        settings = Mock(max_concurrent_jobs=1)
        settings.is_valid.return_value = True
        with patch(module + ".load_settings", return_value=settings), \
                patch(module + ".J6BForm", side_effect=lambda *_: QWidget()), \
                patch(module + ".MMTForm", side_effect=lambda *_: QWidget()), \
                patch(module + ".CompareForm", side_effect=lambda *_: QWidget()), \
                patch(module + ".JobPanel", side_effect=lambda *_: QWidget()), \
                patch(module + ".FeishuBridge") as bridge_class:
            bridge = bridge_class.return_value
            bridge.start.return_value = True
            window = MainWindow()
            try:
                application = Mock()
                with patch.object(QApplication, "instance", return_value=application):
                    self.app.processEvents()
                bridge_class.assert_called_once_with(window.job_manager, window)
                bridge.start.assert_called_once_with()
                application.aboutToQuit.connect.assert_called_once_with(bridge.stop)
                self.assertIn("飞书助手正在连接", window.statusBar().currentMessage())
            finally:
                window.close()
                window.deleteLater()

    def test_desktop_bot_start_failure_is_visible(self):
        from UnifiedApp.unified_app.ui.main_window import MainWindow

        window = Mock()
        with patch("UnifiedApp.unified_app.ui.main_window.FeishuBridge") as bridge_class:
            bridge_class.return_value.start.side_effect = FileNotFoundError("CLI unavailable")
            MainWindow._start_feishu(window)
        self.assertIn("CLI unavailable", window.statusBar.return_value.showMessage.call_args.args[0])

    def test_only_fresh_owner_p2p_text(self):
        self.assertTrue(authorized_event(self.event(), self.config()))
        for change in [{"sender_id": "ou_other"}, {"chat_type": "group"},
                       {"sender_type": "bot"}, {"chat_id": "oc_other"},
                       {"message_type": "file"}, {"message_id": ""},
                       {"create_time": "0"}, {"create_time": "nonsense"}]:
            self.assertFalse(authorized_event(self.event(**change), self.config()), change)

    def test_normalize_event_preserves_identity_and_decodes_only_text(self):
        event = self.event(content=json.dumps({"text": "绑定挖掘助手 123456"}))
        normalized = normalize_event({"data": event})
        self.assertEqual(normalized["content"], "绑定挖掘助手 123456")
        self.assertEqual(normalized["sender_id"], "ou_owner")
        raw = {"event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_owner"}},
                         "message": {k: v for k, v in event.items() if not k.startswith("sender")}}}
        self.assertEqual(normalize_event(raw), normalized)

    def test_binding_requires_exact_code_and_persists_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.json"
            bridge = FeishuBridge(JobManager(), path=path, bind_code="123456")
            bridge.send_text = Mock()
            bridge.handle_event(self.event(content="绑定挖掘助手 wrong"))
            self.assertFalse(path.exists())
            bridge.handle_event(self.event(content="绑定挖掘助手 123456"))
            saved = load_bot_config(path)
            self.assertEqual(saved["owner_open_id"], "ou_owner")
            self.assertNotIn("secret", json.dumps(saved).lower())
            bridge.send_text.assert_called_once()

    def test_duplicate_remote_run_is_submitted_once_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            db_path = Path(tmp) / "messages.sqlite3"
            for _ in range(2):
                bridge = FeishuBridge(JobManager(), path=path)
                bridge.db = sqlite3.connect(str(db_path))
                bridge.db.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, received REAL)")
                bridge.db.commit()
                bridge.send_text = Mock()
                with patch("UnifiedApp.unified_app.feishu_bot.RemoteController") as controller:
                    remote = controller.return_value
                    remote.identity = ["ou_owner", "oc_private"]
                    remote.handle.return_value = "请确认运行"
                    event = self.event(content="运行 J6B saved preset")
                    bridge.handle_event(event)
                    bridge.handle_event(event)
                    if _ == 0:
                        remote.handle.assert_called_once_with("运行 J6B saved preset")
                    else:
                        remote.handle.assert_not_called()
                bridge.db.close()

    def test_no_arbitrary_preset_path(self):
        from UnifiedApp.unified_app.remote_control import RemoteController
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            controller = RemoteController(Mock(), path, Mock(), Mock())
            with patch("UnifiedApp.unified_app.remote_control.presets.load_preset") as load:
                for text in ("运行 j6b ../../secrets", "运行 shell valid"):
                    with self.assertRaises(ValueError):
                        controller.handle(text)
            load.assert_not_called()

    def test_progress_notifications_are_throttled_and_ignore_stopped_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            manager = JobManager()
            job = Job("mine", "mmt", "test", [], Path(tmp), status="排队中")
            manager._jobs[job.id] = job
            bridge = FeishuBridge(manager, path=path)
            bridge.send_text = Mock()
            with patch("UnifiedApp.unified_app.feishu_bot.time.time", return_value=100):
                bridge._job_updated(job.id)
                bridge._job_updated(job.id)
                job.status = "运行中"
                bridge._job_updated(job.id)
                job.percent = 21
                bridge._job_updated(job.id)
            self.assertEqual(bridge.send_text.call_count, 2)
            with patch("UnifiedApp.unified_app.feishu_bot.time.time", return_value=161):
                bridge._job_updated(job.id)
            self.assertEqual(bridge.send_text.call_count, 3)
            bridge.stopped = True
            bridge._job_finished(job.id)
            self.assertEqual(bridge.send_text.call_count, 3)

    def test_live_binding_change_does_not_send_queued_messages_to_new_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            bridge = FeishuBridge(JobManager(), path=path)
            bridge._process = Mock()
            path.write_text(json.dumps({**self.config(), "owner_open_id": "ou_other"}), encoding="utf-8")
            bridge.send_text("old owner's result")
            self.assertTrue(bridge.stopped)
            bridge._process.assert_not_called()

    def test_remote_result_resend_uses_persistent_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "bot.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            manager = JobManager()
            job = Job("mine", "mmt", "test", [], root, bundle_path=root / "result.zip")
            manager._jobs[job.id] = job
            bridge = FeishuBridge(manager, path=path)
            bridge._poll_outbox = Mock()
            with patch("UnifiedApp.unified_app.feishu_bot.enqueue_bundle", return_value="delivery") as enqueue:
                bridge._bundle_ready(job.id, resend=True)
            enqueue.assert_called_once_with(job.bundle_path.resolve(), job.title, job.id, path=path, resend=True)
            bridge._poll_outbox.assert_called_once_with()
            self.assertFalse(bridge.queue)

    def test_zip_signal_emitted_only_after_successful_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad.xlsx"
            bad.write_bytes(b"fixture")
            manager = JobManager()
            source = Job("mine", "j6b", "test", [], root, bad_case_report_path=bad,
                         bundle_options={"output_dir": str(root), "vehicle": "E2LB-2", "version": "v1"})
            calls = []
            manager.bundle_ready.connect(calls.append)
            manager._bundle_reports(source)
            manager._bundle_reports(source)
            self.assertTrue(source.bundle_path.is_file())
            self.assertEqual(calls, ["mine"])

    def test_sender_uses_argv_file_cwd_and_waits_for_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "bot.json"
            path.write_text(json.dumps(self.config()), encoding="utf-8")
            fake = root / "fake_cli.py"
            fake.write_text("import sys,json,pathlib\n"
                            "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\n"
                            "print(json.dumps({'ok': True, 'data': {'message_id': 'om_test'}}))\n", encoding="utf-8")
            bundle = root / "result & model.zip"
            bundle.write_bytes(b"zip fixture")
            manager = JobManager()
            job = Job("mine", "j6b", "test", [], root, bundle_path=bundle)
            manager._jobs[job.id] = job
            bridge = FeishuBridge(manager, path=path)
            bridge.prefix = [sys.executable, str(fake)]
            bridge._bundle_ready(job.id)
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if bridge.sender is None else None)
            poll.start(20)
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            poll.stop()
            self.assertIsNone(bridge.sender)
            argv = json.loads((root / "argv.json").read_text(encoding="utf-8"))
            self.assertIn("./result & model.zip", argv)
            self.assertIn("ou_owner", argv)
            self.assertTrue(any("推送成功" in line for line in job.log_lines))
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
