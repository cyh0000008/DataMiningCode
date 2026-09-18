from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from bad_case_report import write_bad_case_report
from metric_utils import BaseMetric, metric_required_fields
from reporting import StreamingReportWriter
from signal_preprocessor import (
    SignalFrame,
    build_signal_frame,
    collect_h5_files,
    iter_signal_paths,
    raw_dependencies_for_field,
    stitch_signal_frames,
)


METRIC_SIGNAL_SCOPE_ALL = "all"
METRIC_SIGNAL_SCOPE_DEBUGCAN_ONLY = "debugcan_only"
METRIC_SIGNAL_SCOPE_PCAN_CCAN_ONLY = "pcan_ccan_only"
VALID_METRIC_SIGNAL_SCOPES = {
    METRIC_SIGNAL_SCOPE_ALL,
    METRIC_SIGNAL_SCOPE_DEBUGCAN_ONLY,
    METRIC_SIGNAL_SCOPE_PCAN_CCAN_ONLY,
}
NON_SIGNAL_FIELDS = {"time_s", "time_ms"}


def main(argv: List[str]) -> int:
    base_dir = Path(__file__).resolve().parent
    config_path = Path(argv[1]) if len(argv) > 1 else base_dir / "config.json"
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    config = load_config(config_path)

    input_paths = resolve_paths(config_path.parent, config.get("input_path"))
    filter_dir = base_dir / "filters"
    output_dir = resolve_path(config_path.parent, config.get("output_dir", "Result_Test"))
    date_range = normalize_date_range(config.get("date", ""))
    date_label = date_range or "ALL"
    report_name = build_report_name(str(config.get("report_name", config.get("cpi_filename", "Longitudinal_CPI_Report"))))

    h5_files = collect_h5_files(input_paths)
    h5_files = filter_files_by_date(h5_files, date_range)
    if not h5_files:
        raise FileNotFoundError("No .h5/.hdf5 files found under %s in date filter %s" % (
            ", ".join(str(path) for path in input_paths),
            date_label,
        ))

    metric_signal_scope = normalize_metric_signal_scope(config.get("metric_signal_scope", METRIC_SIGNAL_SCOPE_ALL))
    metrics = load_metrics(filter_dir, config.get("enabled_metrics", ["all"]), config, metric_signal_scope)
    if not metrics:
        raise RuntimeError(
            "No metric filters loaded from %s with enabled_metrics=%s metric_signal_scope=%s"
            % (filter_dir, config.get("enabled_metrics", ["all"]), metric_signal_scope)
        )

    print("Loaded %d metric filters" % len(metrics))
    print("Metric signal scope: %s" % metric_signal_scope)
    print("Scanning input path(s): %s" % ", ".join(str(path) for path in input_paths))
    print("Found %d H5 file(s) in date filter %s" % (len(h5_files), date_label))

    needs_stitched_frames = any(metric_uses_scene(metric) for metric in metrics)
    stitched_field_names = required_fields_for_metrics(metric for metric in metrics if metric_uses_scene(metric))
    frame_cache: Dict[int, SignalFrame] = {}
    continuous_next_flags = [False] * len(h5_files)
    stitch_max_gap_s = float(config.get("defaults", {}).get("h5_stitch_max_gap_s", 5.0))

    def get_frame(index: int) -> SignalFrame:
        if index not in frame_cache:
            frame_cache[index] = build_signal_frame(h5_files[index], config)
        return frame_cache[index]

    actual_output_dir = output_dir
    with StreamingReportWriter(output_dir, report_name, metrics) as writer:
        actual_output_dir = writer.output_dir
        for file_index, h5_file in enumerate(h5_files, 1):
            current_index = file_index - 1
            print("[%d/%d] preprocessing %s" % (file_index, len(h5_files), h5_file))
            frame = get_frame(current_index)
            has_continuous_previous = current_index > 0 and continuous_next_flags[current_index - 1]
            frame.has_continuous_previous = has_continuous_previous

            stitched_frame = None
            if needs_stitched_frames and current_index + 1 < len(h5_files):
                is_continuous, next_offset_s, boundary_gap_s = h5_files_are_continuous(
                    h5_files[current_index],
                    h5_files[current_index + 1],
                    frame,
                    stitch_max_gap_s,
                )
                continuous_next_flags[current_index] = is_continuous
                if is_continuous:
                    next_frame = get_frame(current_index + 1)
                    stitched_frame = stitch_signal_frames(
                        frame,
                        next_frame,
                        next_offset_s,
                        has_continuous_previous=has_continuous_previous,
                        field_names=stitched_field_names,
                    )
                    print(
                        "  stitching next H5 for eligible metrics: %s gap=%.3fs" % (
                            h5_files[current_index + 1],
                            boundary_gap_s,
                        )
                    )

            for metric in metrics:
                metric_frame = stitched_frame if stitched_frame is not None and metric_uses_scene(metric) else frame
                result = metric.run(metric_frame)
                writer.write_result(metric.name, result)
                status = result.status or ("BAD" if result.bad_cases else "OK")
                if result.error and not result.status:
                    status = "ERR"
                print("  [%-3s] %-48s %s total=%d bad=%d severity=%.3f runtime=%.3fs" % (
                    metric.category,
                    metric.name,
                    status,
                    result.total_cases,
                    result.bad_cases,
                    result.severity_score,
                    result.runtime_s,
                ))
            frame_cache.pop(current_index - 1, None)
        report_path = writer.close()
    write_run_manifest(actual_output_dir, config_path, input_paths, date_range, metric_signal_scope, h5_files, metrics)
    bad_case_config_path = resolve_path(config_path.parent, config.get("bad_case_extract_config", "bad_case_extract.json"))
    bad_case_report_path = write_bad_case_report(actual_output_dir, bad_case_config_path, metrics)
    print("Report written: %s" % report_path)
    if bad_case_report_path is not None:
        print("Bad case report written: %s" % bad_case_report_path)
    return 0


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError("Config file not found: %s" % config_path)
    with config_path.open("r", encoding="utf-8") as fp:
        config = json.load(fp)
    if "signals" not in config:
        raise KeyError("config.json must contain a flat 'signals' mapping")
    if "input_path" not in config:
        raise KeyError("config.json must contain 'input_path'")
    date_range = normalize_date_range(config.get("date", ""))
    if date_range:
        parse_date_range(date_range)
    config["metric_signal_scope"] = normalize_metric_signal_scope(config.get("metric_signal_scope", METRIC_SIGNAL_SCOPE_ALL))
    return config


def normalize_date_range(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_metric_signal_scope(value: Any) -> str:
    if value is None:
        return METRIC_SIGNAL_SCOPE_ALL
    scope = str(value).strip().lower()
    aliases = {
        "": METRIC_SIGNAL_SCOPE_ALL,
        "debugcan": METRIC_SIGNAL_SCOPE_DEBUGCAN_ONLY,
        "debug_can": METRIC_SIGNAL_SCOPE_DEBUGCAN_ONLY,
        "pure_debugcan": METRIC_SIGNAL_SCOPE_DEBUGCAN_ONLY,
        "pcan_ccan": METRIC_SIGNAL_SCOPE_PCAN_CCAN_ONLY,
        "pcan/ccan": METRIC_SIGNAL_SCOPE_PCAN_CCAN_ONLY,
        "pure_pcan_ccan": METRIC_SIGNAL_SCOPE_PCAN_CCAN_ONLY,
    }
    scope = aliases.get(scope, scope)
    if scope not in VALID_METRIC_SIGNAL_SCOPES:
        raise ValueError(
            "metric_signal_scope must be one of %s, got: %s"
            % (sorted(VALID_METRIC_SIGNAL_SCOPES), value)
        )
    return scope


def resolve_path(base_dir: Path, value: Any) -> Path:
    if value is None:
        raise ValueError("Path value is required")
    path = Path(str(value))
    return path if path.is_absolute() else (base_dir / path).resolve()


def resolve_paths(base_dir: Path, value: Any) -> List[Path]:
    if value is None:
        raise ValueError("Path value is required")
    if isinstance(value, list):
        if not value:
            raise ValueError("Path list must not be empty")
        return [resolve_path(base_dir, item) for item in value]
    return [resolve_path(base_dir, value)]


def build_report_name(prefix: str) -> str:
    stem = prefix[:-5] if prefix.lower().endswith(".xlsx") else prefix
    return "%s_%s.xlsx" % (stem, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))


def filter_files_by_date(h5_files: List[str], date_range: str) -> List[str]:
    if not date_range:
        selected = list(h5_files)
        selected.sort(key=file_chronological_sort_key)
        return selected

    start_date, end_date = parse_date_range(date_range)
    selected = []
    for file_path in h5_files:
        file_date = extract_date_from_path(file_path)
        if file_date is None:
            continue
        if start_date <= file_date <= end_date:
            selected.append(file_path)
    selected.sort(key=file_chronological_sort_key)
    return selected


def file_chronological_sort_key(file_path: str) -> Tuple[datetime, str]:
    file_name = Path(file_path).name
    file_dt = extract_datetime_from_filename(file_name)
    if file_dt is not None:
        return (file_dt, file_path)
    file_date = extract_date_from_filename(file_name)
    if file_date is None:
        file_date = extract_date_from_path(file_path)
    if file_date is not None:
        return (datetime.combine(file_date, datetime.min.time()), file_path)
    return (datetime.min, file_path)


def parse_date_range(date_range: str) -> Tuple[date, date]:
    if not re.fullmatch(r"\d{8}-\d{8}", date_range):
        raise ValueError("date must be in YYYYMMDD-YYYYMMDD format, got: %s" % date_range)
    start_text, end_text = date_range.split("-", 1)
    start_date = datetime.strptime(start_text, "%Y%m%d").date()
    end_date = datetime.strptime(end_text, "%Y%m%d").date()
    if start_date > end_date:
        raise ValueError("date start must not be later than date end: %s" % date_range)
    return start_date, end_date


def extract_date_from_filename(file_name: str) -> Optional[date]:
    patterns = [
        r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, file_name)
        if not match:
            continue
        year, month, day = (int(item) for item in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def extract_date_from_path(file_path: str | Path) -> Optional[date]:
    """Return the nearest dated parent folder, falling back to the H5 name."""
    path = Path(file_path)
    for parent in path.parents:
        parent_date = extract_date_from_filename(parent.name)
        if parent_date is not None:
            return parent_date
    return extract_date_from_filename(path.name)


def extract_datetime_from_filename(file_name: str) -> Optional[datetime]:
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


def h5_files_are_continuous(
    current_file: str,
    next_file: str,
    current_frame: SignalFrame,
    max_gap_s: float,
) -> Tuple[bool, float, float]:
    current_dt = extract_datetime_from_filename(Path(current_file).name)
    next_dt = extract_datetime_from_filename(Path(next_file).name)
    if current_dt is None or next_dt is None or current_frame.length < 2:
        return False, 0.0, 0.0
    next_offset_s = (next_dt - current_dt).total_seconds()
    if next_offset_s <= 0.0:
        return False, next_offset_s, 0.0
    current_duration_s = float(current_frame.time_s[-1] - current_frame.time_s[0])
    boundary_gap_s = next_offset_s - current_duration_s
    return abs(boundary_gap_s) <= float(max_gap_s), next_offset_s, boundary_gap_s


def metric_uses_scene(metric: BaseMetric) -> bool:
    return bool(getattr(metric, "scene", None) or getattr(metric, "uses_h5_stitching", False))


def required_fields_for_metrics(metrics: Iterable[BaseMetric]) -> List[str]:
    fields: List[str] = ["time_s", "time_ms"]
    for metric in metrics:
        fields.extend(metric_required_fields(metric))
    return list(dict.fromkeys(fields))


def load_metrics(
    filter_dir: Path,
    enabled: Any,
    config: Optional[Mapping[str, Any]] = None,
    metric_signal_scope: str = METRIC_SIGNAL_SCOPE_ALL,
) -> List[BaseMetric]:
    if not filter_dir.is_dir():
        raise FileNotFoundError("Filter folder not found: %s" % filter_dir)
    enabled_set = None
    if isinstance(enabled, list) and enabled and "all" not in enabled:
        enabled_set = set(str(item) for item in enabled)
    metric_signal_scope = normalize_metric_signal_scope(metric_signal_scope)

    metrics: List[BaseMetric] = []
    for file_path in sorted(filter_dir.rglob("*.py")):
        if file_path.name.startswith("_"):
            continue
        metric_name = file_path.stem
        if enabled_set is not None and metric_name not in enabled_set:
            continue
        module = import_module_from_path(file_path)
        if not hasattr(module, "create_metric"):
            raise AttributeError("Filter file %s must define create_metric()" % file_path)
        metric = module.create_metric()
        if not isinstance(metric, BaseMetric):
            raise TypeError("create_metric() in %s did not return BaseMetric" % file_path)
        if not metric_matches_signal_scope(metric, config, metric_signal_scope):
            continue
        metrics.append(metric)
    return metrics


def metric_matches_signal_scope(
    metric: BaseMetric,
    config: Optional[Mapping[str, Any]],
    metric_signal_scope: str,
) -> bool:
    if metric_signal_scope == METRIC_SIGNAL_SCOPE_ALL:
        return True
    if config is None:
        return False
    return classify_metric_signal_scope(metric, config) == metric_signal_scope


def classify_metric_signal_scope(metric: BaseMetric, config: Mapping[str, Any]) -> str:
    kinds = metric_signal_kinds(metric, config)
    if kinds == {"debugcan"}:
        return METRIC_SIGNAL_SCOPE_DEBUGCAN_ONLY
    if kinds == {"pcan_ccan"}:
        return METRIC_SIGNAL_SCOPE_PCAN_CCAN_ONLY
    return "mixed_or_unknown"


def metric_signal_kinds(metric: BaseMetric, config: Mapping[str, Any]) -> set:
    sources = dict(config.get("signals", {}))
    sources.update(dict(config.get("optional_signals", {})))
    kinds = set()
    for field_name in metric_required_fields(metric):
        if field_name in NON_SIGNAL_FIELDS:
            continue
        for raw_field in raw_dependencies_for_field(field_name):
            if raw_field in NON_SIGNAL_FIELDS:
                continue
            configured_paths = sources.get(raw_field)
            if configured_paths in (None, ""):
                continue
            for signal_path in iter_signal_paths(configured_paths):
                kind = signal_path_kind(signal_path)
                if kind != "unknown":
                    kinds.add(kind)
    return kinds


def signal_path_kind(signal_path: str) -> str:
    path = str(signal_path).replace("\\", "/")
    if any(token in path for token in ("Eth_Debug", "DebugCAN", "Debug_CAN", "DebugMsg")):
        return "debugcan"
    if any(token in path for token in ("CAN_PB", "CAN_CB", "PCAN", "CCAN")):
        return "pcan_ccan"
    return "unknown"


def import_module_from_path(file_path: Path) -> Any:
    module_name = "unified_filter_%s" % file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError("Cannot import filter file: %s" % file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def write_run_manifest(
    output_dir: Path,
    config_path: Path,
    input_paths: List[Path],
    date_range: str,
    metric_signal_scope: str,
    h5_files: List[str],
    metrics: List[BaseMetric],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "config_path": str(config_path),
        "input_paths": [str(path) for path in input_paths],
        "date": date_range,
        "metric_signal_scope": metric_signal_scope,
        "h5_files": h5_files,
        "metrics": [{"name": metric.name, "category": metric.category} for metric in metrics],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with (output_dir / "run_manifest.json").open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
