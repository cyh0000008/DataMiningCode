"""Typed remote draft editing and one-shot confirmations over the existing job queue."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import socket
import time
from typing import Callable

from . import presets
from .job_manager import Job, JobManager
from .job_spec import J6BJobSpec, MMTJobSpec, validate_job_spec
from .paths import load_settings


COMMON_FIELDS = {
    "车型": "vehicle", "报告软件版本": "software_version", "动力形式": "powertrain_type",
    "任务名称": "task_name", "输出目录": "output_dir", "自动对比": "auto_compare",
    "基线报告": "compare_base_path", "对比详情": "compare_details_sheet",
    "原始报告保存目录": "raw_report_archive_dir",
}
FIELDS = {
    "j6b": {**COMMON_FIELDS, "输入目录": "input_paths", "日期": "date_range",
            "报告名称": "report_name", "启用指标": "enabled_metrics",
            "信号范围": "metric_signal_scope", "信号图": "bad_case_signal_plots",
            "延迟检查": "lcc_sync_check"},
    "mmt": {**COMMON_FIELDS, "输入目录": "source_path", "日期": "query_date",
            "数据软件筛选": "query_software", "软件筛选": "query_software",
            "扫描深度": "max_depth", "目录后缀": "suffix", "报告名称": "report_prefix",
            "文件匹配": "file_pattern", "CAN流水线": "enable_can_pipeline",
            "Topic流水线": "enable_topic_pipeline", "续跑模式": "resume_mode", "试运行": "dry_run",
            "信号图": "bad_case_signal_plots"},
}
HELP = (
    "飞书挖掘操作台\n"
    "发送“操作台”打开按钮和参数表单，无需逐条输入指令。\n"
    "预设 [J6B/MMT]；查看预设 MMT 名称；编辑预设 MMT 名称\n"
    "新建预设 J6B 名称；查看草稿；修改 日期=20261101-20261105\n"
    "修改草稿（后续每行 参数=值）；参数；路径\n"
    "保存预设 [名称] / 另存预设 MMT 名称；覆盖预设；删除预设 MMT 名称\n"
    "运行草稿 / 运行 J6B 名称；确认运行 确认码\n"
    "任务；状态 [任务ID]；取消 任务ID；结果 [任务ID]；日志 任务ID\n"
    "确认覆盖/确认删除/确认取消 确认码；放弃确认；清空草稿\n"
    "修改只影响草稿。运行、覆盖、删除、取消均需5分钟内一次性确认。"
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象")
    return data


class RemoteController:
    def __init__(self, manager: JobManager, path: Path, changed: Callable[[str], None],
                 resend: Callable[[str], None]) -> None:
        self.manager = manager
        self.path = path
        self.changed = changed
        self.resend = resend
        self.pending = None
        self.state_path = path.with_suffix(".remote.json")
        binding = _read_json(path)
        self.identity = [binding.get("owner_open_id"), binding.get("chat_id")]
        self.draft = None
        if self.state_path.exists():
            state = _read_json(self.state_path)
            if state.get("identity") == self.identity:
                self.draft = state.get("draft")

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"identity": self.identity, "draft": self.draft},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _config(self) -> dict:
        config = _read_json(self.path)
        if not config.get("enabled") or [config.get("owner_open_id"), config.get("chat_id")] != self.identity:
            self.pending = None
            raise ValueError("绑定已变更或机器人已禁用，请重新连接")
        return config

    def _remote(self) -> dict:
        return self._config().get("remote", {})

    @staticmethod
    def _kind_name(text: str) -> tuple[str, str]:
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() not in FIELDS:
            raise ValueError("请填写类型和名称，例如 MMT 日常挖掘")
        kind, name = parts[0].lower(), parts[1].strip()
        if presets.sanitize_preset_name(name) != name:
            raise ValueError("预设名称不能包含路径或非法字符")
        return kind, name

    def _need_draft(self) -> dict:
        if self.draft is None:
            raise ValueError("没有草稿，请先编辑预设或新建预设")
        return self.draft

    def _spec(self, kind: str, data: dict) -> J6BJobSpec | MMTJobSpec:
        spec = (J6BJobSpec if kind == "j6b" else MMTJobSpec).from_dict(data)
        validate_job_spec(spec)
        return spec

    def _open(self, kind: str, name: str, *, new: bool = False) -> str:
        if new:
            data = (J6BJobSpec() if kind == "j6b" else MMTJobSpec()).to_dict()
            revision = None
        else:
            revision = presets.preset_revision(kind, name)
            if revision is None:
                raise ValueError("预设不存在")
            data = presets.load_preset(kind, name)
            if revision != presets.preset_revision(kind, name):
                raise ValueError("预设读取期间被修改，请重试")
        spec = self._spec(kind, data)
        self.draft = {"kind": kind, "name": name, "data": spec.to_dict(),
                      "original": spec.to_dict(), "revision": revision}
        self.pending = None
        self._persist()
        return self._show_draft()

    def _format(self, kind: str, data: dict) -> str:
        lines = []
        seen = set()
        for label, field in FIELDS[kind].items():
            if field in seen:
                continue
            seen.add(field)
            value = data.get(field)
            if isinstance(value, bool):
                value = "开启" if value else "关闭"
            elif isinstance(value, list):
                value = "；".join(value)
            lines.append(f"{label}={value if value != '' else '（未填写）'}")
        lines.append("模板配置=本机预设固定配置，远程不可修改")
        return "\n".join(lines)

    def _show_draft(self) -> str:
        draft = self._need_draft()
        return f"草稿：{draft['kind'].upper()} {draft['name']}\n" + self._format(draft["kind"], draft["data"])

    def _update(self, text: str) -> str:
        draft = self._need_draft()
        data = deepcopy(draft["data"])
        fields = FIELDS[draft["kind"]]
        defaults = (J6BJobSpec() if draft["kind"] == "j6b" else MMTJobSpec()).to_dict()
        assignments = text.strip().splitlines()
        if not assignments:
            raise ValueError("请填写每行一个 参数=值")
        seen = set()
        for assignment in assignments:
            if "=" not in assignment:
                raise ValueError("每行请使用 参数=值，不支持任意指令")
            label, value = (part.strip() for part in assignment.split("=", 1))
            field = fields.get(label)
            if field is None:
                raise ValueError(f"不可修改参数：{label}；发送“参数”查看支持项")
            if field in seen:
                raise ValueError(f"参数重复：{label}")
            seen.add(field)
            if isinstance(defaults[field], bool):
                booleans = {"开启": True, "关闭": False, "true": True, "false": False,
                            "开": True, "关": False}
                if value.lower() not in booleans:
                    raise ValueError(f"{label} 请填写 开启 或 关闭")
                data[field] = booleans[value.lower()]
            elif isinstance(defaults[field], int):
                try:
                    data[field] = int(value)
                except ValueError as exc:
                    raise ValueError(f"{label} 必须是整数") from exc
            elif isinstance(defaults[field], list):
                data[field] = [item.strip() for item in value.replace("；", ";").split(";") if item.strip()]
            else:
                data[field] = value.upper() if field == "powertrain_type" else value
        spec = self._spec(draft["kind"], data)
        draft["data"] = spec.to_dict()
        self.pending = None
        self._persist()
        return "草稿已修改，原预设未变更。\n" + self._show_draft()

    def _path(self, value: str, access: str, *, file: bool = False) -> str:
        aliases = self._remote().get("path_aliases", {})
        item = aliases.get(value)
        raw = item["path"] if item else value
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise ValueError(f"路径 {value} 未配置为本机别名，或不是绝对路径")
        candidate = candidate.resolve()
        for alias in aliases.values():
            if alias.get("access") != access:
                continue
            root = Path(alias["path"]).expanduser().resolve()
            allowed = candidate == root if root.is_file() else candidate.is_relative_to(root)
            if allowed:
                if file and (not candidate.is_file() or candidate.suffix.lower() != ".xlsx"):
                    raise ValueError("基线报告必须为存在的 XLSX")
                if access == "input" and not candidate.exists():
                    raise ValueError("输入路径不存在")
                if access == "input" and not candidate.is_dir() and candidate.suffix.lower() != ".h5":
                    raise ValueError("输入路径必须为数据目录或 H5")
                if access == "output" and candidate.exists() and not candidate.is_dir():
                    raise ValueError("输出路径必须是目录")
                return str(candidate)
        raise ValueError(f"路径 {value} 不在允许的 {access} 范围；请在应用“飞书远程路径设置”授权")

    def _prepare(self, draft: dict) -> tuple[J6BJobSpec | MMTJobSpec, Path, str]:
        settings = load_settings()
        if not settings.is_valid():
            raise ValueError("本机项目环境未配置正确")
        kind = draft["kind"]
        spec = self._spec(kind, draft["data"])
        validate_job_spec(spec, require_complete=True)
        engine = settings.j6b_dir() if kind == "j6b" else settings.mmt_dir()
        if not (engine / "DataMining.py").is_file():
            raise ValueError("本机挖掘入口不存在，请检查环境设置")
        if spec.auto_compare and not (
            settings.compare_dir() / "mpi_cpi_compare.py"
        ).is_file():
            raise ValueError("已启用自动对比，但本机对比入口不存在")
        template = Path(spec.template_config_path) if spec.template_config_path else engine / "config.json"
        template = template.resolve()
        config = _read_json(template)
        if isinstance(spec, J6BJobSpec):
            spec.input_paths = [self._path(item, "input") for item in spec.input_paths]
            metrics = spec.enabled_metrics.split(",")
            available = {p.stem for p in (engine / "filters").rglob("*.py") if not p.name.startswith("_")}
            unknown = set(item.strip() for item in metrics) - available - {"all"}
            if unknown:
                raise ValueError("未知指标：" + "、".join(sorted(unknown)))
        else:
            spec.source_path = self._path(spec.source_path, "input")
            if not Path(spec.source_path).is_dir():
                raise ValueError("MMT 输入必须是数据目录")
            pipelines = config.get("pipelines", [])
            items = [{**item, "name": name} for name, item in pipelines.items()] if isinstance(pipelines, dict) else pipelines
            enabled = {"can": spec.enable_can_pipeline, "topic": spec.enable_topic_pipeline}
            active = False
            for item in items:
                pipeline_kind = str(item.get("type", item.get("name", ""))).lower()
                pipeline_enabled = enabled.get(pipeline_kind, item.get("enabled", True))
                active = active or pipeline_enabled
                if pipeline_enabled and item.get("output_dir"):
                    output = Path(item["output_dir"])
                    self._path(str(output if output.is_absolute() else template.parent / output), "output")
            if not active:
                raise ValueError("模板中没有可启用的流水线")
        output = spec.output_dir or config.get("output_dir", "Result_Test" if kind == "j6b" else "Result")
        if not spec.output_dir and not Path(output).is_absolute():
            output = str(template.parent / output)
        spec.output_dir = self._path(output, "output")
        if spec.compare_base_path:
            spec.compare_base_path = self._path(spec.compare_base_path, "report", file=True)
        if spec.raw_report_archive_dir:
            spec.raw_report_archive_dir = self._path(spec.raw_report_archive_dir, "output")
        spec.template_config_path = str(template)
        fingerprint = _digest({"spec": spec.to_dict(), "settings": settings.to_dict(),
                               "template": hashlib.sha256(template.read_bytes()).hexdigest(),
                               "remote": self._remote(), "identity": self.identity})
        return spec, template, fingerprint

    def _saving_data(self, draft: dict) -> dict:
        spec = self._spec(draft["kind"], draft["data"])
        if isinstance(spec, J6BJobSpec):
            spec.input_paths = [self._path(value, "input") for value in spec.input_paths]
        elif spec.source_path:
            spec.source_path = self._path(spec.source_path, "input")
        if spec.output_dir:
            spec.output_dir = self._path(spec.output_dir, "output")
        if spec.raw_report_archive_dir:
            spec.raw_report_archive_dir = self._path(spec.raw_report_archive_dir, "output")
        if spec.compare_base_path:
            spec.compare_base_path = self._path(spec.compare_base_path, "report", file=True)
        return spec.to_dict()

    def _confirm_request(self, action: str, payload: dict, fingerprint: str, summary: str) -> str:
        code = secrets.token_hex(3).upper()
        self.pending = {"action": action, "code": code, "expires": time.time() + 300.,
                        "payload": deepcopy(payload), "fingerprint": fingerprint}
        return summary + f"\n5分钟内发送：确认{action} {code}\n仅执行一次；可发送“放弃确认”。"

    def _run_request(self) -> str:
        draft = self._need_draft()
        spec, template, fingerprint = self._prepare(draft)
        changes = [label for label, key in FIELDS[draft["kind"]].items()
                   if draft["data"].get(key) != draft["original"].get(key)]
        machine = self._remote().get("machine_name") or socket.gethostname()
        summary = f"待运行：{draft['kind'].upper()} {draft['name']}\n运行电脑：{machine}\n"
        summary += self._format(draft["kind"], spec.to_dict())
        summary += f"\n模板：{template.name}（其余配置继承本机模板）"
        summary += f"\n修改项：{'、'.join(changes) or '无'}"
        queued = sum(job.status == "排队中" for job in self.manager.jobs())
        summary += f"\n队列：运行 {self.manager.running_count()}，等待 {queued}"
        return self._confirm_request("运行", draft, fingerprint, summary)

    def _job(self, prefix: str) -> Job:
        matches = [job for job in self.manager.jobs() if job.id.startswith(prefix.strip())]
        if not prefix.strip() or len(matches) != 1:
            raise ValueError("任务ID不存在或不唯一，请使用“任务”列表中的ID")
        return matches[0]

    def _confirmation(self, action: str, code: str) -> str:
        pending = self.pending
        if pending is None:
            raise ValueError("没有待确认操作（应用重启后需重新确认）")
        if time.time() > pending["expires"]:
            self.pending = None
            raise ValueError("确认已过期，请重新发起操作")
        if pending["action"] != action or not secrets.compare_digest(pending["code"], code.strip().upper()):
            raise ValueError("确认类型或确认码错误")
        self.pending = None
        payload = pending["payload"]
        if action == "运行":
            spec, template, fingerprint = self._prepare(payload)
            if fingerprint != pending["fingerprint"]:
                raise ValueError("参数、路径权限、环境或模板已变更，请重新预览确认")
            settings = load_settings()
            kind = payload["kind"]
            engine = settings.j6b_dir() if kind == "j6b" else settings.mmt_dir()
            submit = self.manager.submit_j6b if kind == "j6b" else self.manager.submit_mmt
            job_id = submit(settings.python_for(kind), engine, template, spec,
                            compare_python=settings.python_for("compare"), compare_dir=settings.compare_dir())
            return f"任务已提交：{job_id}\n后续进度和结果包自动回传。"
        if action in ("覆盖", "删除"):
            kind, name = payload["kind"], payload["name"]
            if action == "覆盖":
                data = self._saving_data(payload)
                presets.save_preset(kind, name, data, expected_revision=pending["fingerprint"])
            else:
                presets.delete_preset(kind, name, expected_revision=pending["fingerprint"])
            self.changed(kind)
            if self.draft and self.draft["kind"] == kind and self.draft["name"] == name:
                self.draft["revision"] = presets.preset_revision(kind, name)
                if action == "覆盖":
                    self.draft["data"] = deepcopy(payload["data"])
                    self.draft["original"] = deepcopy(payload["data"])
                self._persist()
            return f"已{action}预设：{kind.upper()} {name}"
        job = self._job(payload["job_id"])
        if job.status not in ("排队中", "运行中"):
            raise ValueError("任务已结束，无需取消")
        self.manager.cancel(job.id)
        return f"已请求取消：{job.id}"

    def handle(self, text: str) -> str:
        self._config()
        text = text.strip()
        if text in ("帮助", "help"):
            return HELP
        if text.startswith("确认"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or parts[0][2:] not in ("运行", "覆盖", "删除", "取消"):
                raise ValueError("请使用预览中提供的确认类型和确认码")
            return self._confirmation(parts[0][2:], parts[1])
        if text == "放弃确认":
            self.pending = None
            return "已放弃待确认操作。"
        if text == "清空草稿":
            self.draft = self.pending = None
            self._persist()
            return "已清空草稿，保存的预设不变。"
        if text == "查看草稿":
            return self._show_draft()
        if text == "参数":
            draft = self._need_draft()
            return "可修改参数：\n" + "、".join(FIELDS[draft["kind"]]) + "\n多输入目录用分号分隔。"
        if text == "路径":
            aliases = self._remote().get("path_aliases", {})
            return "\n".join(f"{name} [{item['access']}] = {item['path']}" for name, item in aliases.items()) or "暂无路径授权，请在应用内配置。"
        if text == "预设" or text.startswith("预设 "):
            kind = text[2:].strip().lower()
            if kind and kind not in FIELDS:
                raise ValueError("预设类型必须为 J6B 或 MMT")
            return "\n".join(k.upper() + "：" + ("、".join(presets.list_presets(k)) or "暂无")
                             for k in ([kind] if kind else FIELDS))
        for command in ("查看预设", "编辑预设", "新建预设", "运行", "删除预设"):
            if text.startswith(command + " "):
                kind, name = self._kind_name(text[len(command):].strip())
                if command == "查看预设":
                    return f"{kind.upper()} {name}\n" + self._format(kind, self._spec(kind, presets.load_preset(kind, name)).to_dict())
                if command == "删除预设":
                    revision = presets.preset_revision(kind, name)
                    if revision is None:
                        raise ValueError("预设不存在")
                    return self._confirm_request("删除", {"kind": kind, "name": name}, revision,
                                                 f"待删除预设：{kind.upper()} {name}（不会删除报告或数据）")
                reply = self._open(kind, name, new=command == "新建预设")
                return self._run_request() if command == "运行" else reply
        if text.startswith("修改草稿") or text.startswith("修改 "):
            prefix = "修改草稿" if text.startswith("修改草稿") else "修改"
            return self._update(text[len(prefix):])
        if text in ("保存预设", "覆盖预设") or text.startswith(("保存预设 ", "另存预设 ")):
            draft = self._need_draft()
            name = draft["name"]
            if text.startswith("另存预设 "):
                kind, name = self._kind_name(text[len("另存预设 "):])
                if kind != draft["kind"]:
                    raise ValueError("不能跨车型平台保存预设")
            elif text.startswith("保存预设 "):
                name = text[len("保存预设 "):].strip()
            if presets.sanitize_preset_name(name) != name:
                raise ValueError("预设名称不能包含路径或非法字符")
            data = self._saving_data(draft)
            revision = presets.preset_revision(draft["kind"], name)
            if revision is not None:
                if name == draft["name"] and revision != draft["revision"]:
                    raise ValueError("原预设已被本机修改，请重新编辑或另存为新名称")
                current = self._spec(draft["kind"], presets.load_preset(draft["kind"], name)).to_dict()
                changes = [f"{label}: {current.get(key)} → {data.get(key)}"
                           for label, key in FIELDS[draft["kind"]].items()
                           if current.get(key) != data.get(key)]
                return self._confirm_request("覆盖", {"kind": draft["kind"], "name": name, "data": data},
                                             revision, f"待覆盖：{name}\n" + ("\n".join(changes) or "无参数差异"))
            if text == "覆盖预设":
                raise ValueError("原预设不存在，请使用保存预设")
            presets.save_preset(draft["kind"], name, data, expected_revision=None)
            draft["data"] = data
            draft["name"], draft["revision"] = name, presets.preset_revision(draft["kind"], name)
            draft["original"] = deepcopy(draft["data"])
            self.pending = None
            self._persist()
            self.changed(draft["kind"])
            return f"已保存预设：{draft['kind'].upper()} {name}，应用列表已刷新。"
        if text == "运行草稿":
            return self._run_request()
        if text in ("任务", "状态"):
            jobs = self.manager.jobs()[-15:]
            return "\n".join(f"{job.id[:8]} {job.kind_label} {job.status} {job.percent}% {job.title}" for job in jobs) or "本进程暂无任务。"
        for command in ("状态", "取消", "日志", "结果"):
            if text.startswith(command + " ") or text == "结果":
                if text == "结果":
                    jobs = [job for job in self.manager.jobs() if job.bundle_path is not None]
                    if not jobs:
                        raise ValueError("本进程还没有结果包")
                    job = jobs[-1]
                else:
                    job = self._job(text[len(command):])
                if command == "状态":
                    return f"{job.id}\n{job.title}：{job.status} {job.percent}%\n{job.progress_text}\n报告打包：{job.bundle_error or ('已完成' if job.bundle_path else '未完成')}"
                if command == "取消":
                    if job.status not in ("排队中", "运行中"):
                        raise ValueError("任务已结束，无需取消")
                    return self._confirm_request("取消", {"job_id": job.id}, "",
                                                 f"待取消：{job.id} {job.title} {job.status}")
                if command == "日志":
                    return f"{job.id} 最近日志（最多10行/3000字）：\n" + ("\n".join(job.log_lines[-10:])[-3000:] or "暂无日志")
                if job.bundle_path is None or not job.bundle_path.is_file():
                    raise ValueError("该任务尚未生成可发送的结果包")
                self.resend(job.id)
                return f"结果包已加入发送队列：{job.id}"
        raise ValueError("无法识别指令，请发送“帮助”。")
