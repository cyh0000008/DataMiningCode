from __future__ import annotations

from typing import Any

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, display_speed_array, mask_to_segments, time_window_moving_average, severity_outside, within_bounds
from powertrain_utils import value_for_powertrain
from signal_preprocessor import SignalFrame


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
REQUIRED_FIELDS = (
    "time_s",
    "acceleration",
    "acceleration_valid_flag",
    "ego_speed",
    "ego_speed_valid_flag",
    "set_speed",
    "acc_active",
    "pedal_override",
    "front_target_id",
    "front_range",
    "front_lat_range",
    "flt1_cutin_flag",
    "curv_enable",
    "curv_speed_limit",
    "acc_active_flag",
    "pedal_override_flag",
    "front_valid",
    "cutin_valid",
)
REQUIRED_PATHS = (
    "acceleration",
    "acceleration_valid",
    "ego_speed",
    "ego_speed_valid",
    "set_speed",
    "acc_active",
    "pedal_override",
    "front_target_id",
    "front_range",
    "front_lat_range",
    "flt1_cutin_flag",
    "curv_enable",
    "curv_speed_limit",
)


def create_metric_class(stratum: str, measurement: str) -> type[BaseMetric]:
    lower_kph, upper_kph = SPEED_STRATA[stratum]
    name = "driving_uninterfered_accel_%s_%s" % (measurement, stratum)

    class DrivingUninterferedAccelerationMetric(BaseMetric):
        category = "CPI"
        result_mode = "case_rate"
        required_fields = REQUIRED_FIELDS

        def __init__(self) -> None:
            self.name = name

        def evaluate(self, frame: SignalFrame) -> MetricResult:
            missing_paths = [field for field in REQUIRED_PATHS if field not in frame.paths]
            if missing_paths:
                return MetricResult(
                    metric_name=self.name,
                    category=self.category,
                    result_mode=self.result_mode,
                    source_path=frame.source_path,
                    distance_km=frame.distance_km,
                    distance_status=frame.distance_status,
                    total_cases=0,
                    bad_cases=0,
                    severity_score=0.0,
                    events=[],
                    details=["skipped: required signal paths unavailable: %s" % ", ".join(missing_paths)],
                    error="SKIPPED_MISSING_SIGNALS",
                    status="SKIPPED",
                    missing_fields=missing_paths,
                    missing_signals={field: frame.missing_signals.get(field, "<signal path not resolved>") for field in missing_paths},
                )

            observations = _find_observations(frame)
            selected = [
                item for item in observations
                if lower_kph <= item["initial_speed_kph"] < upper_kph
            ]
            metric_field = MEASUREMENT_FIELDS[measurement]
            standards = value_for_powertrain(
                self.powertrain_type,
                ice_value={"max_acc": (1.5, 2.5), "avg_acc": (1.5, None), "accel_build_time": (None, 3.5)},
                phev_value={"max_acc": (1.5, 2.5), "avg_acc": (1.5, None), "accel_build_time": (None, 3.5)},
                bev_value={"max_acc": (1.5, 2.5), "avg_acc": (1.5, None), "accel_build_time": (None, 3.5)},
            )
            lower, upper = standards[measurement]

            def is_bad(value: float) -> bool:
                if measurement == "avg_acc":
                    return value <= lower
                if measurement == "accel_build_time":
                    return value >= upper
                return not within_bounds(value, lower, upper)

            events = [
                MetricEvent(
                    start_s=item["start_s"],
                    end_s=item["end_s"],
                    severity=severity_outside(item[metric_field], lower, upper, 1.0),
                    message="%s=%.6f" % (metric_field, item[metric_field]),
                    values={**item, "measurement_value": item[metric_field]},
                )
                for item in selected
                if is_bad(item[metric_field])
            ]
            return MetricResult(
                metric_name=self.name,
                category=self.category,
                result_mode=self.result_mode,
                source_path=frame.source_path,
                distance_km=frame.distance_km,
                distance_status=frame.distance_status,
                total_cases=len(selected),
                bad_cases=len(events),
                severity_score=max((event.severity for event in events), default=0.0),
                events=events,
                details=[event.message for event in events],
            )

    DrivingUninterferedAccelerationMetric.__name__ = "DrivingUninterferedAccelerationMetric"
    return DrivingUninterferedAccelerationMetric


def _find_observations(frame: SignalFrame) -> list[dict[str, Any]]:
    cache_key = ("driving_uninterfered_accel_observations",)
    if cache_key not in frame.runtime_cache:
        frame.runtime_cache[cache_key] = _extract_observations(frame)
    return list(frame.runtime_cache[cache_key])


def _extract_observations(frame: SignalFrame) -> list[dict[str, Any]]:
    t = frame.time_s.astype(float)
    if t.size < 2:
        return []

    speed_kph = display_speed_array(frame, "ego_speed")
    set_speed_kph = display_speed_array(frame, "set_speed")
    acceleration = frame.col("acceleration").astype(float)
    acc_active_raw = frame.col("acc_active").astype(float)
    override_raw = frame.col("pedal_override").astype(float)
    target_id = frame.col("front_target_id").astype(float)
    front_range = frame.col("front_range").astype(float)
    front_lat_range = frame.col("front_lat_range").astype(float)
    cutin = frame.col("flt1_cutin_flag").astype(float)
    curv_enabled = frame.col("curv_enable").astype(float) != 0.0
    curv_limit_mps = frame.col("curv_speed_limit").astype(float)

    known = np.logical_and.reduce(
        (
            np.isfinite(speed_kph),
            np.isfinite(set_speed_kph),
            np.isfinite(acceleration),
            frame.col("acceleration_valid_flag").astype(bool),
            frame.col("ego_speed_valid_flag").astype(bool),
            np.isfinite(acc_active_raw),
            np.isfinite(override_raw),
            np.isfinite(target_id),
            np.logical_or(target_id == 0.0, np.logical_and(np.isfinite(front_range), front_range > 0.0)),
            np.isfinite(front_lat_range),
            np.isfinite(cutin),
            np.isfinite(frame.col("curv_enable").astype(float)),
            np.logical_or(np.logical_not(curv_enabled), np.isfinite(curv_limit_mps)),
        )
    )
    no_front = np.logical_and(
        np.logical_not(frame.col("front_valid").astype(bool)),
        np.logical_not(frame.col("cutin_valid").astype(bool)),
    )
    no_override = np.logical_not(frame.col("pedal_override_flag").astype(bool))
    curv_ok = np.logical_or(
        np.logical_not(curv_enabled),
        curv_limit_mps * 3.6 >= speed_kph + 20.0 + CURVATURE_SPEED_MARGIN_KPH,
    )
    base = np.logical_and.reduce(
        (
            known,
            frame.col("acc_active_flag").astype(bool),
            no_override,
            no_front,
            curv_ok,
            speed_kph >= 0.0,
            set_speed_kph >= speed_kph + SET_SPEED_MARGIN_KPH,
        )
    )
    if t.size > 1:
        gaps = np.diff(t)
        base[1:] &= np.logical_and(gaps > 0.0, gaps <= MAX_SAMPLE_GAP_SECONDS)

    observations: list[dict[str, Any]] = []
    primary_length = frame.primary_length if frame.primary_length is not None else frame.length
    for seg in mask_to_segments(t, base):
        if seg.duration(t) < QUALIFICATION_SECONDS:
            continue
        if seg.start >= primary_length or (frame.has_continuous_previous and seg.start == 0):
            continue

        end_limit_s = t[seg.start] + MAX_EVENT_SECONDS
        end_limit_idx = int(np.searchsorted(t, end_limit_s, side="right") - 1)
        available_end = min(seg.end, end_limit_idx)
        if available_end <= seg.start:
            continue

        smooth_speed = _window_average(t, speed_kph, seg.start, available_end, SPEED_SMOOTHING_SECONDS)
        target_speed = speed_kph[seg.start] + 20.0
        target_hits = np.flatnonzero(smooth_speed >= target_speed)
        if not target_hits.size:
            continue
        goal_offset = int(target_hits[0])
        if goal_offset == 0:
            continue
        if _has_persistent_speed_drop(t[seg.start : seg.start + goal_offset + 1], smooth_speed[: goal_offset + 1]):
            continue

        event_end_s = _crossing_time(
            t[seg.start + goal_offset - 1],
            t[seg.start + goal_offset],
            smooth_speed[goal_offset - 1],
            smooth_speed[goal_offset],
            target_speed,
        )
        duration_s = event_end_s - float(t[seg.start])
        if duration_s <= 0.0 or duration_s > MAX_EVENT_SECONDS:
            continue

        smooth_acceleration = _window_average(
            t, acceleration, seg.start, seg.start + goal_offset, ACCELERATION_SMOOTHING_SECONDS
        )
        finite_acc = np.flatnonzero(np.isfinite(smooth_acceleration))
        if not finite_acc.size:
            continue
        smooth_acceleration = smooth_acceleration[: goal_offset + 1]
        build_hits = np.flatnonzero(smooth_acceleration > ACCELERATION_BUILD_THRESHOLD_MPS2)
        if not build_hits.size:
            continue
        max_acceleration = float(np.nanmax(smooth_acceleration))
        max_offset = int(
            np.flatnonzero(np.isclose(smooth_acceleration, max_acceleration, rtol=1e-9, atol=1e-12))[0]
        )
        if max_offset < int(build_hits[0]):
            continue

        initial_speed = float(speed_kph[seg.start])
        observations.append(
            {
                "start_s": float(t[seg.start]),
                "end_s": float(event_end_s),
                "duration_s": round(duration_s, 6),
                "initial_speed_kph": initial_speed,
                "target_speed_kph": round(initial_speed + 20.0, 6),
                "max_acceleration_mps2": float(smooth_acceleration[max_offset]),
                "acceleration_build_time_s": float(
                    t[seg.start + max_offset] - t[seg.start + int(build_hits[0])]
                ),
                "average_acceleration_mps2": float((20.0 / 3.6) / duration_s),
            }
        )
    return observations


def _window_average(t: np.ndarray, values: np.ndarray, start: int, end: int, window_s: float) -> np.ndarray:
    segment_t = t[start : end + 1]
    segment_values = values[start : end + 1]
    return time_window_moving_average(segment_t, segment_values, window_s)


def _has_persistent_speed_drop(t: np.ndarray, speed: np.ndarray) -> bool:
    peak = -np.inf
    drop_start: float | None = None
    for timestamp, value in zip(t, speed):
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
