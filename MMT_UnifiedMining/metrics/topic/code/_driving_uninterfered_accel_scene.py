from __future__ import annotations

from bisect import bisect_right
from typing import Any

import numpy as np


QUALIFICATION_SECONDS = 0.5
MAX_EVENT_SECONDS = 30.0
MAX_SAMPLE_GAP_SECONDS = 0.2
SPEED_SMOOTHING_SECONDS = 0.5
ACCELERATION_SMOOTHING_SECONDS = 0.5
SPEED_DROP_TOLERANCE_KPH = 1.0
SPEED_DROP_PERSISTENCE_SECONDS = 0.5
ACCELERATION_BUILD_THRESHOLD_MPS2 = 0.2
SET_SPEED_MARGIN_KPH = 22.0
CURVATURE_SPEED_MARGIN_KPH = 2.0
SPEED_STRATA = {
    "0_30": (0.0, 30.0),
    "30_60": (30.0, 60.0),
    "60_90": (60.0, 90.0),
    "90_plus": (90.0, float("inf")),
}
MEASUREMENT_FIELDS = {
    "max_acc": "max_acceleration_mps2",
    "accel_build_time": "acceleration_build_time_s",
    "avg_acc": "average_acceleration_mps2",
}


def run_metric(comprehensive_frames: list[dict[str, Any]], stratum: str, measurement: str):
    observations = extract_observations(comprehensive_frames)
    lower_kph, upper_kph = SPEED_STRATA[stratum]
    field = MEASUREMENT_FIELDS[measurement]
    selected = [
        item for item in observations
        if lower_kph <= item["initial_speed_kph"] < upper_kph
    ]
    values = [float(item[field]) for item in selected]
    details = [
        (
            "[%s] start=%.3fs end=%.3fs initial_speed=%.3fkph "
            "target_speed=%.3fkph value=%.6f"
        ) % (
            field,
            item["start_s"],
            item["end_s"],
            item["initial_speed_kph"],
            item["target_speed_kph"],
            item[field],
        )
        for item in selected
    ]
    stats = {
        "All Case Count": len(values),
        "Bad Case": 0,
        "Severity Score": 0.0,
        "Metric Value Sum": sum(values),
        "Metric Value Count": len(values),
    }
    return False, details, stats


def extract_observations(comprehensive_frames: list[dict[str, Any]]) -> list[dict[str, float]]:
    rows = []
    for frame in comprehensive_frames:
        if not isinstance(frame, dict):
            continue
        sequence_meta = frame.get("_sequence_meta", {})
        timestamp_ms = _safe_float(frame.get("unp_timestamp_ms"))
        chassis = frame.get("matched_chassis")
        if isinstance(chassis, dict):
            timestamp_ms = _safe_float(chassis.get("timestamp_ms"), timestamp_ms)
        if timestamp_ms is None:
            continue

        unp = frame.get("unp_frame_data")
        unp = unp if isinstance(unp, dict) else {}
        chassis = chassis if isinstance(chassis, dict) else {}
        fusion = frame.get("matched_fusion")
        fusion = fusion if isinstance(fusion, dict) else {}
        traffic = unp.get("TrafficLight")
        traffic = traffic if isinstance(traffic, dict) else {}
        curvature = unp.get("curvature_info")
        curvature = curvature if isinstance(curvature, dict) else {}
        acc_info = unp.get("Acc_info")
        acc_info = acc_info if isinstance(acc_info, dict) else {}
        np_state = unp.get("NP_State")
        np_state = np_state if isinstance(np_state, dict) else {}

        speed = _safe_float(chassis.get("vehicle_speed_average"))
        acceleration = _safe_float(chassis.get("accleration_on_wheel"))
        set_speed = _safe_float(acc_info.get("SetSpeed"))
        stop_distance = _safe_float(traffic.get("stop_distance"))
        curvature_limit = _safe_float(curvature.get("curv_velocity_limit"))
        curvature_enabled = _as_bool(curvature.get("is_curv_v_limit_enable"))
        throttle_pedal = _safe_float(chassis.get("throttle_pedal_percent"))
        override = chassis.get("throttle_override")
        function = str(np_state.get("function", "")).strip().upper()

        override_known = override is not None or throttle_pedal is not None
        override_active = _as_bool(override) if override is not None else False
        if throttle_pedal is not None and throttle_pedal > 2.0:
            override_active = True
        acc_active_known = bool(function) or "f_Np_on" in np_state
        acc_active = function in {"ACC", "UNP"} or _as_bool(np_state.get("f_Np_on", False))

        known = (
            speed is not None
            and acceleration is not None
            and set_speed is not None
            and stop_distance is not None
            and (
                not curvature_enabled
                or (
                    curvature.get("has_curv_velocity_limit") is True
                    and curvature_limit is not None
                )
            )
            and override_known
            and acc_active_known
            and traffic.get("has_redlight_state") is True
            and traffic.get("has_stop_distance") is True
            and curvature.get("has_curvature_limit_state") is True
            and fusion.get("has_valid_fusion_data") is True
            and "f_potential_cipv_by_pos" in fusion
            and "f_potential_cipv_in_trafficlight" in fusion
        )
        if not known:
            continue

        rows.append(
            {
                "timestamp_s": timestamp_ms / 1000.0,
                "speed_kph": speed,
                "acceleration_mps2": acceleration,
                "set_speed_kph": set_speed,
                "override_active": override_active,
                "acc_active": acc_active,
                "front_present": _as_bool(fusion.get("f_potential_cipv_by_pos"))
                or _as_bool(fusion.get("f_potential_cipv_in_trafficlight")),
                "redlight": _as_bool(traffic.get("f_RedLightOn")),
                "stop_distance_m": stop_distance,
                "curvature_enabled": curvature_enabled,
                "curvature_limit_kph": curvature_limit,
                "is_primary": _safe_int(sequence_meta.get("source_index", 0)) == 0,
            }
        )

    rows.sort(key=lambda item: item["timestamp_s"])
    deduped = []
    for row in rows:
        if deduped and row["timestamp_s"] - deduped[-1]["timestamp_s"] <= 1e-6:
            continue
        deduped.append(row)
    if len(deduped) < 2:
        return []

    times = np.asarray([row["timestamp_s"] for row in deduped], dtype=float)
    speed = np.asarray([row["speed_kph"] for row in deduped], dtype=float)
    acceleration = np.asarray([row["acceleration_mps2"] for row in deduped], dtype=float)
    qualified = np.zeros(len(deduped), dtype=bool)
    for index, row in enumerate(deduped):
        goal_speed = row["speed_kph"] + 20.0
        curvature_ok = (
            not row["curvature_enabled"]
            or row["curvature_limit_kph"] >= goal_speed + CURVATURE_SPEED_MARGIN_KPH
        )
        qualified[index] = (
            row["acc_active"]
            and not row["override_active"]
            and not row["front_present"]
            and not row["redlight"]
            and row["stop_distance_m"] > 50.0
            and curvature_ok
            and row["speed_kph"] >= 0.0
            and row["set_speed_kph"] >= row["speed_kph"] + SET_SPEED_MARGIN_KPH
        )
        if index > 0:
            gap = times[index] - times[index - 1]
            if gap <= 0.0 or gap > MAX_SAMPLE_GAP_SECONDS:
                qualified[index] = False

    observations: list[dict[str, float]] = []
    index = 0
    while index < len(deduped):
        if not qualified[index]:
            index += 1
            continue
        start = index
        while index + 1 < len(deduped) and qualified[index + 1]:
            index += 1
        end = index
        index += 1
        if not deduped[start]["is_primary"] or times[end] - times[start] < QUALIFICATION_SECONDS:
            continue

        max_end = min(end, int(np.searchsorted(times, times[start] + MAX_EVENT_SECONDS, side="right") - 1))
        if max_end <= start:
            continue
        smooth_speed = _window_average(times, speed, start, max_end, SPEED_SMOOTHING_SECONDS)
        goal_speed = speed[start] + 20.0
        goal_hits = np.flatnonzero(smooth_speed >= goal_speed)
        if not goal_hits.size:
            continue
        goal_offset = int(goal_hits[0])
        if goal_offset == 0:
            continue
        if _has_persistent_speed_drop(times[start : start + goal_offset + 1], smooth_speed[: goal_offset + 1]):
            continue

        finish_s = _crossing_time(
            times[start + goal_offset - 1],
            times[start + goal_offset],
            smooth_speed[goal_offset - 1],
            smooth_speed[goal_offset],
            goal_speed,
        )
        duration_s = finish_s - times[start]
        if duration_s <= 0.0 or duration_s > MAX_EVENT_SECONDS:
            continue

        smooth_acc = _window_average(times, acceleration, start, start + goal_offset, ACCELERATION_SMOOTHING_SECONDS)
        build_hits = np.flatnonzero(smooth_acc > ACCELERATION_BUILD_THRESHOLD_MPS2)
        if not build_hits.size:
            continue
        max_acceleration = float(np.nanmax(smooth_acc))
        max_offset = int(
            np.flatnonzero(np.isclose(smooth_acc, max_acceleration, rtol=1e-9, atol=1e-12))[0]
        )
        first_build = int(build_hits[0])
        if max_offset < first_build:
            continue

        initial_speed = float(speed[start])
        observations.append(
            {
                "start_s": float(times[start]),
                "end_s": float(finish_s),
                "duration_s": float(duration_s),
                "initial_speed_kph": initial_speed,
                "target_speed_kph": initial_speed + 20.0,
                "max_acceleration_mps2": float(smooth_acc[max_offset]),
                "acceleration_build_time_s": float(times[start + max_offset] - times[start + first_build]),
                "average_acceleration_mps2": float((20.0 / 3.6) / duration_s),
            }
        )
    return observations


def _window_average(times: np.ndarray, values: np.ndarray, start: int, end: int, window_s: float) -> np.ndarray:
    axis = times[start : end + 1]
    data = values[start : end + 1]
    output = np.full(data.shape, np.nan, dtype=float)
    half_window = window_s / 2.0
    for index, timestamp in enumerate(axis):
        left = np.searchsorted(axis, max(axis[0], timestamp - half_window), side="left")
        right = np.searchsorted(axis, min(axis[-1], timestamp + half_window), side="right")
        window = data[left:right]
        finite = window[np.isfinite(window)]
        if finite.size:
            output[index] = float(np.mean(finite))
    return output


def _has_persistent_speed_drop(times: np.ndarray, speed: np.ndarray) -> bool:
    peak = -float("inf")
    drop_start: float | None = None
    for timestamp, value in zip(times, speed):
        peak = max(peak, float(value))
        if peak - float(value) > SPEED_DROP_TOLERANCE_KPH:
            if drop_start is None:
                drop_start = float(timestamp)
            elif float(timestamp) - drop_start >= SPEED_DROP_PERSISTENCE_SECONDS:
                return True
        else:
            drop_start = None
    return False


def _crossing_time(t0: float, t1: float, v0: float, v1: float, target: float) -> float:
    if v1 <= v0:
        return float(t1)
    ratio = min(1.0, max(0.0, (target - v0) / (v1 - v0)))
    return float(t0 + ratio * (t1 - t0))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        parsed = float(value)
        return parsed if np.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"", "0", "false", "none", "nan"}
