from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


THROTTLE_OVERRIDE_THRESHOLD_PERCENT = 0.0


def load_can_reference_module(file_name: str) -> object:
    module_path = Path(__file__).resolve().parents[2] / "can" / "code" / file_name
    module_name = f"topic_reference_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load CAN reference metric: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_topic_acc_series(
    comprehensive_frames: list[dict[str, Any]],
    require_speed: bool,
    use_slope_compensation: bool = True,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    primary_timestamps_ms: list[float] = []

    for frame in comprehensive_frames:
        if not isinstance(frame, dict):
            continue

        sequence_meta = frame.get("_sequence_meta", {}) if isinstance(frame.get("_sequence_meta", {}), dict) else {}
        is_primary = int(sequence_meta.get("source_index", 0) or 0) == 0

        unp_frame_data = frame.get("unp_frame_data", {}) if isinstance(frame.get("unp_frame_data", {}), dict) else {}
        matched_chassis = frame.get("matched_chassis", {}) or {}
        matched_msd_control = frame.get("matched_msd_control", {}) or {}
        if not isinstance(matched_chassis, dict) or not isinstance(matched_msd_control, dict):
            continue

        timestamp_ms = _safe_float(matched_chassis.get("timestamp_ms"), None)
        if timestamp_ms is None:
            timestamp_ms = _safe_float(frame.get("unp_timestamp_ms"), None)
        if timestamp_ms is None:
            continue

        acceleration = _safe_float(matched_chassis.get("accleration_on_wheel"), None)
        if acceleration is None:
            continue
        slope_acc = _safe_float(matched_msd_control.get("slope_acc"), 0.0) or 0.0
        if use_slope_compensation:
            acceleration += slope_acc

        speed = _safe_float(matched_chassis.get("vehicle_speed_average"), None)
        if require_speed and speed is None:
            continue

        rows.append(
            {
                "timestamp_ms": timestamp_ms,
                "acceleration": acceleration,
                "speed": speed,
                "acc_active": _is_acc_active(unp_frame_data),
                "pedal_override": _is_throttle_override(matched_chassis),
                "is_primary": is_primary,
            }
        )
        if is_primary:
            primary_timestamps_ms.append(timestamp_ms)

    rows.sort(key=lambda row: float(row["timestamp_ms"]))
    rows = _dedupe_rows_by_timestamp(rows)
    if not rows:
        return {
            "axis": [],
            "acceleration": [],
            "speed": [],
            "acc_active": [],
            "pedal_override": [],
            "primary_end_seconds": 0.0,
            "segment_start_ms": 0.0,
            "valid_frame_count": 0,
        }

    segment_start_ms = min(primary_timestamps_ms) if primary_timestamps_ms else float(rows[0]["timestamp_ms"])
    primary_end_ms = max(primary_timestamps_ms) if primary_timestamps_ms else float(rows[-1]["timestamp_ms"])

    return {
        "axis": [(float(row["timestamp_ms"]) - segment_start_ms) / 1000.0 for row in rows],
        "acceleration": [float(row["acceleration"]) for row in rows],
        "speed": [0.0 if row["speed"] is None else float(row["speed"]) for row in rows],
        "acc_active": [bool(row["acc_active"]) for row in rows],
        "pedal_override": [bool(row["pedal_override"]) for row in rows],
        "primary_end_seconds": (primary_end_ms - segment_start_ms) / 1000.0,
        "segment_start_ms": segment_start_ms,
        "valid_frame_count": len(rows),
    }


def filter_events_started_in_primary(
    events: list[dict[str, Any]],
    primary_end_seconds: float,
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if _safe_float(event.get("start_timestamp"), primary_end_seconds + 1.0) <= primary_end_seconds + 1e-9
    ]


def _dedupe_rows_by_timestamp(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for row in rows:
        timestamp_ms = float(row["timestamp_ms"])
        if deduped and abs(timestamp_ms - float(deduped[-1]["timestamp_ms"])) <= 1e-6:
            continue
        deduped.append(row)
    return deduped


def _is_acc_active(unp_frame_data: dict[str, Any]) -> bool:
    np_state = unp_frame_data.get("NP_State", {}) if isinstance(unp_frame_data, dict) else {}
    if not isinstance(np_state, dict):
        return False
    function_value = str(np_state.get("function", "")).strip().upper()
    if function_value in {"ACC", "UNP"}:
        return True
    return _as_bool(np_state.get("f_Np_on", False))


def _is_throttle_override(matched_chassis: dict[str, Any]) -> bool:
    throttle_override = matched_chassis.get("throttle_override")
    if throttle_override is not None:
        return _as_bool(throttle_override)

    throttle_pedal_percent = _safe_float(matched_chassis.get("throttle_pedal_percent"), None)
    if throttle_pedal_percent is None:
        return False
    return throttle_pedal_percent > THROTTLE_OVERRIDE_THRESHOLD_PERCENT


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = 0) -> int | None:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in {"0", "false", "none", ""}
