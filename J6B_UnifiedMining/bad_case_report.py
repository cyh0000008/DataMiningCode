from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Mapping, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from reporting import ensure_xlsx, metric_report_sort_key


DEFAULT_RULE = {
    "severity_threshold": 0.0,
    "severity_top_n": 10,
    "max_cases": 10,
}

BAD_CASE_HEADERS = [
    "指标类型",
    "指标名称",
    "文件路径",
    "日期",
    "时间",
    "严重度排名",
    "严重程度得分",
    "问题内容",
    "开始时间(s)",
    "结束时间(s)",
]


def write_bad_case_report(
    output_dir: Path,
    config_path: Path,
    metrics: Iterable[Any],
) -> Optional[Path]:
    config = load_bad_case_config(config_path)
    if config is None or not bool(config.get("enabled", True)):
        return None

    output_dir = Path(output_dir)
    metric_list = list(metrics)
    metric_categories = {metric.name: metric.category for metric in metric_list}
    metric_order = sorted(
        [metric.name for metric in metric_list],
        key=lambda metric_name: metric_report_sort_key(
            metric_name,
            metric_categories.get(metric_name, "CPI"),
        ),
    )

    rows: List[Dict[str, Any]] = []
    for metric_name in metric_order:
        event_path = output_dir / ("%s_events.json" % metric_name)
        if not event_path.exists():
            continue
        cases = read_metric_bad_cases(event_path, metric_categories.get(metric_name, "CPI"))
        selected = apply_bad_case_rule(cases, rule_for_metric(config, metric_name))
        rows.extend(selected)

    output_filename = ensure_xlsx(str(config.get("output_filename", "bad_case_report.xlsx")))
    report_path = output_dir / Path(output_filename).name
    write_bad_case_xlsx(rows, report_path)
    return report_path


def load_bad_case_config(config_path: Path) -> Optional[Dict[str, Any]]:
    if not config_path.exists():
        return None
    with config_path.open("r", encoding="utf-8") as fp:
        config = json.load(fp)
    if not isinstance(config, dict):
        raise ValueError("bad_case_extract.json must contain a JSON object")
    return config


def rule_for_metric(config: Mapping[str, Any], metric_name: str) -> Dict[str, Any]:
    default_rule = dict(DEFAULT_RULE)
    configured_default = config.get("default_rule", {})
    if isinstance(configured_default, Mapping):
        default_rule.update(configured_default)

    metric_rules = config.get("metric_rules", {})
    if isinstance(metric_rules, Mapping):
        metric_rule = metric_rules.get(metric_name)
        if isinstance(metric_rule, Mapping):
            default_rule.update(metric_rule)
    return default_rule


def read_metric_bad_cases(event_path: Path, fallback_category: str) -> List[Dict[str, Any]]:
    with event_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, list):
        raise ValueError("Metric event file must contain a JSON array: %s" % event_path)

    cases: List[Dict[str, Any]] = []
    for result in payload:
        if not isinstance(result, Mapping):
            continue
        source_path = str(result.get("source_path", ""))
        metric_name = str(result.get("metric_name", event_path.stem.replace("_events", "")))
        category = str(result.get("category", fallback_category))
        file_dt = extract_datetime_from_path(source_path)
        events = result.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, Mapping):
                continue
            severity = as_float(event.get("severity"))
            if severity is None:
                continue
            cases.append({
                "category": category,
                "metric_name": metric_name,
                "source_path": source_path,
                "date": file_dt.strftime("%m/%d") if file_dt else "",
                "time": file_dt.strftime("%H:%M") if file_dt else "",
                "severity": severity,
                "message": str(event.get("message", "")),
                "start_s": event.get("start_s", ""),
                "end_s": event.get("end_s", ""),
            })
    return cases


def apply_bad_case_rule(cases: List[Dict[str, Any]], rule: Mapping[str, Any]) -> List[Dict[str, Any]]:
    severity_threshold = number_rule_value(rule, ("severity_threshold", "min_severity"))
    severity_top_n = integer_rule_value(rule, ("severity_top_n", "top_n_by_severity"))
    max_cases = integer_rule_value(rule, ("max_cases", "limit"))

    filtered = []
    for case in cases:
        severity = as_float(case.get("severity"))
        if severity is None:
            continue
        if severity_threshold is not None and severity < severity_threshold:
            continue
        filtered.append(case)

    filtered.sort(key=lambda item: float(item["severity"]), reverse=True)
    if severity_top_n is not None:
        filtered = filtered[:severity_top_n]
    if max_cases is not None:
        filtered = filtered[:max_cases]

    for rank, case in enumerate(filtered, 1):
        case["severity_rank"] = rank
    return filtered


def write_bad_case_xlsx(rows: List[Mapping[str, Any]], output_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Bad Case提取"
    sheet.append(BAD_CASE_HEADERS)

    header_font = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center")
    for cell in sheet[1]:
        cell.font = header_font
        cell.alignment = center

    if not rows:
        sheet.append(["", "无匹配Bad Case", "", "", "", "", "", "", "", ""])
    else:
        for row in rows:
            sheet.append([
                row.get("category", ""),
                row.get("metric_name", ""),
                row.get("source_path", ""),
                row.get("date", ""),
                row.get("time", ""),
                row.get("severity_rank", ""),
                row.get("severity", ""),
                row.get("message", ""),
                row.get("start_s", ""),
                row.get("end_s", ""),
            ])

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column_cells in sheet.columns:
        width = min(max(len(str(cell.value or "")) for cell in column_cells) + 4, 100)
        sheet.column_dimensions[column_cells[0].column_letter].width = width
    workbook.save(output_path)


def extract_datetime_from_path(file_path: str) -> Optional[datetime]:
    file_name = file_name_from_path(file_path)
    patterns = [
        r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})[_-](\d{2})[_-](\d{2})[_-](\d{2})(?!\d)",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})(?!\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, file_name)
        if not match:
            continue
        year, month, day, hour, minute, second = (int(item) for item in match.groups())
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None
    return None


def file_name_from_path(file_path: str) -> str:
    path_text = str(file_path)
    if "\\" in path_text:
        return PureWindowsPath(path_text).name
    return Path(path_text).name


def number_rule_value(rule: Mapping[str, Any], names: Iterable[str]) -> Optional[float]:
    for name in names:
        if name not in rule or rule[name] is None:
            continue
        return float(rule[name])
    return None


def integer_rule_value(rule: Mapping[str, Any], names: Iterable[str]) -> Optional[int]:
    for name in names:
        if name not in rule or rule[name] is None:
            continue
        value = int(rule[name])
        return value if value >= 0 else None
    return None


def as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
