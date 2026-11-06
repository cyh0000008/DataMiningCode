"""三类任务的参数定义（表单数据结构），用于运行和预设的保存/加载。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List
import warnings
from datetime import datetime
import re


JOB_KIND_J6B = "j6b"
JOB_KIND_MMT = "mmt"
JOB_KIND_COMPARE = "compare"
JOB_KIND_LCC_SYNC = "lcc_sync"
POWERTRAIN_TYPES = ("ICE", "PHEV", "BEV")


def normalize_powertrain_type(value: str) -> str:
    value = value.strip().upper()
    if value == "PHEV/BEV":
        warnings.warn("旧动力形式 PHEV/BEV 已迁移为 PHEV；纯电车辆请重新选择 BEV。", UserWarning)
        value = "PHEV"
    if value not in POWERTRAIN_TYPES:
        raise ValueError("动力形式必须为 ICE、PHEV 或 BEV")
    return value

JOB_KIND_LABELS = {
    JOB_KIND_J6B: "J6B 挖掘",
    JOB_KIND_MMT: "MMT 挖掘",
    JOB_KIND_COMPARE: "对比报告",
    JOB_KIND_LCC_SYNC: "LCC延迟检查",
}


def _from_dict(cls, data: Dict[str, Any]):
    data = dict(data)
    if cls.__name__ in {"J6BJobSpec", "MMTJobSpec"}:
        data.setdefault("vehicle", data.get("compare_target_vehicle", ""))
        data.setdefault("software_version", data.get("compare_target_version", ""))
    defaults = cls()
    known = {f: data.get(f, getattr(defaults, f)) for f in cls.__dataclass_fields__}
    known["powertrain_type"] = normalize_powertrain_type(known["powertrain_type"])
    return cls(**known)


def validate_job_spec(spec: "J6BJobSpec | MMTJobSpec", *, require_complete: bool = False) -> None:
    """Validate task parameters before either desktop or remote config generation."""
    defaults = type(spec)()
    for name, value in spec.to_dict().items():
        default = getattr(defaults, name)
        if isinstance(default, bool):
            valid = type(value) is bool
        elif isinstance(default, int):
            valid = type(value) is int
        elif isinstance(default, list):
            valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
        else:
            valid = isinstance(value, str)
        if not valid:
            raise ValueError(f"参数 {name} 类型不正确")
    normalize_powertrain_type(spec.powertrain_type)
    report_name = spec.report_name if isinstance(spec, J6BJobSpec) else spec.report_prefix
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', report_name):
        raise ValueError("报告名称不能包含路径或非法文件名字符")
    date = spec.date_range if isinstance(spec, J6BJobSpec) else spec.query_date
    if date:
        match = re.fullmatch(r"(20\d{6})(?:[-~](20\d{6}))?", date)
        if match is None:
            raise ValueError("日期请使用 YYYYMMDD 或 YYYYMMDD-YYYYMMDD")
        start, end = match[1], match[2] or match[1]
        datetime.strptime(start, "%Y%m%d")
        datetime.strptime(end, "%Y%m%d")
        if start > end:
            raise ValueError("日期开始不能晚于结束")
    if isinstance(spec, J6BJobSpec):
        if spec.metric_signal_scope not in ("all", "debugcan_only", "pcan_ccan_only"):
            raise ValueError("信号范围必须为 all、debugcan_only 或 pcan_ccan_only")
        metrics = [part.strip() for part in spec.enabled_metrics.split(",")]
        if not all(re.fullmatch(r"[A-Za-z_]\w*", part) for part in metrics):
            raise ValueError("启用指标必须为 all 或逗号分隔的指标名称")
        if "all" in metrics and metrics != ["all"]:
            raise ValueError("all 不能与单项指标混用")
    else:
        if not 0 <= spec.max_depth <= 20:
            raise ValueError("扫描深度必须在 0～20 之间")
        if spec.resume_mode not in ("auto", "never"):
            raise ValueError("续跑模式必须为 auto 或 never")
        if not spec.enable_can_pipeline and not spec.enable_topic_pipeline:
            raise ValueError("至少启用 CAN 或 Topic 流水线")
        if not spec.file_pattern or any(char in spec.file_pattern for char in "/\\"):
            raise ValueError("文件匹配只能填写文件名模式，不能包含路径")
        if not spec.suffix or any(char in spec.suffix for char in "/\\"):
            raise ValueError("目录后缀不能为空或包含路径")
    if require_complete:
        source = spec.input_paths if isinstance(spec, J6BJobSpec) else spec.source_path
        if not source:
            raise ValueError("输入目录不能为空")
        if not isinstance(spec, MMTJobSpec) or not spec.dry_run:
            if not all((spec.vehicle.strip(), spec.software_version.strip(), date)):
                raise ValueError("运行前请填写车型、报告软件版本和日期")


@dataclass
class J6BJobSpec:
    vehicle: str = ""
    software_version: str = ""
    powertrain_type: str = "ICE"
    task_name: str = ""
    template_config_path: str = ""
    input_paths: List[str] = field(default_factory=list)
    date_range: str = ""
    output_dir: str = ""
    report_name: str = ""
    enabled_metrics: str = "all"
    metric_signal_scope: str = "all"
    bad_case_signal_plots: bool = False
    lcc_sync_check: bool = True
    auto_compare: bool = False
    compare_base_path: str = ""
    compare_details_sheet: bool = False
    compare_base_vehicle: str = ""
    compare_target_vehicle: str = ""
    compare_base_date: str = ""
    compare_target_date: str = ""
    compare_base_version: str = ""
    compare_target_version: str = ""
    raw_report_archive_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "J6BJobSpec":
        return _from_dict(cls, data)


@dataclass
class MMTJobSpec:
    vehicle: str = ""
    software_version: str = ""
    powertrain_type: str = "PHEV"
    task_name: str = ""
    template_config_path: str = ""
    source_path: str = ""
    query_date: str = ""
    query_software: str = ""
    max_depth: int = 4
    suffix: str = "_done"
    output_dir: str = ""
    report_prefix: str = ""
    file_pattern: str = "*.h5"
    enable_can_pipeline: bool = True
    enable_topic_pipeline: bool = True
    resume_mode: str = "auto"
    dry_run: bool = False
    auto_compare: bool = False
    compare_base_path: str = ""
    compare_details_sheet: bool = False
    compare_base_vehicle: str = ""
    compare_target_vehicle: str = ""
    compare_base_date: str = ""
    compare_target_date: str = ""
    compare_base_version: str = ""
    compare_target_version: str = ""
    raw_report_archive_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MMTJobSpec":
        return _from_dict(cls, data)


@dataclass
class CompareJobSpec:
    task_name: str = ""
    report_profile: str = "J6B"
    powertrain_type: str = "PHEV"
    base_path: str = ""
    target_path: str = ""
    template_path: str = ""
    output_path: str = ""
    details_sheet: bool = False
    base_vehicle: str = ""
    target_vehicle: str = ""
    base_date: str = ""
    target_date: str = ""
    base_version: str = ""
    target_version: str = ""
    base_source_date: str = ""
    target_source_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompareJobSpec":
        return _from_dict(cls, data)
