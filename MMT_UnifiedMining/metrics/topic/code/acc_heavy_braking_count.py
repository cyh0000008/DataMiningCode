from __future__ import annotations

import time
from typing import Any

from _topic_acc_common_scene import (
    extract_topic_acc_series,
    filter_events_started_in_primary,
    load_can_reference_module,
)


METRIC_CATEGORY = "MPI"

_CAN = load_can_reference_module("acc_heavy_braking_count.py")

DEFAULT_ACCELERATION_SMOOTHING_WINDOW = _CAN.DEFAULT_ACCELERATION_SMOOTHING_WINDOW
DEFAULT_JERK_SMOOTHING_WINDOW = _CAN.DEFAULT_JERK_SMOOTHING_WINDOW
DEFAULT_HEAVY_BRAKE_ACCEL_THRESHOLD = _CAN.DEFAULT_HEAVY_BRAKE_ACCEL_THRESHOLD
DEFAULT_HEAVY_BRAKE_JERK_THRESHOLD = _CAN.DEFAULT_HEAVY_BRAKE_JERK_THRESHOLD
DEFAULT_DECEL_JUMP_WINDOW_SECONDS = _CAN.DEFAULT_DECEL_JUMP_WINDOW_SECONDS
DEFAULT_DECEL_JUMP_MIN_DROP = _CAN.DEFAULT_DECEL_JUMP_MIN_DROP
DEFAULT_DECEL_JUMP_MIN_SLOPE = _CAN.DEFAULT_DECEL_JUMP_MIN_SLOPE
DEFAULT_HEAVY_BRAKE_MIN_DWELL_SECONDS = _CAN.DEFAULT_HEAVY_BRAKE_MIN_DWELL_SECONDS
DEFAULT_MERGE_GAP_SECONDS = _CAN.DEFAULT_MERGE_GAP_SECONDS


def __run_haoran_data_run__(comprehensive_frames: list[dict[str, Any]]) -> tuple[bool, list[str], dict[str, Any]]:
    start_time = time.time()
    result_lines: list[str] = []
    stats: dict[str, Any] = {
        "All Case Count": 0,
        "Bad Case": 0,
        "Severity Score": 0.0,
    }

    result = count_topic_acc_heavy_braking_events(comprehensive_frames)
    event_count = int(result["event_count"])
    max_severity = float(result["max_event_severity"])

    stats["All Case Count"] = event_count
    stats["Bad Case"] = event_count
    stats["Severity Score"] = max_severity

    result_lines.append(
        f"[Topic ACC Heavy Braking] event_count={event_count}, "
        f"qualified_sample_count={int(result['qualified_sample_count'])}, "
        f"valid_frame_count={int(result['valid_frame_count'])}, "
        f"segment_start_ms={float(result['segment_start_ms']):.3f}, "
        f"max_severity={max_severity:.3f}"
    )
    for event in result["events"]:
        result_lines.append(
            f"[Event start={event['start_timestamp']:.3f}, end={event['end_timestamp']:.3f}, "
            f"duration={event['duration']:.3f}, sample_count={event['sample_count']}, "
            f"start_acceleration={event['start_acceleration']:.3f}, min_acceleration={event['min_acceleration']:.3f}, "
            f"decel_drop={event['decel_drop']:.3f}, decel_slope={event['decel_slope']:.3f}, "
            f"heavy_brake_dwell_seconds={event['heavy_brake_dwell_seconds']:.3f}, "
            f"min_filtered_jerk={event['min_filtered_jerk']:.3f}, "
            f"type={event.get('detection_type', 'primary_decel_jump')}, severity={event['severity']:.3f}]"
        )

    result_lines.insert(0, f"【运算总耗时】: {time.time() - start_time:.4f} 秒")
    return event_count > 0, result_lines, stats


def count_topic_acc_heavy_braking_events(
    comprehensive_frames: list[dict[str, Any]],
    acceleration_smoothing_window: int = DEFAULT_ACCELERATION_SMOOTHING_WINDOW,
    jerk_smoothing_window: int = DEFAULT_JERK_SMOOTHING_WINDOW,
    heavy_brake_accel_threshold: float = DEFAULT_HEAVY_BRAKE_ACCEL_THRESHOLD,
    heavy_brake_jerk_threshold: float = DEFAULT_HEAVY_BRAKE_JERK_THRESHOLD,
    decel_jump_window_seconds: float = DEFAULT_DECEL_JUMP_WINDOW_SECONDS,
    decel_jump_min_drop: float = DEFAULT_DECEL_JUMP_MIN_DROP,
    decel_jump_min_slope: float = DEFAULT_DECEL_JUMP_MIN_SLOPE,
    heavy_brake_min_dwell_seconds: float = DEFAULT_HEAVY_BRAKE_MIN_DWELL_SECONDS,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
) -> dict[str, object]:
    series = extract_topic_acc_series(comprehensive_frames, require_speed=False, use_slope_compensation=True)
    axis = series["axis"]
    accel_values = series["acceleration"]
    if len(axis) < 3:
        return _empty_result(series)

    valid_mask = [
        bool(acc_on) and (not bool(override))
        for acc_on, override in zip(series["acc_active"], series["pedal_override"])
    ]

    smoothed_acc = _CAN._moving_average(accel_values, acceleration_smoothing_window)
    jerk_raw: list[float] = []
    for index in range(1, len(smoothed_acc)):
        dt = axis[index] - axis[index - 1]
        jerk_raw.append(0.0 if dt <= 0 else (smoothed_acc[index] - smoothed_acc[index - 1]) / dt)
    filtered_jerk = _CAN._moving_average(jerk_raw, jerk_smoothing_window)

    candidate_timestamps = axis[1:]
    candidate_valid_mask = [valid_mask[index] and valid_mask[index - 1] for index in range(1, len(axis))]
    accel_candidates = smoothed_acc[1:]
    qualified_sample_count = sum(candidate_valid_mask)

    raw_events = _CAN._detect_heavy_braking_events(
        timestamps=candidate_timestamps,
        valid_mask=candidate_valid_mask,
        accel_values=accel_candidates,
        filtered_jerk=filtered_jerk,
        heavy_brake_accel_threshold=heavy_brake_accel_threshold,
        heavy_brake_jerk_threshold=heavy_brake_jerk_threshold,
        decel_jump_window_seconds=decel_jump_window_seconds,
        decel_jump_min_drop=decel_jump_min_drop,
        decel_jump_min_slope=decel_jump_min_slope,
        heavy_brake_min_dwell_seconds=heavy_brake_min_dwell_seconds,
    )
    events = _CAN._merge_close_events(
        raw_events,
        accel_values=accel_candidates,
        filtered_jerk=filtered_jerk,
        accel_threshold=heavy_brake_accel_threshold,
        jerk_threshold=heavy_brake_jerk_threshold,
        drop_threshold=decel_jump_min_drop,
        slope_threshold=decel_jump_min_slope,
        dwell_threshold=heavy_brake_min_dwell_seconds,
        merge_gap_seconds=merge_gap_seconds,
    )
    events = filter_events_started_in_primary(events, float(series["primary_end_seconds"]))
    max_event_severity = max((float(event["severity"]) for event in events), default=0.0)

    return {
        "metric_name": "topic_acc_heavy_braking_count",
        "method": "topic_heavy_brake_decel_jump_detection",
        "event_count": len(events),
        "qualified_sample_count": qualified_sample_count,
        "valid_frame_count": int(series["valid_frame_count"]),
        "segment_start_ms": float(series["segment_start_ms"]),
        "start_timestamp": axis[0],
        "end_timestamp": axis[-1],
        "max_event_severity": max_event_severity,
        "events": events,
    }


def _empty_result(series: dict[str, Any]) -> dict[str, object]:
    return {
        "metric_name": "topic_acc_heavy_braking_count",
        "method": "topic_heavy_brake_decel_jump_detection",
        "event_count": 0,
        "qualified_sample_count": 0,
        "valid_frame_count": int(series.get("valid_frame_count", 0)),
        "segment_start_ms": float(series.get("segment_start_ms", 0.0)),
        "start_timestamp": 0.0,
        "end_timestamp": 0.0,
        "max_event_severity": 0.0,
        "events": [],
    }
