from __future__ import annotations

import importlib.util
import time
from bisect import bisect_right
from pathlib import Path
from typing import Any

from signal_lib import FolderSession


def _load_acc_jerkiness_module() -> object:
    module_path = Path(__file__).with_name("acc_jerkiness.py")
    spec = importlib.util.spec_from_file_location("_acc_jerkiness_shared", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载共享顿挫算法：{module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ACC_JERKINESS = _load_acc_jerkiness_module()

METRIC_CATEGORY = "MPI"
DEFAULT_START_WINDOW_SECONDS = 1.0

ACCELERATION_SIGNAL = _ACC_JERKINESS.ACCELERATION_SIGNAL
SPEED_SIGNAL = _ACC_JERKINESS.SPEED_SIGNAL
ACC_ACTIVE_SIGNAL = _ACC_JERKINESS.ACC_ACTIVE_SIGNAL
PEDAL_OVERRIDE_SIGNAL = _ACC_JERKINESS.PEDAL_OVERRIDE_SIGNAL
REQUIRED_SIGNALS = list(_ACC_JERKINESS.REQUIRED_SIGNALS)
SIGNAL_KEYS = dict(_ACC_JERKINESS.SIGNAL_KEYS)


def __run_haoran_data_run__(folder_context: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    start_time = time.time()
    result_lines: list[str] = []
    stats: dict[str, Any] = {
        "All Case Count": 0,
        "Bad Case": 0,
        "Severity Score": 0.0,
    }

    session = folder_context.get("session")
    if session is None:
        result_lines.append("当前文件夹下未找到可处理的H5文件")
        result_lines.insert(0, f"【运算总耗时】: {time.time() - start_time:.4f} 秒")
        return False, result_lines, stats

    result = analyze_acc_start_no_override_jerkiness(session)
    all_case_count = int(result["all_case_count"])
    bad_case_count = int(result["bad_case_count"])
    severity_score = float(result["severity_score"])

    stats["All Case Count"] = all_case_count
    stats["Bad Case"] = bad_case_count
    stats["Severity Score"] = severity_score

    result_lines.append(
        f"[ACC Start No Override Jerkiness] file={Path(session.primary_h5_file).name}, "
        f"all_case_count={all_case_count}, bad_case_count={bad_case_count}, "
        f"qualified_sample_count={int(result['qualified_sample_count'])}, severity_score={severity_score:.3f}"
    )
    for scene in result["scenes"]:
        result_lines.append(
            f"[Scene acc_start={scene['acc_start_timestamp']:.3f}, start={scene['start_timestamp']:.3f}, "
            f"end={scene['end_timestamp']:.3f}, duration={scene['duration']:.3f}, "
            f"event_count={scene['event_count']}, severity={scene['severity']:.3f}]"
        )
    for event in result["events"]:
        result_lines.append(
            f"[Event type={event.get('detection_type', 'oscillation')}, start={event['start_timestamp']:.3f}, "
            f"end={event['end_timestamp']:.3f}, duration={event['duration']:.3f}, "
            f"sample_count={event['sample_count']}, amplitude={event['amplitude']:.3f}, "
            f"sign_flips={event['sign_flips']}, severity={event['severity']:.3f}]"
        )

    result_lines.insert(0, f"【运算总耗时】: {time.time() - start_time:.4f} 秒")
    return bad_case_count > 0, result_lines, stats


def analyze_acc_start_no_override_jerkiness(
    session_or_h5_path: FolderSession | str | Path,
    start_window_seconds: float = DEFAULT_START_WINDOW_SECONDS,
    **jerkiness_options: Any,
) -> dict[str, object]:
    session = session_or_h5_path if isinstance(session_or_h5_path, FolderSession) else FolderSession(session_or_h5_path)
    start_timestamp, end_timestamp = session.common_time_range(REQUIRED_SIGNALS)

    axis, accel_values_raw = session.get_signal_slice(SIGNAL_KEYS["acceleration"], start_timestamp, end_timestamp)
    accel_values = [float(value) for value in accel_values_raw]
    if len(axis) < 3:
        return _empty_scene_result(start_timestamp, end_timestamp, start_window_seconds)

    speed_values = [
        float(value)
        for value in session.sample_signal_to_axis(
            SIGNAL_KEYS["speed"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
        )
    ]
    acc_active_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["acc_active"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    pedal_override_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["pedal_override"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )

    scenes = _collect_acc_start_scenes(
        axis=axis,
        acc_active_values=acc_active_values,
        pedal_override_values=pedal_override_values,
        window_seconds=start_window_seconds,
    )
    if not scenes:
        return _empty_scene_result(start_timestamp, end_timestamp, start_window_seconds)

    qualified_mask = [False] * len(axis)
    for scene in scenes:
        for index in range(int(scene["start_index"]), int(scene["end_index"]) + 1):
            qualified_mask[index] = (
                _as_bool(acc_active_values[index])
                and not _as_bool(pedal_override_values[index])
                and speed_values[index] > 0.0
            )

    jerkiness_result = _ACC_JERKINESS.evaluate_acc_jerkiness_from_axis(
        session=session,
        axis=axis,
        accel_values=accel_values,
        valid_mask=qualified_mask,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        metric_name="acc_start_no_override_jerkiness",
        method="acc_start_no_override_torque_gated_jerkiness",
        **jerkiness_options,
    )
    events = list(jerkiness_result["events"])

    bad_case_count = 0
    severity_score = 0.0
    scene_results: list[dict[str, float | int]] = []
    for scene in scenes:
        scene_start = float(scene["start_timestamp"])
        scene_end = float(scene["end_timestamp"])
        overlapping_events = [
            event
            for event in events
            if float(event["end_timestamp"]) >= scene_start and float(event["start_timestamp"]) <= scene_end
        ]
        event_count = len(overlapping_events)
        scene_severity = max((float(event["severity"]) for event in overlapping_events), default=0.0)
        if event_count > 0:
            bad_case_count += 1
            severity_score = max(severity_score, scene_severity)
        scene_results.append(
            {
                "acc_start_timestamp": float(scene["acc_start_timestamp"]),
                "start_timestamp": scene_start,
                "end_timestamp": scene_end,
                "duration": float(scene["duration"]),
                "sample_count": int(scene["sample_count"]),
                "event_count": event_count,
                "severity": round(scene_severity, 3),
            }
        )

    return {
        "metric_name": "acc_start_no_override_jerkiness",
        "all_case_count": len(scene_results),
        "bad_case_count": bad_case_count,
        "severity_score": round(severity_score, 3),
        "qualified_sample_count": int(jerkiness_result["valid_sample_count"]),
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "parameters": {
            "start_window_seconds": start_window_seconds,
            **dict(jerkiness_result["parameters"]),
        },
        "scenes": scene_results,
        "events": events,
    }


def _collect_acc_start_scenes(
    axis: list[float],
    acc_active_values: list[object],
    pedal_override_values: list[object],
    window_seconds: float,
) -> list[dict[str, float | int]]:
    scenes: list[dict[str, float | int]] = []
    if len(axis) < 2 or window_seconds <= 0:
        return scenes

    for index in range(1, len(axis)):
        previous_acc_active = _as_bool(acc_active_values[index - 1])
        current_acc_active = _as_bool(acc_active_values[index])
        if previous_acc_active or not current_acc_active:
            continue

        acc_start_timestamp = float(axis[index])
        window_end_target = acc_start_timestamp + window_seconds
        end_index = bisect_right(axis, window_end_target) - 1
        if end_index <= index:
            continue
        if axis[end_index] < window_end_target:
            continue
        if not all(_as_bool(value) for value in acc_active_values[index : end_index + 1]):
            continue
        if any(_as_bool(value) for value in pedal_override_values[index : end_index + 1]):
            continue

        scenes.append(
            {
                "acc_start_timestamp": acc_start_timestamp,
                "start_timestamp": float(axis[index]),
                "end_timestamp": float(axis[end_index]),
                "duration": float(axis[end_index] - axis[index]),
                "sample_count": end_index - index + 1,
                "start_index": index,
                "end_index": end_index,
            }
        )
    return scenes


def _empty_scene_result(start_timestamp: float, end_timestamp: float, start_window_seconds: float) -> dict[str, object]:
    return {
        "metric_name": "acc_start_no_override_jerkiness",
        "all_case_count": 0,
        "bad_case_count": 0,
        "severity_score": 0.0,
        "qualified_sample_count": 0,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "parameters": {"start_window_seconds": start_window_seconds},
        "scenes": [],
        "events": [],
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in {"0", "false", "none", ""}
