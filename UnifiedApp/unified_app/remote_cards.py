"""Card 2.0 views with server-owned, expiring actions over RemoteController."""

from __future__ import annotations

from datetime import datetime
import html
import json
from pathlib import Path
import re
import secrets
import socket
import time

from . import presets
from .remote_control import FIELDS, RemoteController, _digest


BROWSE_FIELDS = {
    "input_paths": ("输入目录", "input", "basic"),
    "source_path": ("输入目录", "input", "basic"),
    "output_dir": ("输出目录", "output", "basic"),
    "compare_base_path": ("基线报告", "report", "advanced"),
    "raw_report_archive_dir": ("原始报告保存目录", "output", "advanced"),
}
PAGE_SIZE = 8


def plain(text: str) -> dict:
    return {"tag": "plain_text", "content": str(text)}


def markdown(text: str) -> dict:
    return {"tag": "markdown", "content": text}


def escaped(text: object) -> str:
    result = html.escape(str(text), quote=False)
    for char in "*~[]()#_":
        result = result.replace(char, f"&#{ord(char)};")
    return result


def columns(elements: list[dict]) -> dict:
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "medium",
            "columns": [{"tag": "column", "width": "weighted", "weight": 1,
                         "elements": [element]} for element in elements]}


def card_event_time(timestamp: object) -> float:
    # Live card callbacks use microseconds despite the CLI's millisecond schema.
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{10}|[0-9]{13}|[0-9]{16}", timestamp):
        raise ValueError("卡片回调时间戳格式错误")
    return int(timestamp) / {10: 1, 13: 1000, 16: 1000000}[len(timestamp)]


def normalize_card_event(envelope: dict) -> dict:
    event = envelope
    header = envelope.get("header") or {}
    if not isinstance(header, dict):
        raise ValueError("卡片回调头格式错误")
    event_id = header.get("event_id")
    event_time = header.get("create_time")
    for _ in range(4):
        if "operator_id" in event or "operator" in event:
            break
        child = next((event[k] for k in ("data", "event", "payload") if isinstance(event.get(k), dict)), None)
        if child is None:
            break
        event = child
    if "operator" not in event:
        return dict(event)
    operator = event.get("operator", {})
    context = event.get("context", {})
    action = event.get("action", {})
    if not all(isinstance(item, dict) for item in (operator, context, action)):
        raise ValueError("卡片回调身份、上下文或操作格式错误")
    return {
        "type": "card.action.trigger", "operator_id": operator.get("open_id"),
        "chat_id": context.get("open_chat_id"), "message_id": context.get("open_message_id"),
        "event_id": event_id or event.get("event_id"),
        "timestamp": event_time or event.get("timestamp"),
        "action_name": action.get("name"), "action_tag": action.get("tag"),
        "action_value": action.get("value", {}), "form_value": action.get("form_value", {}),
    }


class RemoteCards:
    def __init__(self, remote: RemoteController) -> None:
        self.remote = remote
        self.sessions: dict[str, dict] = {}

    def _page(self, title: str, *, mutable: bool = False) -> tuple[dict, dict]:
        now = time.time()
        self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now}
        while len(self.sessions) >= 100:
            self.sessions.pop(next(iter(self.sessions)))
        token = secrets.token_hex(12)
        session = {"token": token, "expires": now + 1800, "message_id": None,
                   "actions": {}, "fields": {},
                   "draft_hash": _digest(self.remote.draft) if mutable else None}
        self.sessions[token] = session
        card = {"schema": "2.0",
                "config": {"update_multi": True, "enable_forward": False, "width_mode": "default"},
                "header": {"title": plain(title), "template": "blue",
                           "subtitle": plain("本机应用在线时可操作 · 文字指令仍可使用")},
                "body": {"direction": "vertical", "vertical_spacing": "medium", "elements": []}}
        return card, session

    def _button(self, session: dict, text: str, action: dict, *, form: bool = False,
                primary: bool = False, danger: bool = False) -> dict:
        name = f"mc_{session['token']}_{len(session['actions'])}"
        session["actions"][name] = action
        button = {"tag": "button", "name": name, "text": plain(text),
                  "type": ("danger_filled" if primary else "danger") if danger else "primary_filled" if primary else "default"}
        if form:
            button["form_action_type"] = "submit"
        else:
            button["behaviors"] = [{"type": "callback", "value": {"action_name": name}}]
        return button

    def _finish(self, card: dict, session: dict) -> tuple[dict, str]:
        if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > 28000:
            self.sessions.pop(session["token"])
            raise ValueError("卡片内容过大，请减少选项或使用文字指令")
        return card, session["token"]

    def sent(self, token: str, message_id: str) -> None:
        session = self.sessions.get(token)
        if session is not None:
            session["message_id"] = message_id

    def home(self) -> tuple[dict, str]:
        self.remote._config()
        card, session = self._page("数据挖掘操作台")
        machine = self.remote._remote().get("machine_name") or socket.gethostname()
        card["body"]["elements"] = [
            markdown(f"**选择平台，编辑预设或运行挖掘**\n运行电脑：{escaped(machine)}"),
            columns([self._button(session, "J6B 挖掘", {"op": "platform", "kind": "j6b"}, primary=True),
                     self._button(session, "MMT 挖掘", {"op": "platform", "kind": "mmt"})]),
            columns([self._button(session, "当前任务", {"op": "tasks"}),
                     self._button(session, "继续编辑草稿", {"op": "editor", "page": "basic"})]),
        ]
        return self._finish(card, session)

    def _input(self, session: dict, name: str, label: str, value: str, *, required: bool = False) -> list[dict]:
        session["fields"][name] = {"type": "text", "default": value}
        return [{"tag": "input", "name": name, "label": plain(label), "default_value": value,
                 "required": required, "max_length": 1000}]

    def _select(self, session: dict, name: str, label: str, options: list[tuple[str, str]],
                value: str | list[str], *, multi: bool = False) -> list[dict]:
        session["fields"][name] = {"type": "multi" if multi else "select",
                                   "default": value, "options": dict(options)}
        if not options:
            session["fields"].pop(name)
            return [markdown(f"{escaped(label)}：暂无选项，请先在本机配置。")]
        node = {"tag": "multi_select_static" if multi else "select_static", "name": name, "width": "fill",
                "placeholder": plain(label),
                "options": [{"text": plain(label), "value": key} for key, label in options]}
        if multi:
            node["selected_values"] = value
        elif value in dict(options):
            node["initial_option"] = value
        return [markdown(escaped(label)), node]

    def platform(self, kind: str) -> tuple[dict, str]:
        card, session = self._page(f"{kind.upper()} 预设管理")
        names = presets.list_presets(kind)
        form = {"tag": "form", "name": "preset_form", "elements": []}
        form["elements"] += self._select(session, "preset", "已有预设", [(n, n) for n in names],
                                          names[0] if names else "")
        form["elements"] += self._input(session, "new_name", "新预设名称（新建时填写）", "")
        form["elements"].append(columns([
            self._button(session, "编辑所选预设", {"op": "open", "kind": kind}, form=True, primary=True),
            self._button(session, "新建预设", {"op": "new", "kind": kind}, form=True),
        ]))
        form["elements"].append(self._button(session, "删除所选预设", {"op": "delete", "kind": kind},
                                               form=True, danger=True))
        card["body"]["elements"] = [
            markdown("**先选择已有预设，或填写名称新建**\n编辑仅产生草稿，不立即覆盖原预设。"),
            form, self._button(session, "返回操作台", {"op": "home"}),
        ]
        return self._finish(card, session)

    def _path_options(self, access: str, current: str | list[str]) -> list[tuple[str, str]]:
        aliases = self.remote._remote().get("path_aliases", {})
        options = [(name, name) for name, item in aliases.items() if item.get("access") == access]
        values = current if isinstance(current, list) else [current]
        for value in values:
            if value and value not in dict(options):
                options.append((value, "原预设：" + value))
        return options

    def editor(self, page: str = "basic", notice: str = "") -> tuple[dict, str]:
        draft = self.remote._need_draft()
        kind, data = draft["kind"], draft["data"]
        card, session = self._page(f"{kind.upper()} 参数编辑 · {'基本' if page == 'basic' else '高级'}", mutable=True)
        session["kind"] = kind
        form = {"tag": "form", "name": "edit_form", "elements": []}
        elements = form["elements"]
        if page == "basic":
            elements += self._input(session, "preset_name", "保存 / 另存为的预设名称",
                                    draft.get("card_save_name", draft["name"]), required=True)
            labels = ["车型", "报告软件版本", "动力形式", "输入目录", "输出目录", "自动对比"]
            date = data["date_range" if kind == "j6b" else "query_date"]
            dates = date.replace("~", "-").split("-") if date else ["", ""]
            if len(dates) == 1:
                dates *= 2
            elements.append(columns([markdown("开始日期"), markdown("结束日期")]))
            pickers = []
            for name, value in zip(("date_start", "date_end"), dates):
                session["fields"][name] = {"type": "date", "default": value}
                picker = {"tag": "date_picker", "name": name, "placeholder": plain("选择日期"), "width": "fill"}
                if value:
                    picker["initial_date"] = datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
                pickers.append(picker)
            elements.append(columns(pickers))
            if kind == "mmt":
                labels += ["数据软件筛选", "CAN流水线", "Topic流水线"]
        else:
            basic = {"车型", "报告软件版本", "动力形式", "输入目录", "输出目录", "自动对比",
                     "日期", "数据软件筛选", "软件筛选", "CAN流水线", "Topic流水线"}
            labels = [label for label in FIELDS[kind] if label not in basic]
        for label in labels:
            field = FIELDS[kind][label]
            value = data[field]
            name = "field_" + field
            if isinstance(value, bool):
                elements += self._select(session, name, label, [("开启", "开启"), ("关闭", "关闭")],
                                         "开启" if value else "关闭")
            elif field in ("powertrain_type", "metric_signal_scope", "resume_mode"):
                options = {"powertrain_type": ["ICE", "PHEV", "BEV"],
                           "metric_signal_scope": ["all", "debugcan_only", "pcan_ccan_only"],
                           "resume_mode": ["auto", "never"]}[field]
                elements += self._select(session, name, label, [(v, v) for v in options], value)
            elif field in ("input_paths", "source_path", "output_dir", "compare_base_path", "raw_report_archive_dir"):
                access = "input" if field in ("input_paths", "source_path") else "report" if field == "compare_base_path" else "output"
                options = self._path_options(access, value)
                if field != "input_paths":
                    options.insert(0, ("", "未设置 / 使用默认"))
                elements += self._select(session, name, label, options, value, multi=field == "input_paths")
                elements.append(self._button(session, "浏览" + label,
                                             {"op": "browse_start", "field": field, "page": page},
                                             form=True))
            else:
                elements += self._input(session, name, label, str(value))
        other = "advanced" if page == "basic" else "basic"
        elements.append(columns([
            self._button(session, "预览运行", {"op": "edit", "next": "run"}, form=True, primary=True),
            self._button(session, "保存 / 另存为", {"op": "edit", "next": "save"}, form=True),
            self._button(session, "高级参数" if other == "advanced" else "基本参数",
                         {"op": "edit", "next": other}, form=True),
        ]))
        session["preset_name"] = draft.get("card_save_name", draft["name"])
        card["body"]["elements"] = [
            markdown("**填写表单后点击操作按钮**\n" + escaped(notice or "切换页面会保存到草稿；不覆盖桌面未保存参数。")),
            form, self._button(session, "返回操作台（未提交的输入不会保存）", {"op": "home"}),
        ]
        return self._finish(card, session)

    def _browse_root(self, field: str, alias: str, expected: str | None = None) -> Path:
        access = BROWSE_FIELDS[field][1]
        item = self.remote._remote().get("path_aliases", {}).get(alias)
        if not isinstance(item, dict) or item.get("access") != access:
            raise ValueError("路径授权已移除或变更，请重新打开浏览器")
        root = Path(item["path"]).expanduser().resolve(strict=True)
        if expected is not None and str(root) != expected:
            raise ValueError("路径授权已变更，请重新打开浏览器")
        self.remote._path(str(root), access, file=root.is_file())
        if not (root.is_dir() or (access == "report" and root.is_file())):
            raise ValueError("授权根路径不是可浏览的目录或报告")
        return root

    def _browser_path(self, field: str, alias: str, root_text: str, path_text: str) -> tuple[Path, Path]:
        root = self._browse_root(field, alias, root_text)
        path = Path(path_text).resolve(strict=True)
        if not (path == root or (root.is_dir() and path.is_relative_to(root))):
            raise ValueError("路径超出授权根目录")
        self.remote._path(str(path), BROWSE_FIELDS[field][1],
                          file=field == "compare_base_path" and path.is_file())
        return root, path

    def browse_roots(self, field: str, page: int = 0) -> tuple[dict, str]:
        label, access, editor_page = BROWSE_FIELDS[field]
        aliases = self.remote._remote().get("path_aliases", {})
        names = sorted((name for name, item in aliases.items()
                        if isinstance(item, dict) and item.get("access") == access), key=str.casefold)
        count = max(1, (len(names) + PAGE_SIZE - 1) // PAGE_SIZE)
        if page < 0 or page >= count:
            raise ValueError("浏览页码无效")
        card, session = self._page("选择" + label + " · 授权目录", mutable=True)
        entries = []
        for name in names[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            root = self._browse_root(field, name)
            entries.append(self._button(session, name[:95], {
                "op": "browse_path", "field": field, "alias": name,
                "root": str(root), "path": str(root), "index": 0,
            }))
        controls = []
        if page:
            controls.append(self._button(session, "上一页", {"op": "browse_roots", "field": field, "index": page - 1}))
        if page + 1 < count:
            controls.append(self._button(session, "下一页", {"op": "browse_roots", "field": field, "index": page + 1}))
        controls.append(self._button(session, "返回参数", {"op": "editor", "page": editor_page}, primary=True))
        card["body"]["elements"] = [
            markdown(f"**请选择本机授权的{escaped(label)}根目录**\n"
                     + (f"第 {page + 1}/{count} 页" if names else "暂无授权，请在电脑的飞书远程路径设置中添加。")),
            {"tag": "column_set", "flex_mode": "none", "columns": [
                {"tag": "column", "width": "weighted", "weight": 1,
                 "elements": entries or [markdown("没有可用目录。")]}]},
            columns(controls),
        ]
        return self._finish(card, session)

    def browse_path(self, field: str, alias: str, root_text: str, path_text: str, index: int = 0) -> tuple[dict, str]:
        label, access, editor_page = BROWSE_FIELDS[field]
        root, path = self._browser_path(field, alias, root_text, path_text)
        if path.is_file():
            return self._select_browsed(field, alias, root_text, str(path))
        if not path.is_dir():
            raise ValueError("目录已不存在或无法访问")
        entries = []
        missing_entries = 0
        for child in path.iterdir():
            try:
                resolved = child.resolve(strict=True)
            except FileNotFoundError:
                missing_entries += 1
                continue
            if not (resolved == root or resolved.is_relative_to(root)):
                continue
            if child.is_dir():
                entries.append((child.name, child, True))
            elif access == "report" and child.is_file() and child.suffix.lower() == ".xlsx":
                entries.append((child.name, child, False))
        entries.sort(key=lambda item: (not item[2], item[0].casefold(), item[0]))
        count = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        if index < 0 or index >= count:
            raise ValueError("目录内容已变更，请重新打开浏览器")
        card, session = self._page("浏览" + label, mutable=True)
        action = {"field": field, "alias": alias, "root": root_text}
        buttons = []
        for name, child, is_dir in entries[index * PAGE_SIZE:(index + 1) * PAGE_SIZE]:
            buttons.append(self._button(session, ("📁 " if is_dir else "📄 ") + name[:90],
                                        {"op": "browse_path", **action, "path": str(child), "index": 0}))
        if not buttons:
            buttons = [markdown("该目录没有可选的子目录或报告文件。")]
        nav = []
        if index:
            nav.append(self._button(session, "上一页", {"op": "browse_path", **action, "path": str(path), "index": index - 1}))
        if index + 1 < count:
            nav.append(self._button(session, "下一页", {"op": "browse_path", **action, "path": str(path), "index": index + 1}))
        if path != root:
            nav.append(self._button(session, "上一级", {"op": "browse_path", **action, "path": str(path.parent), "index": 0}))
        nav.append(self._button(session, "授权目录", {"op": "browse_roots", "field": field, "index": 0}))
        nav.append(self._button(session, "返回参数", {"op": "editor", "page": editor_page}))
        if access != "report":
            nav.insert(0, self._button(session, "选择此目录" if field != "input_paths" else "添加此目录",
                                       {"op": "browse_select", **action, "path": str(path)}, primary=True))
        else:
            nav[0]["type"] = "primary_filled"
        card["body"]["elements"] = [
            markdown(f"**{escaped(alias)}**\n{escaped(str(path))}\n第 {index + 1}/{count} 页"
                     + (f"\n{missing_entries} 个失效或已删除的项目未显示。" if missing_entries else "")),
            {"tag": "column_set", "flex_mode": "none", "columns": [
                {"tag": "column", "width": "weighted", "weight": 1, "elements": buttons}]},
            columns(nav),
        ]
        return self._finish(card, session)

    def _select_browsed(self, field: str, alias: str, root_text: str, path_text: str) -> tuple[dict, str]:
        _, path = self._browser_path(field, alias, root_text, path_text)
        if field == "compare_base_path":
            if not path.is_file() or path.suffix.lower() != ".xlsx":
                raise ValueError("请选择存在的 XLSX 报告")
        elif not path.is_dir():
            raise ValueError("请选择存在的目录")
        value = str(path)
        if "\n" in value or "\r" in value or (field == "input_paths" and (";" in value or "；" in value)):
            raise ValueError("此路径包含不支持的换行或分号，无法用于表单")
        draft = self.remote._need_draft()
        data = dict(draft["data"])
        if field == "input_paths":
            values = list(data[field])
            normalized = {self.remote._path(item, "input") for item in values}
            if value not in normalized:
                values.append(value)
            data[field] = values
        else:
            data[field] = value
        spec = self.remote._spec(draft["kind"], data)
        draft["data"] = spec.to_dict()
        self.remote.pending = None
        self.remote._persist()
        return self.editor(BROWSE_FIELDS[field][2], notice=f"已选择{BROWSE_FIELDS[field][0]}：{value}")

    def confirmation(self, summary: str) -> tuple[dict, str]:
        pending = self.remote.pending
        if pending is None:
            raise ValueError("没有待确认操作")
        card, session = self._page("确认" + pending["action"], mutable=True)
        session["expires"] = pending["expires"]
        card["header"]["template"] = "red" if pending["action"] in ("删除", "取消") else "blue"
        card["body"]["elements"] = [
            markdown("**请核对后确认，仅执行一次**\n<font color='grey'>5分钟有效，返回修改后需重新预览。</font>"),
            columns([markdown(escaped(summary.split("\n5分钟内发送：")[0]))]),
            columns([
                self._button(session, "确认" + pending["action"],
                             {"op": "confirm", "command": f"确认{pending['action']} {pending['code']}"},
                             primary=True, danger=pending["action"] in ("删除", "取消")),
                self._button(session, "返回修改 / 放弃", {"op": "back"}),
            ]),
        ]
        return self._finish(card, session)

    def tasks(self) -> tuple[dict, str]:
        card, session = self._page("挖掘任务")
        jobs = self.remote.manager.jobs()[-15:]
        form = {"tag": "form", "name": "task_form", "elements": []}
        form["elements"] += self._select(session, "job_id", "选择任务",
            [(j.id, f"{j.id[:8]} {j.title} · {j.status} {j.percent}%") for j in jobs],
            jobs[-1].id if jobs else "")
        form["elements"].append(self._button(session, "查看所选任务", {"op": "task_select"},
                                               form=True, primary=True))
        card["body"]["elements"] = [
            markdown("**任务与桌面队列同步**\n请选择任务查看进度、取消或获取结果。"),
            form, columns([self._button(session, "刷新列表", {"op": "tasks"}),
                           self._button(session, "返回操作台", {"op": "home"})]),
        ]
        return self._finish(card, session)

    def task(self, job_id: str, notice: str = "") -> tuple[dict, str]:
        job = self.remote._job(job_id)
        card, session = self._page("任务详情")
        buttons = [
            self._button(session, "刷新状态", {"op": "task", "job_id": job.id}, primary=True),
            self._button(session, "查看日志", {"op": "command", "command": "日志 " + job.id, "job_id": job.id}),
        ]
        if job.bundle_path is not None:
            buttons.append(self._button(session, "获取结果 ZIP", {"op": "command", "command": "结果 " + job.id, "job_id": job.id}))
        if job.status in ("排队中", "运行中"):
            buttons.append(self._button(session, "取消任务", {"op": "command", "command": "取消 " + job.id, "job_id": job.id}, danger=True))
        card["body"]["elements"] = [
            markdown(f"**{escaped(job.title)} · {escaped(job.status)} {job.percent}%**"),
            columns([markdown(escaped(self.remote.handle("状态 " + job.id)))]),
            columns(buttons),
            columns([self._button(session, "任务列表", {"op": "tasks"}),
                     self._button(session, "操作台", {"op": "home"})]),
        ]
        if notice:
            card["body"]["elements"].insert(2, markdown(escaped(notice)))
        return self._finish(card, session)

    def _values(self, session: dict, event: dict) -> dict:
        values = event.get("form_value") or {}
        if isinstance(values, str):
            values = json.loads(values)
        if not isinstance(values, dict):
            raise ValueError("表单数据格式错误")
        if set(values) - set(session["fields"]) - set(session["actions"]):
            raise ValueError("表单包含未授权字段")
        result = {}
        for name, field in session["fields"].items():
            value = values.get(name, field["default"])
            if field["type"] == "multi":
                if not isinstance(value, list) or any(not isinstance(v, str) or v not in field["options"] for v in value):
                    raise ValueError("多选值不在卡片提供的选项中")
            elif not isinstance(value, str):
                raise ValueError("表单字段必须为文本")
            elif field["type"] == "select" and value not in field["options"]:
                raise ValueError("选择值不在卡片提供的选项中")
            elif len(value) > 1000:
                raise ValueError("表单字段过长")
            result[name] = value
        return result

    def _edit(self, session: dict, values: dict) -> None:
        assignments = []
        labels = {field: label for label, field in FIELDS[session["kind"]].items()}
        for name, value in values.items():
            if name.startswith("field_"):
                label = labels[name[6:]]
                if isinstance(value, list):
                    if any(";" in item or "；" in item for item in value):
                        raise ValueError("目录别名不能包含分号，请在本机修改别名")
                    value = ";".join(value)
                if "\n" in value or "\r" in value:
                    raise ValueError("参数不能包含换行")
                assignments.append(label + "=" + value)
        if "date_start" in values:
            dates = []
            for name in ("date_start", "date_end"):
                value = values[name]
                if value:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: [+-]\d{4})?|\d{8}", value):
                        raise ValueError("日期格式不正确")
                    dates.append(datetime.strptime(value.split()[0].replace("-", ""), "%Y%m%d").strftime("%Y%m%d"))
                else:
                    dates.append("")
            if bool(dates[0]) != bool(dates[1]):
                raise ValueError("开始和结束日期必须一起填写")
            assignments.append("日期=" + ("-".join(dates) if dates[0] else ""))
        name = values.get("preset_name", session["preset_name"]).strip()
        if not name or presets.sanitize_preset_name(name) != name or "\n" in name:
            raise ValueError("预设名称不合法")
        self.remote.handle("修改草稿\n" + "\n".join(assignments))
        self.remote.draft["card_save_name"] = name
        self.remote._persist()

    def handle(self, event: dict) -> tuple[dict, str]:
        self.remote._config()
        action_name = event.get("action_name")
        if not action_name:
            value = event.get("action_value") or {}
            if isinstance(value, str):
                value = json.loads(value)
            action_name = value.get("action_name") if isinstance(value, dict) else None
        match = re.fullmatch(r"mc_([a-f0-9]{24})_\d+", action_name or "")
        session = self.sessions.get(match[1]) if match else None
        if session is None or session["expires"] < time.time():
            raise ValueError("卡片已过期或应用已重启，请发送“操作台”重新打开")
        if session["message_id"] is None or session["message_id"] != event.get("message_id"):
            raise ValueError("卡片来源不匹配")
        if event.get("action_tag") != "button" or action_name not in session["actions"]:
            raise ValueError("仅接受本机签发的按钮操作")
        if session["draft_hash"] is not None and session["draft_hash"] != _digest(self.remote.draft):
            raise ValueError("草稿已变更，请发送“操作台”重新打开最新表单")
        values = self._values(session, event)
        action = session["actions"][action_name]
        # One successful callback dispatch consumes the view, even if a later validation fails.
        self.sessions.pop(session["token"])
        op = action["op"]
        if session["draft_hash"] is not None or op in ("open", "new", "delete", "back"):
            self.sessions = {key: value for key, value in self.sessions.items()
                             if value["draft_hash"] is None}
        if op == "home":
            return self.home()
        if op == "platform":
            return self.platform(action["kind"])
        if op in ("open", "new", "delete"):
            name = values.get("new_name", "") if op == "new" else values.get("preset", "")
            if not name:
                raise ValueError("请选择预设或填写新预设名称")
            command = {"open": "编辑预设", "new": "新建预设", "delete": "删除预设"}[op]
            reply = self.remote.handle(f"{command} {action['kind']} {name}")
            return self.confirmation(reply) if op == "delete" else self.editor()
        if op == "editor":
            return self.editor(action["page"])
        if op == "edit":
            self._edit(session, values)
            next_page = action["next"]
            if next_page in ("basic", "advanced"):
                return self.editor(next_page)
            command = "运行草稿" if next_page == "run" else "保存预设 " + values.get("preset_name", session["preset_name"]).strip()
            reply = self.remote.handle(command)
            return self.confirmation(reply) if self.remote.pending else self.editor(notice=reply)
        if op == "browse_start":
            self._edit(session, values)
            return self.browse_roots(action["field"])
        if op == "browse_roots":
            return self.browse_roots(action["field"], action["index"])
        if op == "browse_path":
            return self.browse_path(action["field"], action["alias"], action["root"],
                                    action["path"], action["index"])
        if op == "browse_select":
            return self._select_browsed(action["field"], action["alias"], action["root"], action["path"])
        if op == "confirm":
            reply = self.remote.handle(action["command"])
            if action["command"].startswith("确认运行"):
                match = re.search(r"任务已提交：([a-f0-9]+)", reply)
                if match is None:
                    raise ValueError("任务提交结果缺少有效ID，请查看任务列表")
                return self.task(match[1], notice=reply)
            if action["command"].startswith("确认取消"):
                return self.tasks()
            return self.editor(notice=reply) if self.remote.draft else self.home()
        if op == "back":
            self.remote.handle("放弃确认")
            return self.editor() if self.remote.draft else self.home()
        if op == "tasks":
            return self.tasks()
        if op == "task_select":
            if not values.get("job_id"):
                raise ValueError("暂无任务可查看")
            return self.task(values["job_id"])
        if op == "task":
            return self.task(action["job_id"])
        if op == "command":
            reply = self.remote.handle(action["command"])
            return (self.confirmation(reply) if action["command"].startswith("取消 ")
                    else self.task(action["job_id"], notice=reply))
        raise ValueError("未知卡片操作")
