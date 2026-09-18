"""根据表单参数，合并既有工程的模板 config.json，写出一次性运行用的临时配置文件。

关键点：既有脚本里部分路径字段（如 output_dir、func_folder_path）是相对于
*config 文件所在目录* 解析的。临时配置文件被写到系统临时目录后，这些相对路径
就会解析错误，因此这里必须把所有相对路径在写出前改写成绝对路径（以模板配置
原始所在目录为基准），其余字段（如 J6B 的 signals 映射、MMT 每个 pipeline 的
preprocess_signals）保持模板原值不变。
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, List, Union

from .job_spec import J6BJobSpec, MMTJobSpec


def _abs(base_dir: Path, value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else (base_dir / path).resolve())


def _abs_multi(base_dir: Path, value: Union[str, List[str]]) -> Union[str, List[str]]:
    if isinstance(value, list):
        return [_abs(base_dir, item) for item in value]
    return _abs(base_dir, value)


def _load_template(template_path: Path) -> dict:
    if not template_path.exists():
        raise FileNotFoundError(f"模板配置文件不存在：{template_path}")
    with template_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _write_temp_json(data: dict, prefix: str) -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / "unified_mining_app_runs"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    unique = f"{prefix}_{stamp}_{next(tempfile._get_candidate_names())}.json"  # noqa: SLF001
    path = tmp_dir / unique
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    return path


def prepare_j6b_config(template_path: Path, spec: J6BJobSpec) -> Path:
    template_path = template_path.resolve()
    template_dir = template_path.parent
    data = _load_template(template_path)

    if spec.input_paths:
        data["input_path"] = list(spec.input_paths)
    if spec.date_range.strip() or spec.date_range == "":
        data["date"] = spec.date_range.strip()
    if spec.output_dir.strip():
        data["output_dir"] = spec.output_dir.strip()
    if spec.report_name.strip():
        data["report_name"] = spec.report_name.strip()
    if spec.enabled_metrics.strip():
        raw = [item.strip() for item in spec.enabled_metrics.split(",") if item.strip()]
        data["enabled_metrics"] = raw if raw else ["all"]
    if spec.metric_signal_scope.strip():
        data["metric_signal_scope"] = spec.metric_signal_scope.strip()

    if "input_path" not in data or not data["input_path"]:
        raise ValueError("输入路径不能为空")

    data["input_path"] = _abs_multi(template_dir, data["input_path"])
    data["output_dir"] = _abs(template_dir, data.get("output_dir", "Result_Test"))
    data["bad_case_extract_config"] = _abs(
        template_dir, data.get("bad_case_extract_config", "bad_case_extract.json")
    )

    return _write_temp_json(data, "j6b")


def prepare_mmt_config(template_path: Path, spec: MMTJobSpec) -> Path:
    template_path = template_path.resolve()
    template_dir = template_path.parent
    data = _load_template(template_path)

    if spec.source_path.strip():
        data["source_path"] = spec.source_path.strip()
    data["query_date"] = spec.query_date.strip()
    data["query_software"] = spec.query_software.strip()
    data["max_depth"] = int(spec.max_depth)
    if spec.suffix.strip():
        data["suffix"] = spec.suffix.strip()
    if spec.output_dir.strip():
        data["output_dir"] = spec.output_dir.strip()
    if spec.report_prefix.strip():
        data["report_prefix"] = spec.report_prefix.strip()
    if spec.file_pattern.strip():
        data["file_pattern"] = spec.file_pattern.strip()

    if not data.get("source_path"):
        raise ValueError("数据源路径不能为空")

    data["output_dir"] = _abs(template_dir, data.get("output_dir", "Result"))

    pipelines = data.get("pipelines", [])
    enabled_map = {"can": spec.enable_can_pipeline, "topic": spec.enable_topic_pipeline}
    normalized_pipelines = []
    if isinstance(pipelines, dict):
        pipeline_items = []
        for name, item in pipelines.items():
            item = dict(item)
            item.setdefault("name", name)
            pipeline_items.append(item)
    else:
        pipeline_items = pipelines

    for item in pipeline_items:
        item = dict(item)
        ptype = str(item.get("type", item.get("name", ""))).strip().lower()
        if ptype in enabled_map:
            item["enabled"] = bool(enabled_map[ptype])
        if "func_folder_path" in item:
            item["func_folder_path"] = _abs(template_dir, item["func_folder_path"])
        if "output_dir" in item and item["output_dir"]:
            item["output_dir"] = _abs(template_dir, item["output_dir"])
        normalized_pipelines.append(item)
    data["pipelines"] = normalized_pipelines

    if not any(item.get("enabled", True) for item in normalized_pipelines):
        raise ValueError("至少需要启用一个 pipeline（CAN 或 Topic）")

    return _write_temp_json(data, "mmt")
