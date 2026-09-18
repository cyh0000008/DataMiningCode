from __future__ import annotations

import time
from typing import Any

from _topic_acc_common_scene import (
    extract_topic_acc_series,
    filter_events_started_in_primary,
    load_can_reference_module,
)


METRIC_CATEGORY = "MPI"

_CAN = load_can_reference_module("acc_jerkiness.py")

DEFAULT_SMOOTHING_WINDOW = _CAN.DEFAULT_SMOOTHING_WINDOW
DEFAULT_DEADBAND = _CAN.DEFAULT_DEADBAND
DEFAULT_MIN_OSCILLATION_AMPLITUDE = _CAN.DEFAULT_MIN_OSCILLATION_AMPLITUDE
DEFAULT_MIN_SIGN_FLIPS = _CAN.DEFAULT_MIN_SIGN_FLIPS
DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES = _CAN.DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES
DEFAULT_AMPLITUDE_WINDOW_SECONDS = _CAN.DEFAULT_AMPLITUDE_WINDOW_SECONDS
DEFAULT_MIN_EVENT_DURATION_SECONDS = _CAN.DEFAULT_MIN_EVENT_DURATION_SECONDS
DEFAULT_MAX_EVENT_DURATION_SECONDS = _CAN.DEFAULT_MAX_EVENT_DURATION_SECONDS
DEFAULT_MERGE_GAP_SECONDS = _CAN.DEFAULT_MERGE_GAP_SECONDS
DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS = _CAN.DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS
DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS = _CAN.DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS
DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE = _CAN.DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE
DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT = _CAN.DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT
DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS = _CAN.DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS
DEFAULT_DECEL_JUMP_WINDOW_SECONDS = _CAN.DEFAULT_DECEL_JUMP_WINDOW_SECONDS
DEFAULT_DECEL_JUMP_MIN_DROP = _CAN.DEFAULT_DECEL_JUMP_MIN_DROP
DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS = _CAN.DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS
DEFAULT_DECEL_JUMP_MIN_REBOUND = _CAN.DEFAULT_DECEL_JUMP_MIN_REBOUND
DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE = _CAN.DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE
DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE = _CAN.DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE


def __run_haoran_data_run__(comprehensive_frames: list[dict[str, Any]]) -> tuple[bool, list[str], dict[str, Any]]:
    start_time = time.time()
    result_lines: list[str] = []
    stats: dict[str, Any] = {
        "All Case Count": 0,
        "Bad Case": 0,
        "Severity Score": 0.0,
    }

    result = count_topic_acc_jerkiness_events(comprehensive_frames)
    event_count = int(result["event_count"])
    max_severity = float(result["max_event_severity"])

    stats["All Case Count"] = event_count
    stats["Bad Case"] = event_count
    stats["Severity Score"] = max_severity

    result_lines.append(
        f"[Topic ACC Jerkiness] event_count={event_count}, "
        f"valid_sample_count={int(result['valid_sample_count'])}, "
        f"candidate_sample_count={int(result['candidate_sample_count'])}, "
        f"valid_frame_count={int(result['valid_frame_count'])}, "
        f"segment_start_ms={float(result['segment_start_ms']):.3f}, "
        f"max_severity={max_severity:.3f}"
    )
    for event in result["events"]:
        result_lines.append(
            f"[Event type={event.get('detection_type', 'oscillation')}, start={event['start_timestamp']:.3f}, "
            f"end={event['end_timestamp']:.3f}, duration={event['duration']:.3f}, "
            f"sample_count={event['sample_count']}, amplitude={event['amplitude']:.3f}, "
            f"sign_flips={event['sign_flips']}, severity={event['severity']:.3f}]"
        )

    result_lines.insert(0, f"【运算总耗时】: {time.time() - start_time:.4f} 秒")
    return event_count > 0, result_lines, stats


def count_topic_acc_jerkiness_events(
    comprehensive_frames: list[dict[str, Any]],
    smoothing_window: int = DEFAULT_SMOOTHING_WINDOW,
    deadband: float = DEFAULT_DEADBAND,
    min_oscillation_amplitude: float = DEFAULT_MIN_OSCILLATION_AMPLITUDE,
    min_sign_flips: int = DEFAULT_MIN_SIGN_FLIPS,
    sign_flip_min_stable_samples: int = DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES,
    amplitude_window_seconds: float = DEFAULT_AMPLITUDE_WINDOW_SECONDS,
    min_event_duration_seconds: float = DEFAULT_MIN_EVENT_DURATION_SECONDS,
    max_event_duration_seconds: float = DEFAULT_MAX_EVENT_DURATION_SECONDS,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    long_pattern_min_phase_seconds: float = DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS,
    long_pattern_max_phase_seconds: float = DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS,
    long_pattern_min_phase_amplitude: float = DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE,
    long_pattern_min_phase_count: int = DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT,
    long_pattern_max_gap_seconds: float = DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS,
    decel_jump_window_seconds: float = DEFAULT_DECEL_JUMP_WINDOW_SECONDS,
    decel_jump_min_drop: float = DEFAULT_DECEL_JUMP_MIN_DROP,
    decel_jump_rebound_window_seconds: float = DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS,
    decel_jump_min_rebound: float = DEFAULT_DECEL_JUMP_MIN_REBOUND,
    decel_jump_min_jump_slope: float = DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE,
    decel_jump_min_rebound_slope: float = DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE,
) -> dict[str, object]:
    series = extract_topic_acc_series(comprehensive_frames, require_speed=True, use_slope_compensation=True)
    axis = series["axis"]
    accel_values = series["acceleration"]
    if len(axis) < 3:
        return _empty_result(series)

    valid_mask = [
        (not bool(override)) and bool(acc_on) and float(vehicle_speed) > 0.0
        for vehicle_speed, acc_on, override in zip(series["speed"], series["acc_active"], series["pedal_override"])
    ]

    smoothed_acceleration = _CAN._moving_average(accel_values, smoothing_window)
    slope: list[float] = []
    for index in range(1, len(smoothed_acceleration)):
        dt = axis[index] - axis[index - 1]
        slope.append(0.0 if dt <= 0 else (smoothed_acceleration[index] - smoothed_acceleration[index - 1]) / dt)

    candidate_timestamps = axis[1:]
    candidate_mask = [valid_mask[index] and valid_mask[index - 1] for index in range(1, len(axis))]
    valid_sample_count = sum(candidate_mask)

    original_candidate_mask = _CAN._detect_oscillation_candidates(
        candidate_timestamps,
        slope,
        candidate_mask,
        smoothed_acceleration[1:],
        deadband,
        min_oscillation_amplitude,
        min_sign_flips,
        sign_flip_min_stable_samples,
        amplitude_window_seconds,
        min_event_duration_seconds,
        max_event_duration_seconds,
    )
    original_event_mask = _CAN._merge_short_gaps(candidate_timestamps, original_candidate_mask, merge_gap_seconds)
    original_events = _CAN._build_event_details(
        candidate_timestamps,
        original_event_mask,
        smoothed_acceleration[1:],
        slope,
        deadband,
        min_oscillation_amplitude,
        min_sign_flips,
        sign_flip_min_stable_samples,
        amplitude_window_seconds,
        min_event_duration_seconds,
        max_event_duration_seconds,
        detection_type="oscillation",
    )

    long_cycle_events = _CAN._detect_long_cycle_events(
        timestamps=candidate_timestamps,
        smoothed_acceleration=smoothed_acceleration[1:],
        slope=slope,
        valid_mask=candidate_mask,
        deadband=deadband,
        min_phase_seconds=long_pattern_min_phase_seconds,
        max_phase_seconds=long_pattern_max_phase_seconds,
        min_phase_amplitude=long_pattern_min_phase_amplitude,
        min_phase_count=long_pattern_min_phase_count,
        max_gap_seconds=long_pattern_max_gap_seconds,
        min_oscillation_amplitude=min_oscillation_amplitude,
        min_sign_flips=min_sign_flips,
    )
    decel_jump_events = _CAN._detect_decel_jump_events(
        timestamps=candidate_timestamps,
        smoothed_acceleration=smoothed_acceleration[1:],
        valid_mask=candidate_mask,
        min_oscillation_amplitude=min_oscillation_amplitude,
        min_sign_flips=min_sign_flips,
        window_seconds=decel_jump_window_seconds,
        min_drop=decel_jump_min_drop,
        rebound_window_seconds=decel_jump_rebound_window_seconds,
        min_rebound=decel_jump_min_rebound,
        min_jump_slope=decel_jump_min_jump_slope,
        min_rebound_slope=decel_jump_min_rebound_slope,
    )

    events = _CAN._merge_event_details(
        original_events + long_cycle_events + decel_jump_events,
        candidate_timestamps,
        smoothed_acceleration[1:],
        slope,
        deadband,
        min_oscillation_amplitude,
        min_sign_flips,
        sign_flip_min_stable_samples,
        merge_gap_seconds,
    )
    events = _CAN._filter_events_by_reversal_pattern(
        events=events,
        timestamps=candidate_timestamps,
        smoothed_acceleration=smoothed_acceleration[1:],
        jump_window_seconds=decel_jump_window_seconds,
        min_jump_amplitude=decel_jump_min_drop,
        rebound_window_seconds=decel_jump_rebound_window_seconds,
        min_rebound=decel_jump_min_rebound,
        min_jump_slope=decel_jump_min_jump_slope,
        min_rebound_slope=decel_jump_min_rebound_slope,
    )
    events = filter_events_started_in_primary(events, float(series["primary_end_seconds"]))
    merged_event_mask = _CAN._events_to_mask(candidate_timestamps, events)
    max_event_severity = max((float(event["severity"]) for event in events), default=0.0)

    return {
        "metric_name": "topic_acc_jerkiness",
        "method": "topic_oscillation_based_or_extended",
        "event_count": len(events),
        "valid_sample_count": valid_sample_count,
        "candidate_sample_count": sum(merged_event_mask),
        "total_sample_count": len(candidate_timestamps),
        "valid_frame_count": int(series["valid_frame_count"]),
        "segment_start_ms": float(series["segment_start_ms"]),
        "start_timestamp": axis[0],
        "end_timestamp": axis[-1],
        "max_event_severity": max_event_severity,
        "events": events,
    }


def _empty_result(series: dict[str, Any]) -> dict[str, object]:
    return {
        "metric_name": "topic_acc_jerkiness",
        "method": "topic_oscillation_based_or_extended",
        "event_count": 0,
        "valid_sample_count": 0,
        "candidate_sample_count": 0,
        "total_sample_count": 0,
        "valid_frame_count": int(series.get("valid_frame_count", 0)),
        "segment_start_ms": float(series.get("segment_start_ms", 0.0)),
        "start_timestamp": 0.0,
        "end_timestamp": 0.0,
        "max_event_severity": 0.0,
        "events": [],
    }
