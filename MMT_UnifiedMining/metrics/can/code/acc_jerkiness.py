from __future__ import annotations

import math
import time
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any

from signal_lib import FolderSession, mask_to_segments


METRIC_CATEGORY = "MPI"
ACCELERATION_SIGNAL = "IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec"
SPEED_SIGNAL = "PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1"
ACC_ACTIVE_SIGNAL = "Adaptive_Cruise_Disp_Stat.IACCAct376"
PEDAL_OVERRIDE_SIGNAL = "PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv"
ACTUAL_DRIVE_TORQUE_SIGNAL = "PPEI_Engine_Torque_Status_4_08D_M.IActAxleTrq"
ACTUAL_DRIVE_TORQUE_VALID_SIGNAL = "PPEI_Engine_Torque_Status_4_08D_M.IActAxleTrqV"
ACTUAL_BRAKE_TORQUE_SIGNAL = "PPEI_Chassis_General_Status_2.ICSTBATS_TrqVl"
ACTUAL_BRAKE_TORQUE_VALID_SIGNAL = "PPEI_Chassis_General_Status_2.ICSTBATS_TrqVlV"

DEFAULT_SMOOTHING_WINDOW = 5
DEFAULT_DEADBAND = 0.8
DEFAULT_MIN_OSCILLATION_AMPLITUDE = 0.75
DEFAULT_MIN_SIGN_FLIPS = 1
DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES = 3
DEFAULT_AMPLITUDE_WINDOW_SECONDS = 1.00
DEFAULT_MIN_EVENT_DURATION_SECONDS = 1.00
DEFAULT_MAX_EVENT_DURATION_SECONDS = 4.00
DEFAULT_MERGE_GAP_SECONDS = 0.30
DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS = 1.5
DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS = 3.5
DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE = 0.30
DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT = 4
DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS = 0.60
DEFAULT_DECEL_JUMP_WINDOW_SECONDS = 0.80
DEFAULT_DECEL_JUMP_MIN_DROP = 0.50
DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS = 0.20
DEFAULT_DECEL_JUMP_MIN_REBOUND = 0.50
DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE = 2.2
DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE = 3.0
DEFAULT_TORQUE_PAD_SECONDS = 0.35
DEFAULT_SHORT_ACCELERATION_WINDOW_SECONDS = 0.12
DEFAULT_TREND_ACCELERATION_WINDOW_SECONDS = 0.80
DEFAULT_TORQUE_SMOOTHING_WINDOW_SECONDS = 0.08
DEFAULT_MAX_BASE_EVENT_DURATION_SECONDS = 2.00
DEFAULT_PEAK_MATCH_WINDOW_SECONDS = 0.18
DEFAULT_TRANSIENT_PAD_SECONDS = 0.20
DEFAULT_MIN_POSITIVE_JERK_PEAK = 9.0
DEFAULT_MAX_NEGATIVE_JERK_PEAK = -4.0
DEFAULT_MIN_ABS_JERK_P95 = 3.5
DEFAULT_MIN_HIGHPASS_ACCEL_RMS = 0.10
DEFAULT_MIN_HIGHPASS_ACCEL_PEAK = 0.25
DEFAULT_MIN_DRIVE_TORQUE_RANGE = 80.0
DEFAULT_MIN_BRAKE_TORQUE_RANGE = 120.0
DEFAULT_MIN_DRIVE_RATE_PEAK_NEAR_POSITIVE_JERK = 500.0
DEFAULT_MIN_BRAKE_RATE_PEAK_NEAR_NEGATIVE_JERK = 800.0
DEFAULT_MIN_DIRECTION_SAMPLES = 3


REQUIRED_SIGNALS = [
    ACCELERATION_SIGNAL,
    SPEED_SIGNAL,
    ACC_ACTIVE_SIGNAL,
    PEDAL_OVERRIDE_SIGNAL,
    ACTUAL_DRIVE_TORQUE_SIGNAL,
    ACTUAL_BRAKE_TORQUE_SIGNAL,
]

SIGNAL_KEYS = {
    "acceleration": ACCELERATION_SIGNAL,
    "speed": SPEED_SIGNAL,
    "acc_active": ACC_ACTIVE_SIGNAL,
    "pedal_override": PEDAL_OVERRIDE_SIGNAL,
    "actual_drive_torque": ACTUAL_DRIVE_TORQUE_SIGNAL,
    "actual_drive_torque_valid": ACTUAL_DRIVE_TORQUE_VALID_SIGNAL,
    "actual_brake_torque": ACTUAL_BRAKE_TORQUE_SIGNAL,
    "actual_brake_torque_valid": ACTUAL_BRAKE_TORQUE_VALID_SIGNAL,
}


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

    result = count_acc_jerkiness_events(session)
    event_count = int(result["event_count"])
    valid_sample_count = int(result["valid_sample_count"])
    candidate_sample_count = int(result["candidate_sample_count"])
    max_severity = float(result["max_event_severity"])

    stats["All Case Count"] = event_count
    stats["Bad Case"] = event_count
    stats["Severity Score"] = max_severity

    result_lines.append(
        f"[ACC Jerkiness] file={Path(session.primary_h5_file).name}, event_count={event_count}, "
        f"valid_sample_count={valid_sample_count}, candidate_sample_count={candidate_sample_count}, "
        f"max_severity={max_severity:.3f}"
    )
    if result["events"]:
        for event in result["events"]:
            result_lines.append(
                f"[Event type={event.get('detection_type', 'oscillation')}, start={event['start_timestamp']:.3f}, end={event['end_timestamp']:.3f}, "
                f"duration={event['duration']:.3f}, sample_count={event['sample_count']}, "
                f"amplitude={event['amplitude']:.3f}, sign_flips={event['sign_flips']}, severity={event['severity']:.3f}]"
            )

    bad_case = event_count > 0
    total_time = time.time() - start_time
    result_lines.insert(0, f"【运算总耗时】: {total_time:.4f} 秒")
    return bad_case, result_lines, stats


def count_acc_jerkiness_events(
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
    torque_pad_seconds: float = DEFAULT_TORQUE_PAD_SECONDS,
    short_acceleration_window_seconds: float = DEFAULT_SHORT_ACCELERATION_WINDOW_SECONDS,
    trend_acceleration_window_seconds: float = DEFAULT_TREND_ACCELERATION_WINDOW_SECONDS,
    torque_smoothing_window_seconds: float = DEFAULT_TORQUE_SMOOTHING_WINDOW_SECONDS,
    max_base_event_duration_seconds: float = DEFAULT_MAX_BASE_EVENT_DURATION_SECONDS,
    peak_match_window_seconds: float = DEFAULT_PEAK_MATCH_WINDOW_SECONDS,
    transient_pad_seconds: float = DEFAULT_TRANSIENT_PAD_SECONDS,
    min_positive_jerk_peak: float = DEFAULT_MIN_POSITIVE_JERK_PEAK,
    max_negative_jerk_peak: float = DEFAULT_MAX_NEGATIVE_JERK_PEAK,
    min_abs_jerk_p95: float = DEFAULT_MIN_ABS_JERK_P95,
    min_highpass_accel_rms: float = DEFAULT_MIN_HIGHPASS_ACCEL_RMS,
    min_highpass_accel_peak: float = DEFAULT_MIN_HIGHPASS_ACCEL_PEAK,
    min_drive_torque_range: float = DEFAULT_MIN_DRIVE_TORQUE_RANGE,
    min_brake_torque_range: float = DEFAULT_MIN_BRAKE_TORQUE_RANGE,
    min_drive_rate_peak_near_positive_jerk: float = DEFAULT_MIN_DRIVE_RATE_PEAK_NEAR_POSITIVE_JERK,
    min_brake_rate_peak_near_negative_jerk: float = DEFAULT_MIN_BRAKE_RATE_PEAK_NEAR_NEGATIVE_JERK,
    min_direction_samples: int = DEFAULT_MIN_DIRECTION_SAMPLES,
) -> dict[str, object]:
    session = session_or_h5_path if isinstance(session_or_h5_path, FolderSession) else FolderSession(session_or_h5_path)
    start_timestamp, end_timestamp = session.common_time_range(REQUIRED_SIGNALS)

    axis, accel_values_raw = session.get_signal_slice(SIGNAL_KEYS["acceleration"], start_timestamp, end_timestamp)
    accel_values = [float(value) for value in accel_values_raw]
    if len(axis) < 3:
        return _empty_result(
            start_timestamp,
            end_timestamp,
            0,
            0,
            smoothing_window,
            deadband,
            min_oscillation_amplitude,
            min_sign_flips,
            sign_flip_min_stable_samples,
            amplitude_window_seconds,
            min_event_duration_seconds,
            max_event_duration_seconds,
            merge_gap_seconds,
        )

    speed_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["speed"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    acc_active_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["acc_active"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    pedal_override_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["pedal_override"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )

    valid_mask = [
        (not _as_bool(pedal)) and _as_bool(acc_on) and float(vehicle_speed) > 0.0
        for vehicle_speed, acc_on, pedal in zip(speed_values, acc_active_values, pedal_override_values)
    ]

    return evaluate_acc_jerkiness_from_axis(
        session=session,
        axis=axis,
        accel_values=accel_values,
        valid_mask=valid_mask,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        metric_name="acc_jerkiness",
        method="torque_gated_oscillation_based_or_extended",
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
        torque_pad_seconds=torque_pad_seconds,
        short_acceleration_window_seconds=short_acceleration_window_seconds,
        trend_acceleration_window_seconds=trend_acceleration_window_seconds,
        torque_smoothing_window_seconds=torque_smoothing_window_seconds,
        max_base_event_duration_seconds=max_base_event_duration_seconds,
        peak_match_window_seconds=peak_match_window_seconds,
        transient_pad_seconds=transient_pad_seconds,
        min_positive_jerk_peak=min_positive_jerk_peak,
        max_negative_jerk_peak=max_negative_jerk_peak,
        min_abs_jerk_p95=min_abs_jerk_p95,
        min_highpass_accel_rms=min_highpass_accel_rms,
        min_highpass_accel_peak=min_highpass_accel_peak,
        min_drive_torque_range=min_drive_torque_range,
        min_brake_torque_range=min_brake_torque_range,
        min_drive_rate_peak_near_positive_jerk=min_drive_rate_peak_near_positive_jerk,
        min_brake_rate_peak_near_negative_jerk=min_brake_rate_peak_near_negative_jerk,
        min_direction_samples=min_direction_samples,
    )


def evaluate_acc_jerkiness_from_axis(
    session: FolderSession,
    axis: list[float],
    accel_values: list[float],
    valid_mask: list[bool],
    start_timestamp: float,
    end_timestamp: float,
    metric_name: str,
    method: str,
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
    torque_pad_seconds: float = DEFAULT_TORQUE_PAD_SECONDS,
    short_acceleration_window_seconds: float = DEFAULT_SHORT_ACCELERATION_WINDOW_SECONDS,
    trend_acceleration_window_seconds: float = DEFAULT_TREND_ACCELERATION_WINDOW_SECONDS,
    torque_smoothing_window_seconds: float = DEFAULT_TORQUE_SMOOTHING_WINDOW_SECONDS,
    max_base_event_duration_seconds: float = DEFAULT_MAX_BASE_EVENT_DURATION_SECONDS,
    peak_match_window_seconds: float = DEFAULT_PEAK_MATCH_WINDOW_SECONDS,
    transient_pad_seconds: float = DEFAULT_TRANSIENT_PAD_SECONDS,
    min_positive_jerk_peak: float = DEFAULT_MIN_POSITIVE_JERK_PEAK,
    max_negative_jerk_peak: float = DEFAULT_MAX_NEGATIVE_JERK_PEAK,
    min_abs_jerk_p95: float = DEFAULT_MIN_ABS_JERK_P95,
    min_highpass_accel_rms: float = DEFAULT_MIN_HIGHPASS_ACCEL_RMS,
    min_highpass_accel_peak: float = DEFAULT_MIN_HIGHPASS_ACCEL_PEAK,
    min_drive_torque_range: float = DEFAULT_MIN_DRIVE_TORQUE_RANGE,
    min_brake_torque_range: float = DEFAULT_MIN_BRAKE_TORQUE_RANGE,
    min_drive_rate_peak_near_positive_jerk: float = DEFAULT_MIN_DRIVE_RATE_PEAK_NEAR_POSITIVE_JERK,
    min_brake_rate_peak_near_negative_jerk: float = DEFAULT_MIN_BRAKE_RATE_PEAK_NEAR_NEGATIVE_JERK,
    min_direction_samples: int = DEFAULT_MIN_DIRECTION_SAMPLES,
) -> dict[str, object]:
    parameters = _jerkiness_parameters(
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
        torque_pad_seconds=torque_pad_seconds,
        short_acceleration_window_seconds=short_acceleration_window_seconds,
        trend_acceleration_window_seconds=trend_acceleration_window_seconds,
        torque_smoothing_window_seconds=torque_smoothing_window_seconds,
        max_base_event_duration_seconds=max_base_event_duration_seconds,
        peak_match_window_seconds=peak_match_window_seconds,
        transient_pad_seconds=transient_pad_seconds,
        min_positive_jerk_peak=min_positive_jerk_peak,
        max_negative_jerk_peak=max_negative_jerk_peak,
        min_abs_jerk_p95=min_abs_jerk_p95,
        min_highpass_accel_rms=min_highpass_accel_rms,
        min_highpass_accel_peak=min_highpass_accel_peak,
        min_drive_torque_range=min_drive_torque_range,
        min_brake_torque_range=min_brake_torque_range,
        min_drive_rate_peak_near_positive_jerk=min_drive_rate_peak_near_positive_jerk,
        min_brake_rate_peak_near_negative_jerk=min_brake_rate_peak_near_negative_jerk,
        min_direction_samples=min_direction_samples,
    )
    if len(axis) < 3:
        return _make_jerkiness_result(metric_name, method, 0, 0, [], [], start_timestamp, end_timestamp, parameters)

    usable_length = min(len(axis), len(accel_values), len(valid_mask))
    if usable_length < len(axis):
        axis = axis[:usable_length]
        accel_values = accel_values[:usable_length]
        valid_mask = valid_mask[:usable_length]
    accel_values = [_safe_float(value) for value in accel_values]

    smoothed_acceleration = _moving_average(accel_values, smoothing_window)
    slope = []
    for index in range(1, len(smoothed_acceleration)):
        dt = axis[index] - axis[index - 1]
        slope.append(0.0 if dt <= 0 else (smoothed_acceleration[index] - smoothed_acceleration[index - 1]) / dt)

    candidate_timestamps = axis[1:]
    candidate_mask = [valid_mask[index] and valid_mask[index - 1] for index in range(1, len(axis))]
    valid_sample_count = sum(candidate_mask)

    original_candidate_mask = _detect_oscillation_candidates(
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
    original_event_mask = _merge_short_gaps(candidate_timestamps, original_candidate_mask, merge_gap_seconds)
    original_events = _build_event_details(
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

    long_cycle_events = _detect_long_cycle_events(
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
    decel_jump_events = _detect_decel_jump_events(
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
    base_events = _merge_event_details(
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
    base_events = _filter_events_by_reversal_pattern(
        events=base_events,
        timestamps=candidate_timestamps,
        smoothed_acceleration=smoothed_acceleration[1:],
        jump_window_seconds=decel_jump_window_seconds,
        min_jump_amplitude=decel_jump_min_drop,
        rebound_window_seconds=decel_jump_rebound_window_seconds,
        min_rebound=decel_jump_min_rebound,
        min_jump_slope=decel_jump_min_jump_slope,
        min_rebound_slope=decel_jump_min_rebound_slope,
    )
    if not base_events:
        return _make_jerkiness_result(
            metric_name,
            method,
            valid_sample_count,
            0,
            candidate_timestamps,
            [],
            start_timestamp,
            end_timestamp,
            parameters,
        )

    drive_torque_values, brake_torque_values = sample_torque_signals_to_axis(
        session=session,
        axis=axis,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    events = _filter_events_by_torque_match(
        axis=axis,
        base_events=base_events,
        acceleration_values=accel_values,
        drive_torque_values=drive_torque_values,
        brake_torque_values=brake_torque_values,
        torque_pad_seconds=torque_pad_seconds,
        short_acceleration_window_seconds=short_acceleration_window_seconds,
        trend_acceleration_window_seconds=trend_acceleration_window_seconds,
        torque_smoothing_window_seconds=torque_smoothing_window_seconds,
        max_base_event_duration_seconds=max_base_event_duration_seconds,
        peak_match_window_seconds=peak_match_window_seconds,
        transient_pad_seconds=transient_pad_seconds,
        min_positive_jerk_peak=min_positive_jerk_peak,
        max_negative_jerk_peak=max_negative_jerk_peak,
        min_abs_jerk_p95=min_abs_jerk_p95,
        min_highpass_accel_rms=min_highpass_accel_rms,
        min_highpass_accel_peak=min_highpass_accel_peak,
        min_drive_torque_range=min_drive_torque_range,
        min_brake_torque_range=min_brake_torque_range,
        min_drive_rate_peak_near_positive_jerk=min_drive_rate_peak_near_positive_jerk,
        min_brake_rate_peak_near_negative_jerk=min_brake_rate_peak_near_negative_jerk,
        min_direction_samples=min_direction_samples,
    )
    merged_event_mask = _events_to_mask(candidate_timestamps, events)
    return _make_jerkiness_result(
        metric_name,
        method,
        valid_sample_count,
        sum(merged_event_mask),
        candidate_timestamps,
        events,
        start_timestamp,
        end_timestamp,
        parameters,
    )


def sample_torque_signals_to_axis(
    session: FolderSession,
    axis: list[float],
    start_timestamp: float,
    end_timestamp: float,
) -> tuple[list[float], list[float]]:
    drive_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["actual_drive_torque"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    brake_values = session.sample_signal_to_axis(
        SIGNAL_KEYS["actual_brake_torque"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    if session.has_signal(SIGNAL_KEYS["actual_drive_torque_valid"]):
        drive_valid_values = session.sample_signal_to_axis(
            SIGNAL_KEYS["actual_drive_torque_valid"],
            axis,
            method="nearest",
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
    else:
        drive_valid_values = [0.0] * len(axis)
    if session.has_signal(SIGNAL_KEYS["actual_brake_torque_valid"]):
        brake_valid_values = session.sample_signal_to_axis(
            SIGNAL_KEYS["actual_brake_torque_valid"],
            axis,
            method="nearest",
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
    else:
        brake_valid_values = [0.0] * len(axis)
    return (
        [_safe_float(value) if _is_signal_valid(valid) else float("nan") for value, valid in zip(drive_values, drive_valid_values)],
        [_safe_float(value) if _is_signal_valid(valid) else float("nan") for value, valid in zip(brake_values, brake_valid_values)],
    )


def _filter_events_by_torque_match(
    axis: list[float],
    base_events: list[dict[str, float | int | str]],
    acceleration_values: list[float],
    drive_torque_values: list[float],
    brake_torque_values: list[float],
    torque_pad_seconds: float,
    short_acceleration_window_seconds: float,
    trend_acceleration_window_seconds: float,
    torque_smoothing_window_seconds: float,
    max_base_event_duration_seconds: float,
    peak_match_window_seconds: float,
    transient_pad_seconds: float,
    min_positive_jerk_peak: float,
    max_negative_jerk_peak: float,
    min_abs_jerk_p95: float,
    min_highpass_accel_rms: float,
    min_highpass_accel_peak: float,
    min_drive_torque_range: float,
    min_brake_torque_range: float,
    min_drive_rate_peak_near_positive_jerk: float,
    min_brake_rate_peak_near_negative_jerk: float,
    min_direction_samples: int,
) -> list[dict[str, float | int | str]]:
    acceleration_smooth = _time_window_moving_average(axis, acceleration_values, short_acceleration_window_seconds)
    acceleration_trend = _time_window_moving_average(axis, acceleration_values, trend_acceleration_window_seconds)
    acceleration_highpass = [
        smooth - trend if _is_finite(smooth) and _is_finite(trend) else float("nan")
        for smooth, trend in zip(acceleration_smooth, acceleration_trend)
    ]
    jerk = _derivative(axis, acceleration_smooth)
    drive_torque = _time_window_moving_average(axis, drive_torque_values, torque_smoothing_window_seconds)
    brake_torque = _time_window_moving_average(axis, brake_torque_values, torque_smoothing_window_seconds)
    drive_rate = _derivative(axis, drive_torque)
    brake_rate = _derivative(axis, brake_torque)

    torque_events: list[dict[str, float | int | str]] = []
    for base_event in base_events:
        base_start = bisect_left(axis, float(base_event["start_timestamp"]))
        base_end = bisect_right(axis, float(base_event["end_timestamp"])) - 1
        if base_start < 0 or base_end <= base_start or base_start >= len(axis):
            continue
        base_end = min(base_end, len(axis) - 1)
        if axis[base_end] - axis[base_start] > max_base_event_duration_seconds:
            continue

        start = max(0, bisect_left(axis, axis[base_start] - torque_pad_seconds))
        end = min(len(axis) - 1, bisect_right(axis, axis[base_end] + torque_pad_seconds) - 1)
        if end <= start:
            continue

        window_indices = list(range(start, end + 1))
        finite_indices = [
            index
            for index in window_indices
            if _is_finite(acceleration_smooth[index])
            and _is_finite(acceleration_highpass[index])
            and _is_finite(jerk[index])
            and _is_finite(drive_torque[index])
            and _is_finite(brake_torque[index])
            and _is_finite(drive_rate[index])
            and _is_finite(brake_rate[index])
        ]
        if len(finite_indices) < max(1, int(min_direction_samples)) * 2:
            continue

        drive_torque_range = _finite_range(drive_torque[start : end + 1])
        brake_torque_range = _finite_range(brake_torque[start : end + 1])
        if drive_torque_range < min_drive_torque_range or brake_torque_range < min_brake_torque_range:
            continue

        positive_jerk_index = max(finite_indices, key=lambda index: jerk[index])
        negative_jerk_index = min(finite_indices, key=lambda index: jerk[index])
        positive_jerk_peak = float(jerk[positive_jerk_index])
        negative_jerk_peak = float(jerk[negative_jerk_index])
        if positive_jerk_peak < min_positive_jerk_peak or negative_jerk_peak > max_negative_jerk_peak:
            continue

        abs_jerk_p95 = _finite_percentile([abs(jerk[index]) for index in finite_indices], 95.0)
        highpass_acc_rms = _finite_rms([acceleration_highpass[index] for index in finite_indices])
        highpass_acc_peak = _finite_abs_peak([acceleration_highpass[index] for index in finite_indices])
        if abs_jerk_p95 < min_abs_jerk_p95:
            continue
        if highpass_acc_rms < min_highpass_accel_rms or highpass_acc_peak < min_highpass_accel_peak:
            continue

        drive_rate_peak, drive_rate_lag_s, drive_rate_peak_index = _local_positive_peak_near(
            axis, drive_rate, positive_jerk_index, peak_match_window_seconds
        )
        brake_rate_peak, brake_rate_lag_s, brake_rate_peak_index = _local_positive_peak_near(
            axis, brake_rate, negative_jerk_index, peak_match_window_seconds
        )
        if drive_rate_peak < min_drive_rate_peak_near_positive_jerk:
            continue
        if brake_rate_peak < min_brake_rate_peak_near_negative_jerk:
            continue
        if drive_rate_lag_s > peak_match_window_seconds or brake_rate_lag_s > peak_match_window_seconds:
            continue

        peak_indices = [positive_jerk_index, negative_jerk_index, drive_rate_peak_index, brake_rate_peak_index]
        transient_start = max(0, bisect_left(axis, min(axis[index] for index in peak_indices) - transient_pad_seconds))
        transient_end = min(len(axis) - 1, bisect_right(axis, max(axis[index] for index in peak_indices) + transient_pad_seconds) - 1)
        if transient_end <= transient_start:
            transient_start = min(peak_indices)
            transient_end = max(peak_indices)

        values = {
            "base_event_start_timestamp": round(float(base_event["start_timestamp"]), 6),
            "base_event_end_timestamp": round(float(base_event["end_timestamp"]), 6),
            "torque_window_start_timestamp": round(float(axis[start]), 6),
            "torque_window_end_timestamp": round(float(axis[end]), 6),
            "transient_start_timestamp": round(float(axis[transient_start]), 6),
            "transient_end_timestamp": round(float(axis[transient_end]), 6),
            "base_jerkiness_severity": round(float(base_event["severity"]), 3),
            "acceleration_min": round(_finite_min(acceleration_smooth[start : end + 1]), 6),
            "acceleration_max": round(_finite_max(acceleration_smooth[start : end + 1]), 6),
            "highpass_acc_rms": round(highpass_acc_rms, 6),
            "highpass_acc_peak": round(highpass_acc_peak, 6),
            "positive_jerk_peak": round(positive_jerk_peak, 6),
            "negative_jerk_peak": round(negative_jerk_peak, 6),
            "abs_jerk_p95": round(abs_jerk_p95, 6),
            "positive_jerk_peak_time_s": round(float(axis[positive_jerk_index]), 6),
            "negative_jerk_peak_time_s": round(float(axis[negative_jerk_index]), 6),
            "drive_torque_min": round(_finite_min(drive_torque[start : end + 1]), 3),
            "drive_torque_max": round(_finite_max(drive_torque[start : end + 1]), 3),
            "drive_torque_range": round(drive_torque_range, 3),
            "brake_torque_min": round(_finite_min(brake_torque[start : end + 1]), 3),
            "brake_torque_max": round(_finite_max(brake_torque[start : end + 1]), 3),
            "brake_torque_range": round(brake_torque_range, 3),
            "drive_rate_peak_near_positive_jerk": round(drive_rate_peak, 3),
            "drive_rate_peak_lag_s": round(drive_rate_lag_s, 6),
            "drive_rate_peak_time_s": round(float(axis[drive_rate_peak_index]), 6),
            "brake_rate_peak_near_negative_jerk": round(brake_rate_peak, 3),
            "brake_rate_peak_lag_s": round(brake_rate_lag_s, 6),
            "brake_rate_peak_time_s": round(float(axis[brake_rate_peak_index]), 6),
        }
        severity = round(
            float(base_event["severity"])
            + abs_jerk_p95 * 20.0
            + highpass_acc_peak * 120.0
            + max(drive_rate_peak, 0.0) / 200.0
            + max(brake_rate_peak, 0.0) / 200.0,
            3,
        )
        event = dict(base_event)
        event.update(values)
        event.update(
            {
                "start_timestamp": float(axis[transient_start]),
                "end_timestamp": float(axis[transient_end]),
                "duration": float(axis[transient_end] - axis[transient_start]),
                "sample_count": transient_end - transient_start + 1,
                "severity": severity,
                "detection_type": f"{base_event.get('detection_type', 'oscillation')}_torque_verified",
            }
        )
        torque_events.append(event)
    return torque_events


def _make_jerkiness_result(
    metric_name: str,
    method: str,
    valid_sample_count: int,
    candidate_sample_count: int,
    candidate_timestamps: list[float],
    events: list[dict[str, float | int | str]],
    start_timestamp: float,
    end_timestamp: float,
    parameters: dict[str, float | int],
) -> dict[str, object]:
    return {
        "metric_name": metric_name,
        "method": method,
        "event_count": len(events),
        "valid_sample_count": valid_sample_count,
        "candidate_sample_count": candidate_sample_count,
        "total_sample_count": len(candidate_timestamps),
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "max_event_severity": max((float(event["severity"]) for event in events), default=0.0),
        "parameters": parameters,
        "events": events,
    }


def _jerkiness_parameters(
    smoothing_window: int,
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
    amplitude_window_seconds: float,
    min_event_duration_seconds: float,
    max_event_duration_seconds: float,
    merge_gap_seconds: float,
    long_pattern_min_phase_seconds: float,
    long_pattern_max_phase_seconds: float,
    long_pattern_min_phase_amplitude: float,
    long_pattern_min_phase_count: int,
    long_pattern_max_gap_seconds: float,
    decel_jump_window_seconds: float,
    decel_jump_min_drop: float,
    decel_jump_rebound_window_seconds: float,
    decel_jump_min_rebound: float,
    decel_jump_min_jump_slope: float,
    decel_jump_min_rebound_slope: float,
    torque_pad_seconds: float,
    short_acceleration_window_seconds: float,
    trend_acceleration_window_seconds: float,
    torque_smoothing_window_seconds: float,
    max_base_event_duration_seconds: float,
    peak_match_window_seconds: float,
    transient_pad_seconds: float,
    min_positive_jerk_peak: float,
    max_negative_jerk_peak: float,
    min_abs_jerk_p95: float,
    min_highpass_accel_rms: float,
    min_highpass_accel_peak: float,
    min_drive_torque_range: float,
    min_brake_torque_range: float,
    min_drive_rate_peak_near_positive_jerk: float,
    min_brake_rate_peak_near_negative_jerk: float,
    min_direction_samples: int,
) -> dict[str, float | int]:
    return {
        "smoothing_window": smoothing_window,
        "deadband": deadband,
        "min_oscillation_amplitude": min_oscillation_amplitude,
        "min_sign_flips": min_sign_flips,
        "sign_flip_min_stable_samples": sign_flip_min_stable_samples,
        "amplitude_window_seconds": amplitude_window_seconds,
        "min_event_duration_seconds": min_event_duration_seconds,
        "max_event_duration_seconds": max_event_duration_seconds,
        "merge_gap_seconds": merge_gap_seconds,
        "long_pattern_min_phase_seconds": long_pattern_min_phase_seconds,
        "long_pattern_max_phase_seconds": long_pattern_max_phase_seconds,
        "long_pattern_min_phase_amplitude": long_pattern_min_phase_amplitude,
        "long_pattern_min_phase_count": long_pattern_min_phase_count,
        "long_pattern_max_gap_seconds": long_pattern_max_gap_seconds,
        "decel_jump_window_seconds": decel_jump_window_seconds,
        "decel_jump_min_drop": decel_jump_min_drop,
        "decel_jump_rebound_window_seconds": decel_jump_rebound_window_seconds,
        "decel_jump_min_rebound": decel_jump_min_rebound,
        "decel_jump_min_jump_slope": decel_jump_min_jump_slope,
        "decel_jump_min_rebound_slope": decel_jump_min_rebound_slope,
        "torque_pad_seconds": torque_pad_seconds,
        "short_acceleration_window_seconds": short_acceleration_window_seconds,
        "trend_acceleration_window_seconds": trend_acceleration_window_seconds,
        "torque_smoothing_window_seconds": torque_smoothing_window_seconds,
        "max_base_event_duration_seconds": max_base_event_duration_seconds,
        "peak_match_window_seconds": peak_match_window_seconds,
        "transient_pad_seconds": transient_pad_seconds,
        "min_positive_jerk_peak": min_positive_jerk_peak,
        "max_negative_jerk_peak": max_negative_jerk_peak,
        "min_abs_jerk_p95": min_abs_jerk_p95,
        "min_highpass_accel_rms": min_highpass_accel_rms,
        "min_highpass_accel_peak": min_highpass_accel_peak,
        "min_drive_torque_range": min_drive_torque_range,
        "min_brake_torque_range": min_brake_torque_range,
        "min_drive_rate_peak_near_positive_jerk": min_drive_rate_peak_near_positive_jerk,
        "min_brake_rate_peak_near_negative_jerk": min_brake_rate_peak_near_negative_jerk,
        "min_direction_samples": min_direction_samples,
    }


def _moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1 or len(values) <= 2:
        return values[:]
    radius = max(0, window // 2)
    smoothed: list[float] = []
    for index in range(len(values)):
        left = max(0, index - radius)
        right = min(len(values), index + radius + 1)
        chunk = values[left:right]
        smoothed.append(sum(chunk) / len(chunk))
    return smoothed


def _build_event_details(
    timestamps: list[float],
    event_mask: list[bool],
    smoothed_acceleration: list[float],
    slope: list[float],
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
    amplitude_window_seconds: float,
    min_event_duration_seconds: float,
    max_event_duration_seconds: float,
    detection_type: str = "oscillation",
) -> list[dict[str, float | int | str]]:
    events: list[dict[str, float | int | str]] = []
    for segment in mask_to_segments(timestamps, event_mask):
        start_index = _find_timestamp_index(timestamps, segment.start_timestamp)
        end_index = _find_timestamp_index(timestamps, segment.end_timestamp)
        if start_index < 0 or end_index < start_index:
            continue

        acc_window = smoothed_acceleration[start_index : end_index + 1]
        slope_window = slope[start_index : end_index + 1]
        if not acc_window:
            continue
        if segment.duration < min_event_duration_seconds or segment.duration > max_event_duration_seconds:
            continue

        amplitude = _max_window_amplitude(timestamps, smoothed_acceleration, start_index, end_index, amplitude_window_seconds)
        sign_flips = _count_sign_flips(slope_window, deadband, sign_flip_min_stable_samples)
        severity = _score_event_severity(amplitude, sign_flips, segment.duration, min_oscillation_amplitude, min_sign_flips)
        events.append(
            {
                "start_timestamp": segment.start_timestamp,
                "end_timestamp": segment.end_timestamp,
                "duration": segment.duration,
                "sample_count": segment.sample_count,
                "amplitude": amplitude,
                "sign_flips": sign_flips,
                "severity": severity,
                "detection_type": detection_type,
            }
        )
    return events


def _find_timestamp_index(timestamps: list[float], target: float) -> int:
    for index, value in enumerate(timestamps):
        if value == target:
            return index
    return -1


def _count_sign_flips(values: list[float], deadband: float, min_stable_samples: int = DEFAULT_SIGN_FLIP_MIN_STABLE_SAMPLES) -> int:
    min_stable_samples = max(1, int(min_stable_samples))
    flip_count = 0
    previous_sign = 0
    pending_sign = 0
    pending_count = 0
    for value in values:
        sign = _signed_state(value, deadband)
        if sign == 0:
            continue
        if sign == previous_sign:
            pending_sign = 0
            pending_count = 0
            continue
        if sign != pending_sign:
            pending_sign = sign
            pending_count = 1
            continue
        pending_count += 1
        if pending_count < min_stable_samples:
            continue
        if previous_sign != 0:
            flip_count += 1
        previous_sign = pending_sign
        pending_sign = 0
        pending_count = 0
    return flip_count


def _max_window_amplitude(
    timestamps: list[float],
    values: list[float],
    start_index: int,
    end_index: int,
    window_seconds: float,
) -> float:
    if start_index < 0 or end_index < start_index:
        return 0.0
    if window_seconds <= 0:
        chunk = values[start_index : end_index + 1]
        return max(chunk) - min(chunk) if chunk else 0.0

    max_amplitude = 0.0
    for left in range(start_index, end_index + 1):
        right = left
        while right + 1 <= end_index and timestamps[right + 1] - timestamps[left] <= window_seconds:
            right += 1
        chunk = values[left : right + 1]
        if not chunk:
            continue
        amplitude = max(chunk) - min(chunk)
        if amplitude > max_amplitude:
            max_amplitude = amplitude
    return max_amplitude


def _score_event_severity(
    amplitude: float,
    sign_flips: int,
    duration: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
) -> float:
    if min_oscillation_amplitude <= 0:
        amplitude_ratio = 0.0
    else:
        amplitude_ratio = amplitude / min_oscillation_amplitude
    if min_sign_flips <= 0:
        flip_ratio = 0.0
    else:
        flip_ratio = sign_flips / float(min_sign_flips)
    duration_ratio = duration / 1.0
    return round(max(amplitude_ratio * 50.0 + flip_ratio * 30.0 + duration_ratio * 20.0, 0.0), 3)


def _detect_oscillation_candidates(
    timestamps: list[float],
    slope: list[float],
    valid_mask: list[bool],
    smoothed_acceleration: list[float],
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
    amplitude_window_seconds: float,
    min_event_duration_seconds: float,
    max_event_duration_seconds: float,
) -> list[bool]:
    signs = [_signed_state(value, deadband) for value in slope]
    event_mask = [False] * len(timestamps)
    run_start = None
    flip_count = 0
    previous_sign = 0
    pending_sign = 0
    pending_count = 0
    for index, sign in enumerate(signs):
        is_valid = valid_mask[index]
        if not is_valid:
            _finalize_run(
                event_mask,
                timestamps,
                smoothed_acceleration,
                slope,
                deadband,
                run_start,
                index - 1,
                flip_count,
                min_sign_flips,
                min_oscillation_amplitude,
                sign_flip_min_stable_samples,
                amplitude_window_seconds,
                min_event_duration_seconds,
                max_event_duration_seconds,
            )
            run_start = None
            flip_count = 0
            previous_sign = 0
            pending_sign = 0
            pending_count = 0
            continue
        if sign == 0:
            continue
        if run_start is None:
            run_start = index
            previous_sign = sign
            continue
        if sign == previous_sign:
            pending_sign = 0
            pending_count = 0
            continue
        if sign != pending_sign:
            pending_sign = sign
            pending_count = 1
            continue
        pending_count += 1
        if pending_count < max(1, int(sign_flip_min_stable_samples)):
            continue
        flip_count += 1
        previous_sign = pending_sign
        pending_sign = 0
        pending_count = 0

    _finalize_run(
        event_mask,
        timestamps,
        smoothed_acceleration,
        slope,
        deadband,
        run_start,
        len(timestamps) - 1,
        flip_count,
        min_sign_flips,
        min_oscillation_amplitude,
        sign_flip_min_stable_samples,
        amplitude_window_seconds,
        min_event_duration_seconds,
        max_event_duration_seconds,
    )
    return event_mask


def _finalize_run(
    event_mask: list[bool],
    timestamps: list[float],
    smoothed_acceleration: list[float],
    slope: list[float],
    deadband: float,
    run_start: int | None,
    run_end: int,
    flip_count: int,
    min_sign_flips: int,
    min_oscillation_amplitude: float,
    sign_flip_min_stable_samples: int,
    amplitude_window_seconds: float,
    min_event_duration_seconds: float,
    max_event_duration_seconds: float,
) -> None:
    if run_start is None or run_end < run_start:
        return
    duration = timestamps[run_end] - timestamps[run_start] if run_end > run_start else 0.0
    amplitude = _max_window_amplitude(timestamps, smoothed_acceleration, run_start, run_end, amplitude_window_seconds)
    if flip_count < min_sign_flips or amplitude < min_oscillation_amplitude or duration < min_event_duration_seconds:
        return
    if duration <= max_event_duration_seconds:
        for index in range(run_start, run_end + 1):
            event_mask[index] = True
        return

    best_window = _find_best_subwindow(
        timestamps=timestamps,
        smoothed_acceleration=smoothed_acceleration,
        slope=slope,
        run_start=run_start,
        run_end=run_end,
        deadband=deadband,
        min_oscillation_amplitude=min_oscillation_amplitude,
        min_sign_flips=min_sign_flips,
        sign_flip_min_stable_samples=sign_flip_min_stable_samples,
        amplitude_window_seconds=amplitude_window_seconds,
        min_event_duration_seconds=min_event_duration_seconds,
        max_event_duration_seconds=max_event_duration_seconds,
    )
    if best_window is None:
        return
    best_start, best_end = best_window
    for index in range(best_start, best_end + 1):
        event_mask[index] = True


def _find_best_subwindow(
    timestamps: list[float],
    smoothed_acceleration: list[float],
    slope: list[float],
    run_start: int,
    run_end: int,
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
    amplitude_window_seconds: float,
    min_event_duration_seconds: float,
    max_event_duration_seconds: float,
) -> tuple[int, int] | None:
    best_window: tuple[int, int] | None = None
    best_score = -1.0
    left = run_start
    step_seconds = max(min(min_event_duration_seconds / 2.0, 0.5), 0.1)

    while left <= run_end:
        right = left
        while right + 1 <= run_end and timestamps[right + 1] - timestamps[left] <= max_event_duration_seconds:
            right += 1
        duration = timestamps[right] - timestamps[left] if right > left else 0.0
        if duration >= min_event_duration_seconds:
            amplitude = _max_window_amplitude(timestamps, smoothed_acceleration, left, right, amplitude_window_seconds)
            sign_flips = _count_sign_flips(slope[left : right + 1], deadband, sign_flip_min_stable_samples)
            if amplitude >= min_oscillation_amplitude and sign_flips >= min_sign_flips:
                score = _score_event_severity(amplitude, sign_flips, duration, min_oscillation_amplitude, min_sign_flips)
                if score > best_score:
                    best_score = score
                    best_window = (left, right)

        next_start_time = timestamps[left] + step_seconds
        next_left = left + 1
        while next_left <= run_end and timestamps[next_left] < next_start_time:
            next_left += 1
        if next_left <= left:
            next_left = left + 1
        left = next_left

    return best_window


def _detect_long_cycle_events(
    timestamps: list[float],
    smoothed_acceleration: list[float],
    slope: list[float],
    valid_mask: list[bool],
    deadband: float,
    min_phase_seconds: float,
    max_phase_seconds: float,
    min_phase_amplitude: float,
    min_phase_count: int,
    max_gap_seconds: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
) -> list[dict[str, float | int | str]]:
    phases = _collect_signed_phases(
        timestamps,
        smoothed_acceleration,
        slope,
        valid_mask,
        deadband,
        min_phase_seconds,
        max_phase_seconds,
        min_phase_amplitude,
    )
    if len(phases) < min_phase_count:
        return []

    events: list[dict[str, float | int | str]] = []
    index = 0
    while index <= len(phases) - min_phase_count:
        end_index = index
        if not _phases_can_chain(phases[index], phases[index + 1], max_gap_seconds):
            index += 1
            continue
        while end_index + 1 < len(phases):
            if end_index > index and not _phases_can_chain(phases[end_index], phases[end_index + 1], max_gap_seconds):
                break
            if end_index == index:
                end_index += 1
                continue
            end_index += 1
        sequence = phases[index : end_index + 1]
        if len(sequence) < min_phase_count:
            index += 1
            continue
        if not _is_alternating_sequence(sequence, max_gap_seconds):
            index += 1
            continue

        start_idx = int(sequence[0]["start_index"])
        end_idx = int(sequence[-1]["end_index"])
        acc_window = smoothed_acceleration[start_idx : end_idx + 1]
        amplitude = max(acc_window) - min(acc_window) if acc_window else 0.0
        sign_flips = len(sequence) - 1
        duration = timestamps[end_idx] - timestamps[start_idx] if end_idx > start_idx else 0.0
        severity = _score_event_severity(amplitude, sign_flips, duration, min_oscillation_amplitude, min_sign_flips)
        events.append(
            {
                "start_timestamp": timestamps[start_idx],
                "end_timestamp": timestamps[end_idx],
                "duration": duration,
                "sample_count": end_idx - start_idx + 1,
                "amplitude": amplitude,
                "sign_flips": sign_flips,
                "severity": severity,
                "detection_type": "long_cycle",
            }
        )
        index = end_index + 1
    return events


def _collect_signed_phases(
    timestamps: list[float],
    smoothed_acceleration: list[float],
    slope: list[float],
    valid_mask: list[bool],
    deadband: float,
    min_phase_seconds: float,
    max_phase_seconds: float,
    min_phase_amplitude: float,
) -> list[dict[str, float | int]]:
    phases: list[dict[str, float | int]] = []
    run_start: int | None = None
    run_sign = 0
    for index, value in enumerate(slope):
        sign = _signed_state(value, deadband)
        if not valid_mask[index] or sign == 0:
            _append_phase(phases, timestamps, smoothed_acceleration, run_start, index - 1, run_sign, min_phase_seconds, max_phase_seconds, min_phase_amplitude)
            run_start = None
            run_sign = 0
            continue
        if run_start is None:
            run_start = index
            run_sign = sign
            continue
        if sign != run_sign:
            _append_phase(phases, timestamps, smoothed_acceleration, run_start, index - 1, run_sign, min_phase_seconds, max_phase_seconds, min_phase_amplitude)
            run_start = index
            run_sign = sign
    _append_phase(phases, timestamps, smoothed_acceleration, run_start, len(slope) - 1, run_sign, min_phase_seconds, max_phase_seconds, min_phase_amplitude)
    return phases


def _append_phase(
    phases: list[dict[str, float | int]],
    timestamps: list[float],
    smoothed_acceleration: list[float],
    start_index: int | None,
    end_index: int,
    sign: int,
    min_phase_seconds: float,
    max_phase_seconds: float,
    min_phase_amplitude: float,
) -> None:
    if start_index is None or end_index < start_index or sign == 0:
        return
    duration = timestamps[end_index] - timestamps[start_index] if end_index > start_index else 0.0
    if duration < min_phase_seconds or duration > max_phase_seconds:
        return
    acc_window = smoothed_acceleration[start_index : end_index + 1]
    if not acc_window:
        return
    amplitude = max(acc_window) - min(acc_window)
    if amplitude < min_phase_amplitude:
        return
    phases.append(
        {
            "start_index": start_index,
            "end_index": end_index,
            "start_timestamp": timestamps[start_index],
            "end_timestamp": timestamps[end_index],
            "duration": duration,
            "amplitude": amplitude,
            "sign": sign,
        }
    )


def _phases_can_chain(
    left_phase: dict[str, float | int],
    right_phase: dict[str, float | int],
    max_gap_seconds: float,
) -> bool:
    if int(left_phase["sign"]) == int(right_phase["sign"]):
        return False
    gap = float(right_phase["start_timestamp"]) - float(left_phase["end_timestamp"])
    return gap <= max_gap_seconds


def _is_alternating_sequence(phases: list[dict[str, float | int]], max_gap_seconds: float) -> bool:
    if len(phases) < 2:
        return False
    for index in range(len(phases) - 1):
        if not _phases_can_chain(phases[index], phases[index + 1], max_gap_seconds):
            return False
    return True


def _detect_decel_jump_events(
    timestamps: list[float],
    smoothed_acceleration: list[float],
    valid_mask: list[bool],
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    window_seconds: float,
    min_drop: float,
    rebound_window_seconds: float,
    min_rebound: float,
    min_jump_slope: float,
    min_rebound_slope: float,
) -> list[dict[str, float | int | str]]:
    events: list[dict[str, float | int | str]] = []
    index = 0
    while index < len(timestamps) - 1:
        if not valid_mask[index]:
            index += 1
            continue
        left_acc = smoothed_acceleration[index]
        best_end = -1
        best_jump = 0.0
        best_direction = 0
        probe = index + 1
        while probe < len(timestamps) and valid_mask[probe] and timestamps[probe] - timestamps[index] <= window_seconds:
            candidate_acc = smoothed_acceleration[probe]
            negative_jump = left_acc - candidate_acc
            if negative_jump >= min_drop and negative_jump > best_jump:
                best_jump = negative_jump
                best_end = probe
                best_direction = -1
            positive_jump = candidate_acc - left_acc
            if positive_jump >= min_drop and positive_jump > best_jump:
                best_jump = positive_jump
                best_end = probe
                best_direction = 1
            probe += 1
        if best_end < 0:
            index += 1
            continue

        rebound_peak = smoothed_acceleration[best_end]
        rebound_best_timestamp = timestamps[best_end]
        rebound_probe = best_end + 1
        rebound_deadline = timestamps[best_end] + rebound_window_seconds
        while rebound_probe < len(timestamps) and valid_mask[rebound_probe] and timestamps[rebound_probe] <= rebound_deadline:
            current_acc = smoothed_acceleration[rebound_probe]
            if best_direction < 0:
                rebound_peak = max(rebound_peak, current_acc)
                if rebound_peak == current_acc:
                    rebound_best_timestamp = timestamps[rebound_probe]
            else:
                rebound_peak = min(rebound_peak, current_acc)
                if rebound_peak == current_acc:
                    rebound_best_timestamp = timestamps[rebound_probe]
            rebound_probe += 1
        rebound_amount = (
            rebound_peak - smoothed_acceleration[best_end]
            if best_direction < 0
            else smoothed_acceleration[best_end] - rebound_peak
        )
        if rebound_amount < min_rebound:
            index += 1
            continue
        jump_slope = best_jump / max(timestamps[best_end] - timestamps[index], 1e-6)
        rebound_slope = rebound_amount / max(rebound_best_timestamp - timestamps[best_end], 1e-6)
        if jump_slope < min_jump_slope or rebound_slope < min_rebound_slope:
            index += 1
            continue

        duration = timestamps[best_end] - timestamps[index]
        severity = _score_event_severity(best_jump, max(min_sign_flips, 1), max(duration, 0.2), min_oscillation_amplitude, min_sign_flips)
        events.append(
            {
                "start_timestamp": timestamps[index],
                "end_timestamp": timestamps[best_end],
                "duration": duration,
                "sample_count": best_end - index + 1,
                "amplitude": best_jump,
                "sign_flips": max(min_sign_flips, 1),
                "severity": severity,
                "detection_type": "decel_jump",
            }
        )
        index = best_end + 1
    return events


def _merge_event_details(
    events: list[dict[str, float | int | str]],
    timestamps: list[float],
    smoothed_acceleration: list[float],
    slope: list[float],
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
    merge_gap_seconds: float,
) -> list[dict[str, float | int | str]]:
    if not events:
        return []
    sorted_events = sorted(events, key=lambda item: (float(item["start_timestamp"]), float(item["end_timestamp"])))
    merged: list[dict[str, float | int | str]] = []
    for event in sorted_events:
        if not merged:
            merged.append(dict(event))
            continue
        last = merged[-1]
        if float(event["start_timestamp"]) > float(last["end_timestamp"]) + merge_gap_seconds:
            merged.append(dict(event))
            continue
        merged[-1] = _combine_two_events(
            last,
            event,
            timestamps,
            smoothed_acceleration,
            slope,
            deadband,
            min_oscillation_amplitude,
            min_sign_flips,
            sign_flip_min_stable_samples,
        )
    return merged


def _combine_two_events(
    left_event: dict[str, float | int | str],
    right_event: dict[str, float | int | str],
    timestamps: list[float],
    smoothed_acceleration: list[float],
    slope: list[float],
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
) -> dict[str, float | int | str]:
    start_timestamp = min(float(left_event["start_timestamp"]), float(right_event["start_timestamp"]))
    end_timestamp = max(float(left_event["end_timestamp"]), float(right_event["end_timestamp"]))
    start_index = _find_timestamp_index(timestamps, start_timestamp)
    end_index = _find_timestamp_index(timestamps, end_timestamp)
    if start_index < 0 or end_index < start_index:
        return dict(left_event)

    acc_window = smoothed_acceleration[start_index : end_index + 1]
    slope_window = slope[start_index : end_index + 1]
    amplitude = max(acc_window) - min(acc_window) if acc_window else max(float(left_event["amplitude"]), float(right_event["amplitude"]))
    sign_flips = max(
        _count_sign_flips(slope_window, deadband, sign_flip_min_stable_samples),
        int(left_event["sign_flips"]),
        int(right_event["sign_flips"]),
    )
    duration = end_timestamp - start_timestamp if end_index > start_index else 0.0
    severity = max(
        float(left_event["severity"]),
        float(right_event["severity"]),
        _score_event_severity(amplitude, sign_flips, max(duration, 0.2), min_oscillation_amplitude, min_sign_flips),
    )
    detection_types = sorted(
        {
            item.strip()
            for event in (left_event, right_event)
            for item in str(event.get("detection_type", "oscillation")).split("+")
            if item.strip()
        }
    )
    return {
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "duration": duration,
        "sample_count": end_index - start_index + 1,
        "amplitude": amplitude,
        "sign_flips": sign_flips,
        "severity": round(severity, 3),
        "detection_type": "+".join(detection_types) if detection_types else "oscillation",
    }


def _filter_events_by_reversal_pattern(
    events: list[dict[str, float | int | str]],
    timestamps: list[float],
    smoothed_acceleration: list[float],
    jump_window_seconds: float,
    min_jump_amplitude: float,
    rebound_window_seconds: float,
    min_rebound: float,
    min_jump_slope: float,
    min_rebound_slope: float,
) -> list[dict[str, float | int | str]]:
    filtered: list[dict[str, float | int | str]] = []
    for event in events:
        start_index = _find_timestamp_index(timestamps, float(event["start_timestamp"]))
        end_index = _find_timestamp_index(timestamps, float(event["end_timestamp"]))
        if start_index < 0 or end_index < start_index:
            continue
        if _has_strong_reversal_pattern(
            timestamps=timestamps,
            smoothed_acceleration=smoothed_acceleration,
            start_index=start_index,
            end_index=end_index,
            jump_window_seconds=jump_window_seconds,
            min_jump_amplitude=min_jump_amplitude,
            rebound_window_seconds=rebound_window_seconds,
            min_rebound=min_rebound,
            min_jump_slope=min_jump_slope,
            min_rebound_slope=min_rebound_slope,
        ):
            filtered.append(event)
    return filtered


def _has_strong_reversal_pattern(
    timestamps: list[float],
    smoothed_acceleration: list[float],
    start_index: int,
    end_index: int,
    jump_window_seconds: float,
    min_jump_amplitude: float,
    rebound_window_seconds: float,
    min_rebound: float,
    min_jump_slope: float,
    min_rebound_slope: float,
) -> bool:
    for left in range(start_index, end_index):
        left_acc = smoothed_acceleration[left]
        for peak_index in range(left + 1, end_index + 1):
            jump_duration = timestamps[peak_index] - timestamps[left]
            if jump_duration <= 0:
                continue
            if jump_duration > jump_window_seconds:
                break
            peak_acc = smoothed_acceleration[peak_index]
            jump_value = peak_acc - left_acc
            if abs(jump_value) < min_jump_amplitude:
                continue
            jump_slope = abs(jump_value) / jump_duration
            if jump_slope < min_jump_slope:
                continue

            direction = 1 if jump_value > 0 else -1
            best_rebound = 0.0
            best_rebound_slope = 0.0
            for rebound_index in range(peak_index + 1, end_index + 1):
                rebound_duration = timestamps[rebound_index] - timestamps[peak_index]
                if rebound_duration <= 0:
                    continue
                if rebound_duration > rebound_window_seconds:
                    break
                rebound_value = (
                    peak_acc - smoothed_acceleration[rebound_index]
                    if direction > 0
                    else smoothed_acceleration[rebound_index] - peak_acc
                )
                if rebound_value <= 0:
                    continue
                rebound_slope = rebound_value / rebound_duration
                if rebound_value > best_rebound:
                    best_rebound = rebound_value
                    best_rebound_slope = rebound_slope
            if best_rebound >= min_rebound and best_rebound_slope >= min_rebound_slope:
                return True
    return False


def _events_to_mask(timestamps: list[float], events: list[dict[str, float | int | str]]) -> list[bool]:
    mask = [False] * len(timestamps)
    for event in events:
        start_timestamp = float(event["start_timestamp"])
        end_timestamp = float(event["end_timestamp"])
        start_index = _find_timestamp_index(timestamps, start_timestamp)
        end_index = _find_timestamp_index(timestamps, end_timestamp)
        if start_index < 0:
            start_index = bisect_left(timestamps, start_timestamp)
        if end_index < 0:
            end_index = bisect_right(timestamps, end_timestamp) - 1
        if start_index < 0 or end_index < start_index:
            continue
        start_index = max(0, start_index)
        end_index = min(len(timestamps) - 1, end_index)
        for index in range(start_index, end_index + 1):
            mask[index] = True
    return mask


def _merge_short_gaps(timestamps: list[float], mask: list[bool], merge_gap_seconds: float) -> list[bool]:
    if not mask:
        return []
    merged = mask[:]
    index = 0
    while index < len(merged):
        if merged[index]:
            index += 1
            continue
        gap_start = index
        while index < len(merged) and not merged[index]:
            index += 1
        gap_end = index - 1
        left_index = gap_start - 1
        right_index = index
        if left_index < 0 or right_index >= len(merged):
            continue
        if not merged[left_index] or not merged[right_index]:
            continue
        if timestamps[right_index] - timestamps[left_index] <= merge_gap_seconds:
            for fill_index in range(gap_start, gap_end + 1):
                merged[fill_index] = True
    return merged


def _signed_state(value: float, deadband: float) -> int:
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


def _time_window_moving_average(timestamps: list[float], values: list[float], window_seconds: float) -> list[float]:
    if len(timestamps) != len(values):
        usable_length = min(len(timestamps), len(values))
        timestamps = timestamps[:usable_length]
        values = values[:usable_length]
    if not values:
        return []
    if window_seconds <= 0:
        return [_safe_float(value) for value in values]

    half_window = window_seconds / 2.0
    finite_values = [_safe_float(value) for value in values]
    prefix_sum = [0.0]
    prefix_count = [0]
    for value in finite_values:
        if _is_finite(value):
            prefix_sum.append(prefix_sum[-1] + value)
            prefix_count.append(prefix_count[-1] + 1)
        else:
            prefix_sum.append(prefix_sum[-1])
            prefix_count.append(prefix_count[-1])

    averaged: list[float] = []
    for timestamp in timestamps:
        left = bisect_left(timestamps, timestamp - half_window)
        right = bisect_right(timestamps, timestamp + half_window)
        count = prefix_count[right] - prefix_count[left]
        if count <= 0:
            averaged.append(float("nan"))
        else:
            averaged.append((prefix_sum[right] - prefix_sum[left]) / count)
    return averaged


def _derivative(timestamps: list[float], values: list[float]) -> list[float]:
    if not values:
        return []
    derivative_values = [float("nan")] * len(values)
    for index in range(1, min(len(timestamps), len(values))):
        dt = timestamps[index] - timestamps[index - 1]
        if dt <= 0 or not _is_finite(values[index]) or not _is_finite(values[index - 1]):
            continue
        derivative_values[index] = (float(values[index]) - float(values[index - 1])) / dt
    if len(derivative_values) > 1:
        derivative_values[0] = derivative_values[1]
    return derivative_values


def _finite_range(values: list[float]) -> float:
    finite = [float(value) for value in values if _is_finite(value)]
    if not finite:
        return 0.0
    return max(finite) - min(finite)


def _finite_percentile(values: list[float], percentile: float) -> float:
    finite = sorted(float(value) for value in values if _is_finite(value))
    if not finite:
        return 0.0
    if len(finite) == 1:
        return finite[0]
    position = (len(finite) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return finite[lower]
    fraction = position - lower
    return finite[lower] * (1.0 - fraction) + finite[upper] * fraction


def _finite_rms(values: list[float]) -> float:
    finite = [float(value) for value in values if _is_finite(value)]
    if not finite:
        return 0.0
    return math.sqrt(sum(value * value for value in finite) / len(finite))


def _finite_abs_peak(values: list[float]) -> float:
    finite = [abs(float(value)) for value in values if _is_finite(value)]
    if not finite:
        return 0.0
    return max(finite)


def _finite_min(values: list[float]) -> float:
    finite = [float(value) for value in values if _is_finite(value)]
    return min(finite) if finite else float("nan")


def _finite_max(values: list[float]) -> float:
    finite = [float(value) for value in values if _is_finite(value)]
    return max(finite) if finite else float("nan")


def _local_positive_peak_near(
    timestamps: list[float],
    values: list[float],
    center_index: int,
    radius_seconds: float,
) -> tuple[float, float, int]:
    if center_index < 0 or center_index >= len(timestamps):
        return 0.0, float("inf"), center_index
    left = bisect_left(timestamps, timestamps[center_index] - radius_seconds)
    right = bisect_right(timestamps, timestamps[center_index] + radius_seconds)
    if right <= left:
        return 0.0, float("inf"), center_index

    best_index = center_index
    best_value = float("-inf")
    for index in range(left, min(right, len(values))):
        value = values[index]
        if _is_finite(value) and float(value) > best_value:
            best_value = float(value)
            best_index = index
    if best_value == float("-inf"):
        return 0.0, float("inf"), center_index
    return best_value, abs(float(timestamps[best_index] - timestamps[center_index])), best_index


def _is_signal_valid(value: object) -> bool:
    numeric_value = _safe_float(value)
    if _is_finite(numeric_value):
        return numeric_value == 0.0
    text = str(value).strip().lower()
    return text in {"valid", "true", "ok"}


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _empty_result(
    start_timestamp: float,
    end_timestamp: float,
    valid_sample_count: int,
    candidate_sample_count: int,
    smoothing_window: int,
    deadband: float,
    min_oscillation_amplitude: float,
    min_sign_flips: int,
    sign_flip_min_stable_samples: int,
    amplitude_window_seconds: float,
    min_event_duration_seconds: float,
    max_event_duration_seconds: float,
    merge_gap_seconds: float,
    decel_jump_rebound_window_seconds: float = DEFAULT_DECEL_JUMP_REBOUND_WINDOW_SECONDS,
    decel_jump_min_rebound: float = DEFAULT_DECEL_JUMP_MIN_REBOUND,
    decel_jump_min_jump_slope: float = DEFAULT_DECEL_JUMP_MIN_JUMP_SLOPE,
    decel_jump_min_rebound_slope: float = DEFAULT_DECEL_JUMP_MIN_REBOUND_SLOPE,
) -> dict[str, object]:
    return {
        "metric_name": "acc_jerkiness",
        "method": "torque_gated_oscillation_based_or_extended",
        "event_count": 0,
        "valid_sample_count": valid_sample_count,
        "candidate_sample_count": candidate_sample_count,
        "total_sample_count": 0,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "max_event_severity": 0.0,
        "parameters": {
            "smoothing_window": smoothing_window,
            "deadband": deadband,
            "min_oscillation_amplitude": min_oscillation_amplitude,
            "min_sign_flips": min_sign_flips,
            "sign_flip_min_stable_samples": sign_flip_min_stable_samples,
            "amplitude_window_seconds": amplitude_window_seconds,
            "min_event_duration_seconds": min_event_duration_seconds,
            "max_event_duration_seconds": max_event_duration_seconds,
            "merge_gap_seconds": merge_gap_seconds,
            "long_pattern_min_phase_seconds": DEFAULT_LONG_PATTERN_MIN_PHASE_SECONDS,
            "long_pattern_max_phase_seconds": DEFAULT_LONG_PATTERN_MAX_PHASE_SECONDS,
            "long_pattern_min_phase_amplitude": DEFAULT_LONG_PATTERN_MIN_PHASE_AMPLITUDE,
            "long_pattern_min_phase_count": DEFAULT_LONG_PATTERN_MIN_PHASE_COUNT,
            "long_pattern_max_gap_seconds": DEFAULT_LONG_PATTERN_MAX_GAP_SECONDS,
            "decel_jump_window_seconds": DEFAULT_DECEL_JUMP_WINDOW_SECONDS,
            "decel_jump_min_drop": DEFAULT_DECEL_JUMP_MIN_DROP,
            "decel_jump_rebound_window_seconds": decel_jump_rebound_window_seconds,
            "decel_jump_min_rebound": decel_jump_min_rebound,
            "decel_jump_min_jump_slope": decel_jump_min_jump_slope,
            "decel_jump_min_rebound_slope": decel_jump_min_rebound_slope,
            "torque_pad_seconds": DEFAULT_TORQUE_PAD_SECONDS,
            "short_acceleration_window_seconds": DEFAULT_SHORT_ACCELERATION_WINDOW_SECONDS,
            "trend_acceleration_window_seconds": DEFAULT_TREND_ACCELERATION_WINDOW_SECONDS,
            "torque_smoothing_window_seconds": DEFAULT_TORQUE_SMOOTHING_WINDOW_SECONDS,
            "max_base_event_duration_seconds": DEFAULT_MAX_BASE_EVENT_DURATION_SECONDS,
            "peak_match_window_seconds": DEFAULT_PEAK_MATCH_WINDOW_SECONDS,
            "transient_pad_seconds": DEFAULT_TRANSIENT_PAD_SECONDS,
            "min_positive_jerk_peak": DEFAULT_MIN_POSITIVE_JERK_PEAK,
            "max_negative_jerk_peak": DEFAULT_MAX_NEGATIVE_JERK_PEAK,
            "min_abs_jerk_p95": DEFAULT_MIN_ABS_JERK_P95,
            "min_highpass_accel_rms": DEFAULT_MIN_HIGHPASS_ACCEL_RMS,
            "min_highpass_accel_peak": DEFAULT_MIN_HIGHPASS_ACCEL_PEAK,
            "min_drive_torque_range": DEFAULT_MIN_DRIVE_TORQUE_RANGE,
            "min_brake_torque_range": DEFAULT_MIN_BRAKE_TORQUE_RANGE,
            "min_drive_rate_peak_near_positive_jerk": DEFAULT_MIN_DRIVE_RATE_PEAK_NEAR_POSITIVE_JERK,
            "min_brake_rate_peak_near_negative_jerk": DEFAULT_MIN_BRAKE_RATE_PEAK_NEAR_NEGATIVE_JERK,
            "min_direction_samples": DEFAULT_MIN_DIRECTION_SAMPLES,
        },
        "events": [],
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in {"0", "false", "none", ""}
