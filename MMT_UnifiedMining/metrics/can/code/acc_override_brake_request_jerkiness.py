from __future__ import annotations

import importlib.util
import time
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
BRAKE_REQUEST_ACCEL_SIGNAL = "Adaptive_Cruise_Command_Ext_1A3_M.IACCBSCE_ACCAccl"
ACCELERATOR_PEDAL_POS_SIGNAL = "PPEI_Engine_General_Status_1_082_M2.IAccActPos"
DEFAULT_BRAKE_REQUEST_ACCEL_THRESHOLD = -0.2

ACCELERATION_SIGNAL = _ACC_JERKINESS.ACCELERATION_SIGNAL
SPEED_SIGNAL = _ACC_JERKINESS.SPEED_SIGNAL
ACC_ACTIVE_SIGNAL = _ACC_JERKINESS.ACC_ACTIVE_SIGNAL
PEDAL_OVERRIDE_SIGNAL = _ACC_JERKINESS.PEDAL_OVERRIDE_SIGNAL

REQUIRED_SIGNALS = list(
    dict.fromkeys(
        list(_ACC_JERKINESS.REQUIRED_SIGNALS)
        + [
            BRAKE_REQUEST_ACCEL_SIGNAL,
            ACCELERATOR_PEDAL_POS_SIGNAL,
        ]
    )
)
SIGNAL_KEYS = dict(_ACC_JERKINESS.SIGNAL_KEYS)
SIGNAL_KEYS["brake_request_accel"] = BRAKE_REQUEST_ACCEL_SIGNAL
SIGNAL_KEYS["accelerator_pedal_pos"] = ACCELERATOR_PEDAL_POS_SIGNAL


DEFAULT_SMOOTHING_WINDOW = _ACC_JERKINESS.DEFAULT_SMOOTHING_WINDOW
DEFAULT_DEADBAND = _ACC_JERKINESS.DEFAULT_DEADBAND
DEFAULT_MIN_OSCILLATION_AMPLITUDE = _ACC_JERKINESS.DEFAULT_MIN_OSCILLATION_AMPLITUDE
DEFAULT_MIN_SIGN_FLIPS = _ACC_JERKINESS.DEFAULT_MIN_SIGN_FLIPS
DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES = _ACC_JERKINESS.DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES
DEFAULT_AMPLITUDE_WINDOW_SECONDS = _ACC_JERKINESS.DEFAULT_AMPLITUDE_WINDOW_SECONDS
DEFAULT_MIN_EVENT_DURATION_SECONDS = _ACC_JERKINESS.DEFAULT_MIN_EVENT_DURATION_SECONDS
DEFAULT_MAX_EVENT_DURATION_SECONDS = _ACC_JERKINESS.DEFAULT_MAX_EVENT_DURATION_SECONDS
DEFAULT_MERGE_GAP_SECONDS = _ACC_JERKINESS.DEFAULT_MERGE_GAP_SECONDS
DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS = _ACC_JERKINESS.DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS
DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS = _ACC_JERKINESS.DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS
DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE = _ACC_JERKINESS.DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE
DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT = _ACC_JERKINESS.DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT
DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS = _ACC_JERKINESS.DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS
DEFAULT_DECEL_JUMP_WINDOW_SECONDS = _ACC_JERKINESS.DEFAULT_DECEL_JUMP_WINDOW_SECONDS
DEFAULT_DECEL_JUMP_MIN_DROP = _ACC_JERKINESS.DEFAULT_DECEL_JUMP_MIN_DROP
DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS = _ACC_JERKINESS.DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS
DEFAULT_DECEL_JUMP_MIN_REBOUND = _ACC_JERKINESS.DEFAULT_DECEL_JUMP_MIN_REBOUND
DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE = _ACC_JERKINESS.DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE
DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE = _ACC_JERKINESS.DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE


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

    result = count_acc_override_brake_request_jerkiness_events(session)
    event_count = int(result["event_count"])
    valid_sample_count = int(result["valid_sample_count"])
    candidate_sample_count = int(result["candidate_sample_count"])
    max_severity = float(result["max_event_severity"])

    stats["All Case Count"] = event_count
    stats["Bad Case"] = event_count
    stats["Severity Score"] = max_severity

    result_lines.append(
        f"[ACC Override Brake Request Jerkiness] file={Path(session.primary_h5_file).name}, "
        f"event_count={event_count}, valid_sample_count={valid_sample_count}, "
        f"candidate_sample_count={candidate_sample_count}, max_severity={max_severity:.3f}"
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


def count_acc_override_brake_request_jerkiness_events(
    session_or_h5_path: FolderSession | str | Path,
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
    brake_request_accel_threshold: float = DEFAULT_BRAKE_REQUEST_ACCEL_THRESHOLD,
    **torque_options: Any,
) -> dict[str, object]:
    session = session_or_h5_path if isinstance(session_or_h5_path, FolderSession) else FolderSession(session_or_h5_path)
    start_timestamp, end_timestamp = session.common_time_range(REQUIRED_SIGNALS)

    axis, accel_values_raw = session.get_signal_slice(SIGNAL_KEYS["acceleration"], start_timestamp, end_timestamp)
    accel_values = [float(value) for value in accel_values_raw]
    if len(axis) < 3:
        return _empty_result(start_timestamp, end_timestamp, brake_request_accel_threshold)

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
    brake_request_accel_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["brake_request_accel"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    accelerator_pedal_pos_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["accelerator_pedal_pos"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )

    valid_mask = [
        _as_bool(acc_on)
        and _as_bool(override)
        and float(brake_request_accel) < brake_request_accel_threshold
        and float(accelerator_pedal_pos) > 0.0
        and float(vehicle_speed) > 0.0
        for acc_on, override, brake_request_accel, accelerator_pedal_pos, vehicle_speed in zip(
            acc_active_values,
            pedal_override_values,
            brake_request_accel_values,
            accelerator_pedal_pos_values,
            speed_values,
        )
    ]

    result = _ACC_JERKINESS.evaluate_acc_jerkiness_from_axis(
        session=session,
        axis=axis,
        accel_values=accel_values,
        valid_mask=valid_mask,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        metric_name="acc_override_brake_request_jerkiness",
        method="acc_on_override_brake_request_torque_gated_jerkiness",
        smoothing_window=smoothing_window,
        deadband=deadband,
        min_oscillation_amplitude=min_oscillation_amplitude,
        min_sign_flips=min_sign_flips,
        sign_flip_min_stable_samples=sign_flip_min_stable_samples,
        amplitude_window_seconds=amplitude_window_seconds,
        min_event_duration_seconds=min_event_duration_seconds,
        max_event_duration_seconds=max_event_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
        long_pattern_min_phase_seconds=long_pattern_min_phase_seconds,
        long_pattern_max_phase_seconds=long_pattern_max_phase_seconds,
        long_pattern_min_phase_amplitude=long_pattern_min_phase_amplitude,
        long_pattern_min_phase_count=long_pattern_min_phase_count,
        long_pattern_max_gap_seconds=long_pattern_max_gap_seconds,
        decel_jump_window_seconds=decel_jump_window_seconds,
        decel_jump_min_drop=decel_jump_min_drop,
        decel_jump_rebound_window_seconds=decel_jump_rebound_window_seconds,
        decel_jump_min_rebound=decel_jump_min_rebound,
        decel_jump_min_jump_slope=decel_jump_min_jump_slope,
        decel_jump_min_rebound_slope=decel_jump_min_rebound_slope,
        **torque_options,
    )
    result = dict(result)
    result["parameters"] = {
        "brake_request_accel_threshold": brake_request_accel_threshold,
        **dict(result["parameters"]),
    }
    return result


def _empty_result(start_timestamp: float, end_timestamp: float, brake_request_accel_threshold: float) -> dict[str, object]:
    return {
        "metric_name": "acc_override_brake_request_jerkiness",
        "method": "acc_on_override_brake_request_torque_gated_jerkiness",
        "event_count": 0,
        "valid_sample_count": 0,
        "candidate_sample_count": 0,
        "total_sample_count": 0,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "max_event_severity": 0.0,
        "parameters": {"brake_request_accel_threshold": brake_request_accel_threshold},
        "events": [],
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in {"0", "false", "none", ""}
