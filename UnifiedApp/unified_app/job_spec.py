"""三类任务的参数定义（表单数据结构），用于运行和预设的保存/加载。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


JOB_KIND_J6B = "j6b"
JOB_KIND_MMT = "mmt"
JOB_KIND_COMPARE = "compare"

JOB_KIND_LABELS = {
    JOB_KIND_J6B: "J6B 挖掘",
    JOB_KIND_MMT: "MMT 挖掘",
    JOB_KIND_COMPARE: "对比报告",
}


def _from_dict(cls, data: Dict[str, Any]):
    defaults = cls()
    known = {f: data.get(f, getattr(defaults, f)) for f in cls.__dataclass_fields__}
    return cls(**known)


@dataclass
class J6BJobSpec:
    task_name: str = ""
    template_config_path: str = ""
    input_paths: List[str] = field(default_factory=list)
    date_range: str = ""
    output_dir: str = ""
    report_name: str = ""
    enabled_metrics: str = "all"
    metric_signal_scope: str = "all"
    auto_compare: bool = False
    compare_base_path: str = ""
    compare_details_sheet: bool = False
    compare_base_vehicle: str = ""
    compare_target_vehicle: str = ""
    compare_base_date: str = ""
    compare_target_date: str = ""
    compare_base_version: str = ""
    compare_target_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "J6BJobSpec":
        return _from_dict(cls, data)


@dataclass
class MMTJobSpec:
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MMTJobSpec":
        return _from_dict(cls, data)


@dataclass
class CompareJobSpec:
    task_name: str = ""
    report_profile: str = "J6B"
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
