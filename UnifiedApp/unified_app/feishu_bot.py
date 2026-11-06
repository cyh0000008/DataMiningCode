"""Feishu app-bot bridge. Credentials stay in lark-cli's local credential store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, QLockFile, Signal

from .job_manager import JobManager
from .paths import user_data_dir
from .feishu_outbox import claim_delivery, finish_delivery, enqueue_bundle
from .remote_control import RemoteController, HELP
from .remote_cards import RemoteCards, card_event_time, normalize_card_event


PROFILE = "datamining-bot"


def cli_command() -> list[str]:
    """Use argv, never shell=True (including the Windows npm launcher)."""
    override = os.environ.get("MINING_LARK_CLI", "")
    if override:
        return [override]
    native = shutil.which("lark-cli.exe") if os.name == "nt" else shutil.which("lark-cli")
    if native:
        return [native]
    wrapper = shutil.which("lark-cli.cmd") or shutil.which("lark-cli.ps1")
    if wrapper:
        script = Path(wrapper).parent / "node_modules/@larksuite/cli/scripts/run.js"
        node = shutil.which("node")
        if node and script.is_file():
            return [node, str(script)]
    raise FileNotFoundError("未找到 lark-cli，请先安装并配置 datamining-bot 应用。")


def config_path() -> Path:
    return user_data_dir() / "feishu_bot.json"


def load_bot_config(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("feishu_bot.json 必须是 JSON 对象")
    return data


def save_bot_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def normalize_event(envelope: dict) -> dict:
    """Accept flat CLI events and raw/enveloped IM events without changing sender IDs."""
    event = envelope
    for _ in range(4):
        if "chat_id" in event or "message" in event:
            break
        child = next((event[k] for k in ("data", "event", "payload")
                      if isinstance(event.get(k), dict)), None)
        if child is None:
            break
        event = child
    if isinstance(event.get("message"), dict):
        message = event["message"]
        sender = event.get("sender", {})
        event = {**message, "sender_type": sender.get("sender_type", ""),
                 "sender_id": sender.get("sender_id", {}).get("open_id", "")}
    else:
        event = dict(event)
    content = event.get("content", "")
    if isinstance(content, str) and event.get("message_type") == "text":
        try:
            decoded = json.loads(content)
            if isinstance(decoded, dict) and isinstance(decoded.get("text"), str):
                event["content"] = decoded["text"]
        except ValueError:
            pass
    return event


def authorized_event(event: dict, config: dict, now: float | None = None) -> bool:
    if event.get("chat_type") != "p2p" or event.get("sender_type") != "user":
        return False
    if event.get("message_type") != "text" or not event.get("message_id"):
        return False
    try:
        created = float(event["create_time"]) / 1000
    except (KeyError, TypeError, ValueError):
        return False
    age = (time.time() if now is None else now) - created
    return (-60 <= age <= 600
            and event.get("sender_id") == config.get("owner_open_id")
            and event.get("chat_id") == config.get("chat_id"))


class FeishuBridge(QObject):
    log = Signal(str)
    presets_changed = Signal(str)

    def __init__(self, manager: JobManager, parent=None, *, path: Path | None = None,
                 bind_code: str = "") -> None:
        super().__init__(parent)
        self.manager = manager
        self.path = path or config_path()
        self.config = load_bot_config(self.path)
        self.bind_code = bind_code
        self.listener = None
        self.card_listener = None
        self.card_buffer = b""
        self.card_diagnostics = b""
        self.card_ready = False
        self.sender = None
        self.stopped = False
        self.queue = deque()
        self.active_send = None
        self.prefix = []
        self.listener_buffer = b""
        self.listener_diagnostics = b""
        self.listener_ready = False
        self.sender_output = b""
        self.lock = QLockFile(str(self.path.with_suffix(".lock")))
        self.lock.setStaleLockTime(0)
        self.db = None
        self.remote = None
        self.cards = None
        self.progress_sent = {}
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._send_timeout)
        self.outbox_timer = QTimer(self)
        self.outbox_timer.setInterval(3000)
        self.outbox_timer.timeout.connect(self._poll_outbox)

    def start(self) -> bool:
        if not self.bind_code and not self.config.get("enabled"):
            return False
        if not self.bind_code and not all(self.config.get(k) for k in ("owner_open_id", "chat_id")):
            raise ValueError("飞书尚未绑定，请运行 python UnifiedApp/feishu_agent.py --bind")
        self.prefix = cli_command() + ["--profile", self.config.get("profile", PROFILE)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.lock.tryLock(0):
            raise RuntimeError("另一个进程已运行飞书助手，请勿同时启动界面与后台助手。")
        self.db = sqlite3.connect(str(self.path.with_suffix(".sqlite3")))
        self.db.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, received REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS remote_actions "
                        "(message_id TEXT PRIMARY KEY, action TEXT, outcome TEXT, recorded REAL)")
        self.db.commit()
        self.manager.job_finished.connect(self._job_finished)
        self.manager.job_updated.connect(self._job_updated)
        self.manager.job_added.connect(self._job_updated)
        self.outbox_timer.start()
        self._listen()
        self._listen_cards()
        return True

    def _process(self, args: list[str], cwd: Path | None = None) -> QProcess:
        p = QProcess(self)
        p.setProgram(self.prefix[0])
        p.setArguments(self.prefix[1:] + args)
        if cwd is not None:
            p.setWorkingDirectory(str(cwd))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        return p

    def _listen(self) -> None:
        if self.stopped:
            return
        self.listener_buffer = b""
        self.listener_diagnostics = b""
        self.listener_ready = False
        p = self._process(["event", "consume", "im.message.receive_v1", "--as", "bot", "--timeout", "10m"])
        self.listener = p
        p.readyReadStandardOutput.connect(self._read_events)
        p.readyReadStandardError.connect(self._read_listener_errors)
        p.errorOccurred.connect(self._listen_error)
        p.finished.connect(self._listen_finished)
        p.start()

    def _read_listener_errors(self) -> None:
        if self.listener is None:
            return
        data = bytes(self.listener.readAllStandardError())
        self.listener_diagnostics = (self.listener_diagnostics + data)[-6000:]
        if not self.listener_ready and b"[event] ready" in self.listener_diagnostics:
            self.listener_ready = True
            self.log.emit("[飞书] 消息监听已就绪。")

    def _listen_error(self, error) -> None:
        if self.stopped:
            return
        self.log.emit("[飞书] 监听进程错误：" + str(error))
        if error == QProcess.ProcessError.FailedToStart and self.listener is not None:
            self._listen_finished(-1, QProcess.ExitStatus.CrashExit)

    def _listen_finished(self, code: int, _status) -> None:
        if self.listener is None:
            return
        self._read_events()
        self._read_listener_errors()
        if code:
            self.log.emit("[飞书] 监听失败，请检查应用权限、事件订阅及 lark-cli 日志。")
        self.listener.deleteLater()
        self.listener = None
        if not self.stopped:
            QTimer.singleShot(10000 if code else 1000, self._listen)

    def _read_events(self) -> None:
        if self.listener is None:
            return
        self.listener_buffer += bytes(self.listener.readAllStandardOutput())
        while b"\n" in self.listener_buffer:
            line, self.listener_buffer = self.listener_buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                self.handle_event(event)

    def _listen_cards(self) -> None:
        if self.stopped:
            return
        self.card_buffer = self.card_diagnostics = b""
        self.card_ready = False
        process = self._process(["event", "consume", "card.action.trigger", "--as", "bot", "--timeout", "10m"])
        self.card_listener = process
        process.readyReadStandardOutput.connect(self._read_cards)
        process.readyReadStandardError.connect(self._read_card_errors)
        process.finished.connect(self._cards_finished)
        process.errorOccurred.connect(self._cards_error)
        process.start()

    def _read_card_errors(self) -> None:
        if self.card_listener is None:
            return
        self.card_diagnostics = (self.card_diagnostics + bytes(self.card_listener.readAllStandardError()))[-6000:]
        if not self.card_ready and b"[event] ready" in self.card_diagnostics:
            self.card_ready = True
            self.log.emit("[飞书] 卡片监听已就绪；请确认应用后台已开启 card.action.trigger 回调。")

    def _cards_error(self, error) -> None:
        if self.stopped:
            return
        self.log.emit("[飞书] 卡片监听错误：" + str(error))
        if error == QProcess.ProcessError.FailedToStart:
            self._cards_finished(-1, QProcess.ExitStatus.CrashExit)

    def _cards_finished(self, code: int, _status) -> None:
        if self.card_listener is None:
            return
        self._read_cards()
        self._read_card_errors()
        self.card_listener.deleteLater()
        self.card_listener = None
        self.card_ready = False
        if code and not self.stopped:
            self.log.emit("[飞书] 卡片监听失败，请检查回调配置及 im:message:readonly 权限；文字指令仍可用。")
        if not self.stopped:
            QTimer.singleShot(10000 if code else 1000, self._listen_cards)

    def _read_cards(self) -> None:
        if self.card_listener is None:
            return
        self.card_buffer += bytes(self.card_listener.readAllStandardOutput())
        while b"\n" in self.card_buffer:
            line, self.card_buffer = self.card_buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self.log.emit("[飞书] 卡片回调数据无法解析，已拒绝。")
                continue
            if isinstance(event, dict):
                self.handle_card_event(event)

    def _ensure_remote(self) -> None:
        identity = [self.config.get("owner_open_id"), self.config.get("chat_id")]
        if self.remote is None or self.remote.identity != identity:
            self.remote = RemoteController(
                self.manager, self.path, self.presets_changed.emit,
                lambda job_id: self._bundle_ready(job_id, resend=True),
            )
            self.cards = RemoteCards(self.remote)
        elif self.cards is None:
            self.cards = RemoteCards(self.remote)

    def handle_card_event(self, envelope: dict) -> None:
        if self.stopped or self.bind_code or not self._binding_current():
            return
        try:
            event = normalize_card_event(envelope)
        except ValueError as exc:
            self.log.emit("[飞书] 卡片回调格式无效，已拒绝：" + str(exc))
            return
        if event.get("type") != "card.action.trigger":
            self.log.emit("[飞书] 拒绝非卡片回调事件。")
            return
        if event.get("operator_id") != self.config.get("owner_open_id") or event.get("chat_id") != self.config.get("chat_id"):
            self.log.emit("[飞书] 拒绝非绑定账号或非绑定私聊的卡片操作。")
            return
        try:
            age = time.time() - card_event_time(event.get("timestamp"))
        except (TypeError, ValueError):
            self.log.emit("[飞书] 卡片回调缺少有效时间戳，已拒绝。")
            return
        if not -60 <= age <= 600 or not all(isinstance(event.get(key), str) and event[key]
                                          for key in ("event_id", "message_id")):
            self.log.emit("[飞书] 卡片回调过期或缺少标识，已拒绝。")
            return
        key = "card:" + event["event_id"]
        cursor = self.db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?)", (key, time.time()))
        self.db.commit()
        if cursor.rowcount == 0:
            return
        outcome = "accepted"
        try:
            self._ensure_remote()
            card, token = self.cards.handle(event)
            self.send_card(card, token)
        except Exception as exc:
            outcome = "failed"
            self.send_text("卡片操作未完成：" + str(exc) + "\n发送“操作台”重新打开；文字指令仍可使用。")
            if self.cards is not None:
                try:
                    card, token = self.cards.editor() if self.remote.draft else self.cards.home()
                    self.send_card(card, token)
                except (OSError, ValueError) as recovery_error:
                    self.log.emit("[飞书] 卡片恢复失败：" + str(recovery_error))
        self.db.execute("CREATE TABLE IF NOT EXISTS remote_actions "
                        "(message_id TEXT PRIMARY KEY, action TEXT, outcome TEXT, recorded REAL)")
        self.db.execute("INSERT OR IGNORE INTO remote_actions VALUES (?, ?, ?, ?)",
                        (key, "card", outcome, time.time()))
        self.db.commit()
        self.log.emit("[飞书远程] card：" + outcome)

    def send_card(self, card: dict, token: str) -> None:
        self.queue.append({"args": ["--msg-type", "interactive", "--content", "-"],
                           "input": json.dumps(card, ensure_ascii=False).encode("utf-8"),
                           "key": secrets.token_hex(20), "attempt": 0, "card_token": token})
        self._send_next()

    def handle_event(self, event: dict) -> None:
        if self.stopped:
            return
        original_keys = list(event)
        event = normalize_event(event)
        if self.bind_code:
            text = event.get("content", "").strip()
            code = re.fullmatch(r"绑定挖掘助手\s*(\S+)", text)
            if (event.get("chat_type") != "p2p" or event.get("sender_type") != "user"
                    or event.get("message_type") != "text"
                    or code is None
                    or not secrets.compare_digest(code[1].encode("utf-8"), self.bind_code.encode("utf-8"))
                    or not str(event.get("sender_id", "")).startswith("ou_")
                    or not str(event.get("chat_id", "")).startswith("oc_")):
                self.log.emit("[飞书] 绑定消息未匹配：" + json.dumps({
                    "fields": original_keys, "normalized_fields": list(event),
                    "chat_type": event.get("chat_type"), "sender_type": event.get("sender_type"),
                    "message_type": event.get("message_type"), "binding_text": code is not None,
                }, ensure_ascii=False))
                return
            self.config = {**load_bot_config(self.path), "enabled": True, "profile": self.config.get("profile", PROFILE),
                           "owner_open_id": event["sender_id"], "chat_id": event["chat_id"]}
            self.remote = None
            self.cards = None
            save_bot_config(self.path, self.config)
            self.bind_code = ""
            self.log.emit("[飞书] 绑定成功，接收人配置已保存在本机。")
            self.send_text("绑定成功。结果 ZIP 将推送到此私聊。\n" + HELP)
            return
        if not self._binding_current() or not authorized_event(event, self.config):
            return
        # Reserve before executing: redelivery/restart cannot submit a duplicate task.
        cursor = self.db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?)",
                                 (event["message_id"], time.time()))
        self.db.commit()
        if cursor.rowcount == 0:
            return
        text = event.get("content", "").strip()
        action = text.split(maxsplit=1)[0] if text else ""
        if action not in {
            "帮助", "help", "预设", "查看预设", "编辑预设", "新建预设", "查看草稿",
            "修改", "修改草稿", "参数", "路径", "保存预设", "另存预设", "覆盖预设",
            "删除预设", "运行", "运行草稿", "任务", "状态", "取消", "结果", "日志",
            "确认运行", "确认覆盖", "确认删除", "确认取消", "放弃确认", "清空草稿", "操作台", "菜单",
        }:
            action = "unknown"
        outcome = "accepted"
        try:
            self._ensure_remote()
            if text in ("操作台", "菜单"):
                card, token = self.cards.home()
                self.send_card(card, token)
                reply = None
            else:
                reply = self.remote.handle(text)
        except Exception as exc:
            outcome = "failed"
            reply = "未能执行：" + str(exc)
        self.db.execute("CREATE TABLE IF NOT EXISTS remote_actions "
                        "(message_id TEXT PRIMARY KEY, action TEXT, outcome TEXT, recorded REAL)")
        self.db.execute("INSERT OR IGNORE INTO remote_actions VALUES (?, ?, ?, ?)",
                        (event["message_id"], action, outcome, time.time()))
        self.db.commit()
        self.log.emit(f"[飞书远程] {action}：{outcome}")
        if reply is not None:
            self.send_text(reply)

    def _job_updated(self, job_id: str) -> None:
        if self.stopped or self.bind_code or not self.config.get("enabled"):
            return
        job = self.manager.get_job(job_id)
        if job is None or job.status not in ("排队中", "运行中"):
            return
        bucket = job.percent // 20
        now = time.time()
        previous = self.progress_sent.get(job_id)
        if previous and previous[0] == job.status:
            if bucket <= previous[1] or now - previous[2] < 60:
                return
        self.progress_sent[job_id] = (job.status, bucket, now)
        self.send_text(f"任务 {job.id[:8]} {job.title}：{job.status} {job.percent}%\n{job.progress_text}")

    def _job_finished(self, job_id: str) -> None:
        if self.stopped or self.bind_code or not self.config.get("enabled"):
            return
        job = self.manager.get_job(job_id)
        if job:
            detail = "报告与打包流程将继续，结果就绪后自动发送。" if (
                job.kind in ("j6b", "mmt") and job.status == "成功" and job.bundle_options is not None
            ) else "可发送“日志 " + job.id[:8] + "”查看摘要。"
            self.send_text(f"任务 {job.id[:8]} {job.title}：{job.status}。\n{detail}")

    def _bundle_ready(self, job_id: str, resend: bool = False) -> None:
        job = self.manager.get_job(job_id)
        if job is None or job.bundle_path is None:
            return
        bundle = job.bundle_path.resolve()
        if resend:
            if not enqueue_bundle(bundle, job.title, job.id, path=self.path, resend=True):
                raise ValueError("机器人已禁用或绑定失效，未加入发送队列")
            self._poll_outbox()
            return
        key = hashlib.sha256((job.id + str(bundle)).encode()).hexdigest()[:40]
        self.queue.append({"args": ["--file", "./" + bundle.name], "cwd": bundle.parent,
                           "key": key, "attempt": 0, "job_id": job_id})
        self._send_next()

    def _poll_outbox(self) -> None:
        if self.stopped or self.bind_code or self.sender is not None or self.queue:
            return
        if not self._binding_current():
            return
        try:
            item = claim_delivery(self.path)
            if item is None:
                return
            bundle = Path(item["bundle"])
            self.queue.append({"args": ["--file", "./" + bundle.name], "cwd": bundle.parent,
                               "key": item["outbox_id"], "outbox_id": item["outbox_id"],
                               "attempt": 0, "job_id": item.get("job_id", "")})
            self.log.emit("[飞书] 正在发送结果包：" + bundle.name)
            self._send_next()
        except Exception as exc:
            self.log.emit("[飞书] 检查回传队列失败：" + str(exc))

    def send_text(self, text: str) -> None:
        for start in range(0, len(text), 3000):
            self.queue.append({"args": ["--text", "-"], "input": text[start:start + 3000].encode("utf-8"),
                               "key": secrets.token_hex(20), "attempt": 0})
        self._send_next()

    def _send_next(self) -> None:
        if self.stopped or self.sender is not None or not self.queue:
            return
        if not self._binding_current():
            return
        self.active_send = self.queue.popleft()
        item = self.active_send
        args = ["im", "+messages-send", "--as", "bot", "--user-id", self.config["owner_open_id"],
                "--idempotency-key", item["key"], *item["args"]]
        p = self._process(args, item.get("cwd"))
        self.sender = p
        self.sender_output = b""
        p.readyReadStandardOutput.connect(self._read_send)
        p.readyReadStandardError.connect(lambda: p.readAllStandardError())
        p.started.connect(lambda: (p.write(item.get("input", b"")), p.closeWriteChannel()))
        p.finished.connect(self._send_finished)
        p.errorOccurred.connect(lambda error: self._send_failed_to_start(error))
        p.start()
        self.timeout.start(180000)

    def _binding_current(self) -> bool:
        try:
            current = load_bot_config(self.path)
        except (OSError, ValueError) as exc:
            self.log.emit("[飞书] 配置读取失败，停止远程操作：" + str(exc))
            self.stop()
            return False
        identity = ("owner_open_id", "chat_id")
        if not current.get("enabled") or any(current.get(k) != self.config.get(k) for k in identity):
            self.log.emit("[飞书] 绑定或启用状态已变更，助手停止；重新打开应用后使用新配置。")
            self.stop()
            return False
        self.config = current
        return True

    def _read_send(self) -> None:
        self.sender_output += bytes(self.sender.readAllStandardOutput())

    def _send_timeout(self) -> None:
        if self.sender:
            self.sender.kill()

    def _send_failed_to_start(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._send_finished(-1, QProcess.ExitStatus.CrashExit)

    def _send_finished(self, code: int, _status) -> None:
        if self.sender is None:
            return
        self.timeout.stop()
        self._read_send()
        try:
            response = json.loads(self.sender_output)
            ok = code == 0 and response.get("ok") is True
        except (ValueError, AttributeError):
            ok = False
        self.sender.deleteLater()
        self.sender = None
        item = self.active_send
        self.active_send = None
        if ok and item.get("card_token"):
            data = response.get("data")
            message_id = data.get("message_id") if isinstance(data, dict) else None
            if not message_id and isinstance(data, dict) and isinstance(data.get("message"), dict):
                message_id = data["message"].get("message_id")
            if not isinstance(message_id, str) or not message_id:
                ok = False
                self._delivery_log(item, "卡片发送结果缺少 message_id，无法启用按钮")
        if ok:
            if item.get("card_token"):
                if self.cards is not None:
                    self.cards.sent(item["card_token"], message_id)
            if item.get("outbox_id"):
                finish_delivery(self.path, item["outbox_id"], True)
            self._delivery_log(item, "推送成功")
        elif item["attempt"] < 2 and not self.stopped:
            item["attempt"] += 1
            self.queue.appendleft(item)
            self._delivery_log(item, "推送失败，稍后重试")
            QTimer.singleShot(5000 * item["attempt"], self._send_next)
            return
        else:
            if item.get("outbox_id"):
                finish_delivery(self.path, item["outbox_id"], False)
            if item.get("card_token"):
                self._delivery_log(item, "卡片发送失败；请检查机器人发送权限及网络，文字指令仍可用")
                self.send_text("操作台卡片发送失败，请稍后发送“操作台”重试，或发送“帮助”使用文字指令。")
            else:
                self._delivery_log(item, "推送失败，本地 ZIP 保留；检查机器人发送/文件权限及网络")
        self._send_next()

    def _delivery_log(self, item: dict, text: str) -> None:
        message = "[飞书] " + text
        self.log.emit(message)
        job = self.manager.get_job(item.get("job_id", ""))
        if job:
            job.log_lines.append(message)
            self.manager.job_log.emit(job.id, message)
            self.manager.job_updated.emit(job.id)

    def stop(self) -> None:
        self.stopped = True
        self.timeout.stop()
        self.outbox_timer.stop()
        for attribute in ("listener", "card_listener", "sender"):
            process = getattr(self, attribute)
            if process is not None:
                process.terminate()
                if not process.waitForFinished(3000):
                    process.kill()
                    process.waitForFinished(1000)
                if getattr(self, attribute) is process:
                    process.deleteLater()
                    setattr(self, attribute, None)
        if self.db:
            self.db.close()
            self.db = None
        self.lock.unlock()
