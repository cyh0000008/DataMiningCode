from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QProcess, QTimer
from PySide6.QtWidgets import QApplication

from UnifiedApp.tests import test_remote_control as remote_fixtures
from UnifiedApp.unified_app import presets
from UnifiedApp.unified_app.feishu_bot import FeishuBridge
from UnifiedApp.unified_app.job_manager import Job, JobManager
from UnifiedApp.unified_app.remote_cards import RemoteCards, normalize_card_event


def walk(node, in_form=False):
    if isinstance(node, dict):
        yield node, in_form
        for value in node.values():
            yield from walk(value, in_form or node.get("tag") == "form")
    elif isinstance(node, list):
        for item in node:
            yield from walk(item, in_form)


class RemoteCardTests(unittest.TestCase):
    setUp = remote_fixtures.RemoteControlTests.setUp
    write_config = remote_fixtures.RemoteControlTests.write_config
    new_controller = remote_fixtures.RemoteControlTests.new_controller
    create_draft = remote_fixtures.RemoteControlTests.create_draft

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def cards(self, kind="MMT"):
        self.create_draft(kind)
        self.views = RemoteCards(self.controller)
        return self.views

    def click(self, view, label, values=None, **changes):
        card, token = view
        self.views.sent(token, "om_" + token)
        button = next(n for n, _ in walk(card) if n.get("tag") == "button" and n["text"]["content"] == label)
        event = dict({"action_name": button["name"], "action_tag": "button", "form_value": values or {},
                      "message_id": "om_" + token, "event_id": "ev_" + token}, **changes)
        return self.views.handle(event)

    def test_all_page_shapes_defaults_and_size(self):
        for kind in ("J6B", "MMT"):
            self.cards(kind)
            self.controller.handle("保存预设")
            job = Job("a" * 32, kind.lower(), "<at id=all>title</at>", [], self.root, status="运行中")
            self.manager.jobs.return_value = [job]
            self.controller.handle("运行草稿")
            views = [self.views.home(), self.views.platform(kind.lower()), self.views.editor(),
                     self.views.editor("advanced"), self.views.tasks(), self.views.task(job.id),
                     self.views.confirmation("summary")]
            for card, _ in views:
                self.assertEqual(card["schema"], "2.0")
                self.assertFalse(card["config"]["enable_forward"])
                self.assertLess(len(json.dumps(card, ensure_ascii=False).encode("utf-8")), 28000)
                self.assertTrue(2 <= len(card["body"]["elements"]) <= 5)
                nodes = list(walk(card))
                self.assertLessEqual(sum("tag" in n for n, _ in nodes), 200)
                names = [n["name"] for n, _ in nodes if "name" in n]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(sum(n.get("type") in ("primary_filled", "danger_filled") for n, _ in nodes), 1)
                for node, in_form in nodes:
                    if node.get("tag") == "form":
                        self.assertFalse(in_form)
                    if node.get("tag") == "column_set":
                        self.assertTrue(all(n["tag"] == "column" for n in node["columns"]))
                    if node.get("tag") == "button":
                        self.assertEqual("form_action_type" in node, in_form)
                        self.assertEqual("behaviors" in node, not in_form)
                    if node.get("tag") in ("select_static", "multi_select_static", "date_picker"):
                        self.assertNotIn("label", node)
            basic = self.views.editor()[0]
            fields = {n.get("name"): n for n, _ in walk(basic) if "name" in n}
            self.assertEqual(fields["field_vehicle"]["default_value"], "E2LB")
            self.assertEqual(fields["date_start"]["initial_date"], "2026-11-01")
            self.assertEqual(fields["date_end"]["initial_date"], "2026-11-05")
            node = fields["field_input_paths" if kind == "J6B" else "field_source_path"]
            self.assertIn("数据", [option["value"] for option in node["options"]])
            self.assertNotIn("<at", json.dumps(self.views.task(job.id)[0]))
            self.manager.jobs.return_value = []

    def test_form_paging_preserves_parameters_and_save_as_name(self):
        self.cards()
        advanced = self.click(self.views.editor(), "高级参数",
                              {"preset_name": "release preset", "field_vehicle": "E3",
                               "field_query_software": "DATA_FILTER", "date_start": "2026-11-02 +0800",
                               "date_end": "2026-11-06 +0800"})
        self.assertEqual(self.controller.draft["data"]["vehicle"], "E3")
        self.assertEqual(self.controller.draft["data"]["software_version"], "V1")
        self.assertEqual(self.controller.draft["data"]["query_software"], "DATA_FILTER")
        self.assertEqual(self.controller.draft["data"]["query_date"], "20261102-20261106")
        basic = self.click(advanced, "基本参数", {"field_max_depth": "8"})
        name = next(n["default_value"] for n, _ in walk(basic[0]) if n.get("name") == "preset_name")
        self.assertEqual(name, "release preset")
        result = self.click(basic, "保存 / 另存为")
        self.assertIn("已保存预设", json.dumps(result[0], ensure_ascii=False))
        saved = presets.load_preset("mmt", "release preset")
        self.assertEqual(saved["source_path"], str(self.inputs.resolve()))
        self.assertEqual(saved["output_dir"], str(self.outputs.resolve()))
        self.assertEqual(saved["max_depth"], 8)
        self.changed.assert_called_once_with("mmt")

    def test_existing_save_requires_button_confirmation_and_revision(self):
        self.cards()
        self.controller.handle("保存预设")
        confirm = self.click(self.views.editor(), "保存 / 另存为", {"field_vehicle": "CHANGED"})
        self.assertEqual(self.controller.pending["action"], "覆盖")
        self.assertEqual(presets.load_preset("mmt", "daily")["vehicle"], "E2LB")
        self.click(confirm, "确认覆盖")
        self.assertEqual(presets.load_preset("mmt", "daily")["vehicle"], "CHANGED")
        confirm = self.click(self.views.editor(), "保存 / 另存为", {"field_vehicle": "REMOTE"})
        saved = presets.load_preset("mmt", "daily")
        presets.save_preset("mmt", "daily", dict(saved, vehicle="LOCAL"))
        with self.assertRaisesRegex(ValueError, "修改"):
            self.click(confirm, "确认覆盖")
        self.assertEqual(presets.load_preset("mmt", "daily")["vehicle"], "LOCAL")

    def test_platform_new_open_and_delete(self):
        self.views = RemoteCards(self.controller)
        platform = self.click(self.views.home(), "MMT 挖掘")
        editor = self.click(platform, "新建预设", {"new_name": "test"})
        self.assertIn("参数编辑", editor[0]["header"]["title"]["content"])
        self.controller.handle("修改草稿\n输入目录=数据\n输出目录=报告")
        self.controller.handle("保存预设")
        editor = self.click(self.views.platform("mmt"), "编辑所选预设")
        self.assertEqual(self.controller.draft["name"], "test")
        confirm = self.click(self.views.platform("mmt"), "删除所选预设")
        self.assertIn("test", presets.list_presets("mmt"))
        self.click(confirm, "确认删除")
        self.assertNotIn("test", presets.list_presets("mmt"))

    def test_once_expiry_restart_draft_and_message_binding(self):
        self.cards()
        view = self.views.editor()
        with self.assertRaisesRegex(ValueError, "来源"):
            self.click(view, "高级参数", message_id="om_foreign")
        self.click(view, "高级参数")
        with self.assertRaisesRegex(ValueError, "过期"):
            self.click(view, "高级参数")
        view = self.views.editor()
        self.controller.handle("修改 车型=NEW")
        with self.assertRaisesRegex(ValueError, "草稿已变更"):
            self.click(view, "预览运行")
        view = self.views.home()
        self.views.sessions[view[1]]["expires"] = time.time() - 1
        with self.assertRaisesRegex(ValueError, "过期"):
            self.click(view, "J6B 挖掘")
        view = self.views.home()
        self.views = RemoteCards(self.controller)
        with self.assertRaisesRegex(ValueError, "重启"):
            self.click(view, "J6B 挖掘")
        self.manager.submit_mmt.assert_not_called()

    def test_tampered_fields_options_names_and_atomic_invalid_edit(self):
        self.cards()
        cases = [
            ("基本参数", {"field_unknown": "value"}),
            ("基本参数", {"field_max_depth": "99", "field_task_name": "SHOULD_NOT_CHANGE"}),
            ("基本参数", {"field_resume_mode": "tampered"}),
            ("基本参数", {"field_report_prefix": "inject\n车型=NEW"}),
        ]
        for label, values in cases:
            before = deepcopy(self.controller.draft)
            with self.assertRaises(ValueError):
                self.click(self.views.editor("advanced"), label, values)
            self.assertEqual(self.controller.draft, before)
        for values in ({"field_source_path": "outside"}, {"preset_name": "../escape"},
                       {"date_start": "2026-02-30", "date_end": "2026-03-01"},
                       {"date_start": "", "date_end": "2026-11-05"},
                       {"field_vehicle": {"command": "execute"}}):
            before = deepcopy(self.controller.draft)
            with self.assertRaises(ValueError):
                self.click(self.views.editor(), "保存 / 另存为", values)
            self.assertEqual(self.controller.draft, before)
        with self.assertRaisesRegex(ValueError, "过期"):
            self.click(self.views.home(), "J6B 挖掘", action_name="mc_" + "0" * 24 + "_0")

    def test_j6b_multi_path_and_disabled_pipeline_validation(self):
        self.cards("J6B")
        result = self.click(self.views.editor(), "保存 / 另存为",
                            {"field_input_paths": ["数据"], "field_vehicle": "E5"})
        self.assertIn("已保存预设", json.dumps(result[0], ensure_ascii=False))
        self.assertEqual(presets.load_preset("j6b", "daily")["input_paths"], [str(self.inputs.resolve())])
        with self.assertRaises(ValueError):
            self.click(self.views.editor(), "高级参数", {"field_input_paths": ["数据", "forged"]})
        self.cards("MMT")
        with self.assertRaisesRegex(ValueError, "流水线"):
            self.click(self.views.editor(), "预览运行",
                       {"field_enable_can_pipeline": "关闭", "field_enable_topic_pipeline": "关闭"})
        self.manager.submit_mmt.assert_not_called()

    def test_job_buttons_cancel_results_logs_and_no_client_job_id(self):
        self.cards()
        bundle = self.outputs / "result.zip"
        bundle.write_bytes(b"fixture")
        job = Job("a" * 32, "mmt", "test", [], self.root, status="运行中",
                  bundle_path=bundle, log_lines=["example log"])
        self.manager.jobs.return_value = [job]
        view = self.click(self.views.tasks(), "查看所选任务")
        view = self.click(view, "查看日志")
        self.assertIn("example log", json.dumps(view[0], ensure_ascii=False))
        view = self.click(view, "获取结果 ZIP")
        self.resend.assert_called_once_with(job.id)
        confirm = self.click(view, "取消任务")
        self.manager.cancel.assert_not_called()
        self.click(confirm, "确认取消")
        self.manager.cancel.assert_called_once_with(job.id)
        with self.assertRaises(ValueError):
            self.click(self.views.tasks(), "查看所选任务", {"job_id": "another"})

    def test_discard_clears_pending_and_invalidates_confirm(self):
        self.cards()
        confirm = self.click(self.views.editor(), "预览运行")
        self.assertIsNotNone(self.controller.pending)
        self.click(confirm, "返回修改 / 放弃")
        self.assertIsNone(self.controller.pending)
        with self.assertRaises(ValueError):
            self.click(confirm, "确认运行")
        self.manager.submit_mmt.assert_not_called()

    def test_card_run_uses_real_queue_and_bundle_for_both_platforms(self):
        from UnifiedApp.tests.test_report_bundle import MINING_SCRIPT

        self.settings.j6b_python = self.settings.mmt_python = sys.executable
        for kind, engine in (("J6B", "J6B_UnifiedMining"), ("MMT", "MMT_UnifiedMining")):
            (self.root / engine / "DataMining.py").write_text(MINING_SCRIPT, encoding="utf-8")
            self.manager = JobManager(max_concurrent=1)
            self.controller = self.new_controller()
            self.cards(kind)
            if kind == "J6B":
                self.controller.handle("修改 延迟检查=关闭")
            confirm = self.click(self.views.editor(), "预览运行")
            self.assertFalse(self.manager.jobs())
            with patch.dict(os.environ, {"UNIFIED_MINING_DISABLE_PUSH": "1"}):
                task = self.click(confirm, "确认运行")
                loop = QEventLoop()
                poll = QTimer()
                poll.timeout.connect(lambda: loop.quit() if self.manager.jobs()[0].bundle_path else None)
                poll.start(20)
                QTimer.singleShot(10000, loop.quit)
                loop.exec()
                poll.stop()
            job = self.manager.jobs()[0]
            if job.process is not None and job.status == "运行中":
                self.manager.cancel(job.id)
                job.process.waitForFinished(3000)
            self.assertEqual(job.status, "成功", job.log_lines)
            self.assertIsNotNone(job.bundle_path, job.log_lines)
            self.assertIn("任务详情", task[0]["header"]["title"]["content"])
            with ZipFile(job.bundle_path) as archive:
                self.assertEqual(set(archive.namelist()), {"summary.xlsx", "bad_case_report.xlsx"})
            with self.assertRaises(ValueError):
                self.click(confirm, "确认运行")
            self.assertEqual(len(self.manager.jobs()), 1)

    def bridge(self):
        bridge = FeishuBridge(JobManager(), path=self.config_path)
        bridge.db = sqlite3.connect(":memory:")
        bridge.db.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, received REAL)")
        bridge.send_card = Mock()
        bridge.send_text = Mock()
        self.addCleanup(bridge.stop)
        bridge.remote = self.controller
        bridge.cards = RemoteCards(self.controller)
        return bridge

    def event(self, **changes):
        return dict({"type": "card.action.trigger", "timestamp": str(int(time.time() * 1000)),
                     "operator_id": "ou_owner", "chat_id": "oc_chat", "message_id": "om_card",
                     "event_id": "ev_card", "action_tag": "button", "action_name": "button",
                     "form_value": "{}"}, **changes)

    def test_normalized_cli_and_raw_callback(self):
        flat = self.event()
        self.assertEqual(normalize_card_event({"data": flat}), flat)
        raw = {"header": {"event_id": "ev_card", "create_time": flat["timestamp"]},
               "event": {"operator": {"open_id": "ou_owner"},
                         "context": {"open_chat_id": "oc_chat", "open_message_id": "om_card"},
                         "action": {"tag": "button", "name": "button", "value": {"key": "value"},
                                    "form_value": {"vehicle": "E2"}}}}
        normalized = normalize_card_event(raw)
        for key in ("event_id", "timestamp", "operator_id", "chat_id", "message_id", "action_tag", "action_name"):
            self.assertEqual(normalized[key], flat[key])
        self.assertEqual(normalized["form_value"], {"vehicle": "E2"})

    def test_callback_authorization_event_not_message_dedup_and_audit(self):
        bridge = self.bridge()
        bridge.cards = Mock()
        bridge.cards.handle.return_value = ({"schema": "2.0"}, "token")
        for changes in ({"operator_id": "foreign"}, {"chat_id": "foreign"}, {"timestamp": "bad"},
                        {"timestamp": "0"}, {"message_id": ""}, {"event_id": ""}):
            bridge.handle_card_event(self.event(**changes))
        bridge.cards.handle.assert_not_called()
        event = self.event()
        bridge.handle_card_event(event)
        bridge.handle_card_event(event)
        bridge.handle_card_event(self.event(event_id="ev_second"))
        self.assertEqual(bridge.cards.handle.call_count, 2)
        self.assertEqual(bridge.send_card.call_count, 2)
        rows = bridge.db.execute("SELECT action,outcome FROM remote_actions").fetchall()
        self.assertEqual(rows, [("card", "accepted"), ("card", "accepted")])

    def test_callback_duplicate_is_persistent_across_restart(self):
        db_path = self.root / "messages.sqlite3"
        for index in range(2):
            bridge = self.bridge()
            bridge.db.close()
            bridge.db = sqlite3.connect(db_path)
            bridge.db.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, received REAL)")
            bridge.cards = Mock()
            bridge.cards.handle.return_value = ({"schema": "2.0"}, "token")
            bridge.handle_card_event(self.event())
            self.assertEqual(bridge.cards.handle.call_count, 1 if index == 0 else 0)
            bridge.stop()

    def test_callback_error_not_success_and_recovery_view(self):
        self.create_draft()
        bridge = self.bridge()
        bridge.handle_card_event(self.event(action_name="forged"))
        self.assertIn("未完成", bridge.send_text.call_args.args[0])
        bridge.send_card.assert_called_once()
        self.assertEqual(bridge.db.execute("SELECT outcome FROM remote_actions").fetchone()[0], "failed")

    def test_listener_uses_two_event_keys_and_stops_both(self):
        bridge = self.bridge()
        process = Mock()
        process.waitForFinished.return_value = True
        with patch.object(bridge, "_process", return_value=process) as make:
            bridge._listen()
            bridge._listen_cards()
        keys = [call.args[0][2] for call in make.call_args_list]
        self.assertEqual(keys, ["im.message.receive_v1", "card.action.trigger"])
        bridge.stop()
        self.assertEqual(process.terminate.call_count, 2)
        self.assertEqual(process.waitForFinished.call_count, 2)
        self.assertIsNone(bridge.listener)
        self.assertIsNone(bridge.card_listener)

    def test_card_sender_uses_stdin_activates_only_on_success(self):
        fake = self.root / "fake_cli.py"
        fake.write_text(
            "import sys,json,pathlib\n"
            "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\n"
            "pathlib.Path('card.json').write_text(sys.stdin.read(),encoding='utf-8')\n"
            "print(json.dumps({'ok':True,'data':{'message_id':'om_sent'}}))\n", encoding="utf-8")
        bridge = self.bridge()
        bridge.send_card = FeishuBridge.send_card.__get__(bridge)
        bridge.prefix = [sys.executable, str(fake), "--profile", "datamining-bot"]
        card, token = bridge.cards.home()
        self.assertIsNone(bridge.cards.sessions[token]["message_id"])
        original = bridge._process
        with patch.object(bridge, "_process") as make:

            def process(args, cwd=None):
                return original(args, self.root)

            make.side_effect = process
            bridge.send_card(card, token)
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if bridge.sender is None else None)
            poll.start(20)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            poll.stop()
        self.assertIsNone(bridge.sender)
        args = json.loads((self.root / "argv.json").read_text(encoding="utf-8"))
        self.assertIn("interactive", args)
        self.assertEqual(args[args.index("--content") + 1], "-")
        self.assertEqual(args[args.index("--user-id") + 1], "ou_owner")
        self.assertEqual(args[args.index("--profile") + 1], "datamining-bot")
        self.assertIn("--idempotency-key", args)
        self.assertEqual(json.loads((self.root / "card.json").read_text(encoding="utf-8")), card)
        self.assertEqual(bridge.cards.sessions[token]["message_id"], "om_sent")

    def test_send_missing_message_id_not_activated_or_reported_success(self):
        bridge = self.bridge()
        card, token = bridge.cards.home()
        bridge.sender = Mock()
        bridge.sender.readAllStandardOutput.return_value = b""
        bridge.sender_output = b'{"ok":true,"data":{}}'
        bridge.active_send = {"args": [], "key": "test", "attempt": 2, "card_token": token}
        logs = []
        bridge.log.connect(logs.append)
        bridge._send_next = Mock()
        bridge._send_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertIsNone(bridge.cards.sessions[token]["message_id"])
        self.assertFalse(any("推送成功" in log for log in logs))
        bridge.send_text.assert_called_once()

    def test_confirmation_five_minute_expiry_and_changed_template(self):
        self.cards()
        confirm = self.click(self.views.editor(), "预览运行")
        self.assertAlmostEqual(self.views.sessions[confirm[1]]["expires"] - time.time(), 300, delta=2)
        self.views.sessions[confirm[1]]["expires"] = time.time() - 1
        with self.assertRaisesRegex(ValueError, "过期"):
            self.click(confirm, "确认运行")
        confirm = self.click(self.views.editor(), "预览运行")
        (self.root / "MMT_UnifiedMining" / "config.json").write_text(
            '{"pipelines":[{"type":"can"}],"signals":{"changed":"mapping"}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "变更"):
            self.click(confirm, "确认运行")
        self.manager.submit_mmt.assert_not_called()

    def test_unchanged_paging_invalidates_other_editors(self):
        self.cards()
        self.click(self.views.editor(), "高级参数")
        older = self.views.editor()
        self.click(self.views.editor(), "高级参数")
        with self.assertRaisesRegex(ValueError, "过期"):
            self.click(older, "保存 / 另存为", {"field_vehicle": "STALE"})
        self.assertEqual(self.controller.draft["data"]["vehicle"], "E2LB")

    def test_logs_do_not_present_an_unrelated_pending_run_confirmation(self):
        self.cards()
        job = Job("a" * 32, "mmt", "test", [], self.root, status="运行中", log_lines=["latest log"])
        self.manager.jobs.return_value = [job]
        self.controller.handle("运行草稿")
        pending = deepcopy(self.controller.pending)
        view = self.click(self.views.task(job.id), "查看日志")
        self.assertEqual(view[0]["header"]["title"]["content"], "任务详情")
        self.assertEqual(self.controller.pending, pending)
        self.manager.submit_mmt.assert_not_called()

    def test_malformed_callback_is_rejected_without_dispatch(self):
        bridge = self.bridge()
        bridge.cards = Mock()
        bridge.handle_card_event({"header": "invalid", "event": {}})
        bridge.handle_card_event({"event": {"operator": None, "context": {}, "action": {}}})
        bridge.handle_card_event(self.event(event_id={"id": "invalid"}))
        bridge.handle_card_event(self.event(type="unknown"))
        bridge.cards.handle.assert_not_called()

    def test_live_microsecond_timestamp_and_seconds_milliseconds_keep_expiry_guard(self):
        bridge = self.bridge()
        bridge.cards = Mock()
        bridge.cards.handle.return_value = ({"schema": "2.0"}, "token")
        now = time.time()
        for scale in (1, 1000, 1000000):
            with self.subTest(scale=scale):
                bridge.handle_card_event(self.event(
                    event_id=f"fresh_{scale}", timestamp=str(int(now * scale))))
                accepted = bridge.cards.handle.call_count
                bridge.handle_card_event(self.event(
                    event_id=f"expired_{scale}", timestamp=str(int((now - 700) * scale))))
                bridge.handle_card_event(self.event(
                    event_id=f"future_{scale}", timestamp=str(int((now + 120) * scale))))
                self.assertEqual(bridge.cards.handle.call_count, accepted)
        self.assertEqual(bridge.cards.handle.call_count, 3)
        for timestamp in ("nan", "inf", "1793948383.1", "1793948383000000000", True, None):
            bridge.handle_card_event(self.event(timestamp=timestamp))
        self.assertEqual(bridge.cards.handle.call_count, 3)

    def test_normal_shutdown_does_not_report_expected_process_crash(self):
        bridge = self.bridge()
        logs = []
        bridge.log.connect(logs.append)
        bridge.stop()
        bridge._listen_error(QProcess.ProcessError.Crashed)
        bridge._cards_error(QProcess.ProcessError.Crashed)
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
