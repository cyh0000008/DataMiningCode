from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from metric_utils import (
    BaseMetric,
    MetricEvent,
    MetricResult,
    Segment,
    derivative,
    event_from_segment,
    jerkiness_events,
    result_from_events,
    time_window_moving_average,
)
from signal_preprocessor import SignalFrame


class AccTorqueJerkinessCountMetric(BaseMetric):
    name = "acc_torque_jerkiness_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = (
        "acceleration",
        "acc_active_flag",
        "pedal_override_flag",
        "actual_drive_torque",
        "actual_brake_torque",
    )

    pad_s = 0.35
    short_acceleration_window_s = 0.12
    trend_acceleration_window_s = 0.80
    torque_smoothing_window_s = 0.08
    max_base_event_duration_s = 2.00
    peak_match_window_s = 0.18
    transient_pad_s = 0.20
    min_positive_jerk_peak = 9.0
    max_negative_jerk_peak = -4.0
    min_abs_jerk_p95 = 3.5
    min_highpass_acc_rms = 0.10
    min_highpass_acc_peak = 0.25
    min_drive_torque_range = 80.0
    min_brake_torque_range = 120.0
    min_drive_rate_peak_near_positive_jerk = 500.0
    min_brake_rate_peak_near_negative_jerk = 800.0
    min_direction_samples = 3

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        valid = self._base_valid(frame)
        return self._evaluate_with_valid(frame, valid)

    def _base_valid(self, frame: SignalFrame) -> np.ndarray:
        valid = frame.col("acc_active_flag").astype(bool)
        valid = np.logical_and(valid, np.logical_not(frame.col("pedal_override_flag").astype(bool)))
        if frame.has("ego_speed"):
            valid = np.logical_and(valid, frame.col("ego_speed").astype(float) > 0.0)
        return valid

    def _evaluate_with_valid(self, frame: SignalFrame, valid: np.ndarray) -> MetricResult:
        acceleration = frame.col("acceleration").astype(float)
        base_events = jerkiness_events(frame, valid, acceleration)
        acceleration_smooth = time_window_moving_average(
            frame.time_s,
            acceleration,
            self.short_acceleration_window_s,
        )
        acceleration_trend = time_window_moving_average(
            frame.time_s,
            acceleration,
            self.trend_acceleration_window_s,
        )
        acceleration_highpass = acceleration_smooth - acceleration_trend
        jerk = derivative(frame.time_s, acceleration_smooth)
        drive_torque = time_window_moving_average(
            frame.time_s,
            frame.col("actual_drive_torque").astype(float),
            self.torque_smoothing_window_s,
        )
        brake_torque = time_window_moving_average(
            frame.time_s,
            frame.col("actual_brake_torque").astype(float),
            self.torque_smoothing_window_s,
        )
        drive_rate = derivative(frame.time_s, drive_torque)
        brake_rate = derivative(frame.time_s, brake_torque)

        torque_events: List[MetricEvent] = []
        for base_event in base_events:
            seg = Segment(
                int(base_event.values["start_index"]),
                int(base_event.values["end_index"]),
            )
            passed, transient_seg, values = self._torque_matches_acceleration(
                frame,
                seg,
                acceleration_smooth,
                acceleration_highpass,
                jerk,
                drive_torque,
                brake_torque,
                drive_rate,
                brake_rate,
            )
            if not passed:
                continue

            values.update(
                {
                    "base_jerkiness_severity": base_event.severity,
                    "amplitude": base_event.values.get("amplitude"),
                    "sign_flips": base_event.values.get("sign_flips"),
                }
            )
            severity = round(
                float(base_event.severity)
                + values["abs_jerk_p95"] * 20.0
                + values["highpass_acc_peak"] * 120.0
                + max(values["drive_rate_peak_near_positive_jerk"], 0.0) / 200.0
                + max(values["brake_rate_peak_near_negative_jerk"], 0.0) / 200.0,
                3,
            )
            torque_events.append(
                event_from_segment(
                    frame,
                    transient_seg if transient_seg is not None else seg,
                    severity,
                    "扭矩校验顿挫：驱动扭矩上升对应加速度上升，制动扭矩上升对应减速度增大",
                    values,
                )
            )

        return result_from_events(
            self.name,
            self.category,
            frame,
            len(torque_events),
            torque_events,
            self.result_mode,
        )

    def _torque_matches_acceleration(
        self,
        frame: SignalFrame,
        seg: Segment,
        acceleration: np.ndarray,
        acceleration_highpass: np.ndarray,
        jerk: np.ndarray,
        drive_torque: np.ndarray,
        brake_torque: np.ndarray,
        drive_rate: np.ndarray,
        brake_rate: np.ndarray,
    ) -> Tuple[bool, Optional[Segment], Dict[str, float]]:
        t = frame.time_s
        if seg.duration(t) > self.max_base_event_duration_s:
            return False, None, {}

        start = max(0, int(np.searchsorted(t, t[seg.start] - self.pad_s, side="left")))
        end = min(frame.length - 1, int(np.searchsorted(t, t[seg.end] + self.pad_s, side="right") - 1))
        if end <= start:
            return False, None, {}

        window = slice(start, end + 1)
        finite = np.logical_and.reduce(
            [
                np.isfinite(acceleration[window]),
                np.isfinite(acceleration_highpass[window]),
                np.isfinite(jerk[window]),
                np.isfinite(drive_torque[window]),
                np.isfinite(brake_torque[window]),
                np.isfinite(drive_rate[window]),
                np.isfinite(brake_rate[window]),
            ]
        )
        if np.sum(finite) < self.min_direction_samples * 2:
            return False, None, {}

        drive_window = drive_torque[window]
        brake_window = brake_torque[window]
        acc_window = acceleration[window]
        highpass_window = acceleration_highpass[window]
        jerk_window = jerk[window]
        drive_rate_window = drive_rate[window]
        brake_rate_window = brake_rate[window]

        drive_torque_range = finite_range(drive_window)
        brake_torque_range = finite_range(brake_window)
        if drive_torque_range < self.min_drive_torque_range:
            return False, None, {}
        if brake_torque_range < self.min_brake_torque_range:
            return False, None, {}

        window_indices = np.arange(start, end + 1)
        finite_indices = window_indices[finite]
        positive_jerk_index = int(finite_indices[np.nanargmax(jerk[finite_indices])])
        negative_jerk_index = int(finite_indices[np.nanargmin(jerk[finite_indices])])
        positive_jerk_peak = float(jerk[positive_jerk_index])
        negative_jerk_peak = float(jerk[negative_jerk_index])
        if positive_jerk_peak < self.min_positive_jerk_peak:
            return False, None, {}
        if negative_jerk_peak > self.max_negative_jerk_peak:
            return False, None, {}

        abs_jerk_p95 = finite_percentile(np.abs(jerk_window[finite]), 95.0)
        highpass_acc_rms = finite_rms(highpass_window[finite])
        highpass_acc_peak = finite_abs_peak(highpass_window[finite])
        if abs_jerk_p95 < self.min_abs_jerk_p95:
            return False, None, {}
        if highpass_acc_rms < self.min_highpass_acc_rms:
            return False, None, {}
        if highpass_acc_peak < self.min_highpass_acc_peak:
            return False, None, {}

        drive_rate_peak, drive_rate_lag_s, drive_rate_peak_index = local_positive_peak_near(
            t,
            drive_rate,
            positive_jerk_index,
            self.peak_match_window_s,
        )
        brake_rate_peak, brake_rate_lag_s, brake_rate_peak_index = local_positive_peak_near(
            t,
            brake_rate,
            negative_jerk_index,
            self.peak_match_window_s,
        )
        if drive_rate_peak < self.min_drive_rate_peak_near_positive_jerk:
            return False, None, {}
        if brake_rate_peak < self.min_brake_rate_peak_near_negative_jerk:
            return False, None, {}
        if drive_rate_lag_s > self.peak_match_window_s:
            return False, None, {}
        if brake_rate_lag_s > self.peak_match_window_s:
            return False, None, {}

        peak_indices = [
            positive_jerk_index,
            negative_jerk_index,
            drive_rate_peak_index,
            brake_rate_peak_index,
        ]
        transient_start_time = float(np.nanmin(t[peak_indices]) - self.transient_pad_s)
        transient_end_time = float(np.nanmax(t[peak_indices]) + self.transient_pad_s)
        transient_start = max(0, int(np.searchsorted(t, transient_start_time, side="left")))
        transient_end = min(frame.length - 1, int(np.searchsorted(t, transient_end_time, side="right") - 1))
        if transient_end <= transient_start:
            transient_start = min(peak_indices)
            transient_end = max(peak_indices)
        transient_seg = Segment(transient_start, transient_end)

        values = {
            "base_event_start_index": seg.start,
            "base_event_end_index": seg.end,
            "torque_window_start_index": start,
            "torque_window_end_index": end,
            "transient_start_index": transient_start,
            "transient_end_index": transient_end,
            "acceleration_min": round(finite_min(acc_window), 6),
            "acceleration_max": round(finite_max(acc_window), 6),
            "highpass_acc_rms": round(highpass_acc_rms, 6),
            "highpass_acc_peak": round(highpass_acc_peak, 6),
            "positive_jerk_peak": round(positive_jerk_peak, 6),
            "negative_jerk_peak": round(negative_jerk_peak, 6),
            "abs_jerk_p95": round(abs_jerk_p95, 6),
            "positive_jerk_peak_time_s": round(float(t[positive_jerk_index]), 6),
            "negative_jerk_peak_time_s": round(float(t[negative_jerk_index]), 6),
            "drive_torque_min": round(finite_min(drive_window), 3),
            "drive_torque_max": round(finite_max(drive_window), 3),
            "drive_torque_range": round(drive_torque_range, 3),
            "brake_torque_min": round(finite_min(brake_window), 3),
            "brake_torque_max": round(finite_max(brake_window), 3),
            "brake_torque_range": round(brake_torque_range, 3),
            "drive_rate_peak_near_positive_jerk": round(drive_rate_peak, 3),
            "drive_rate_peak_lag_s": round(drive_rate_lag_s, 6),
            "drive_rate_peak_time_s": round(float(t[drive_rate_peak_index]), 6),
            "brake_rate_peak_near_negative_jerk": round(brake_rate_peak, 3),
            "brake_rate_peak_lag_s": round(brake_rate_lag_s, 6),
            "brake_rate_peak_time_s": round(float(t[brake_rate_peak_index]), 6),
        }
        return True, transient_seg, values


def finite_range(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    return float(np.nanmax(finite) - np.nanmin(finite))


def finite_percentile(values: np.ndarray, percentile: float) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    return float(np.nanpercentile(finite, percentile))


def finite_rms(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    return float(np.sqrt(np.nanmean(finite * finite)))


def finite_abs_peak(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    return float(np.nanmax(np.abs(finite)))


def finite_min(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.nanmin(finite)) if finite.size else float("nan")


def finite_max(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.nanmax(finite)) if finite.size else float("nan")


def finite_ratio(mask: np.ndarray) -> float:
    values = np.asarray(mask)
    if values.size == 0:
        return 0.0
    return float(np.sum(values.astype(bool)) / values.size)


def local_positive_peak_near(
    t: np.ndarray,
    values: np.ndarray,
    center_index: int,
    radius_s: float,
) -> Tuple[float, float, int]:
    left = int(np.searchsorted(t, t[center_index] - radius_s, side="left"))
    right = int(np.searchsorted(t, t[center_index] + radius_s, side="right"))
    if right <= left:
        return 0.0, float("inf"), int(center_index)

    local_values = np.asarray(values[left:right], dtype=float)
    finite = np.isfinite(local_values)
    if not np.any(finite):
        return 0.0, float("inf"), int(center_index)

    safe_values = np.where(finite, local_values, -np.inf)
    local_index = int(np.nanargmax(safe_values))
    peak_index = left + local_index
    peak_value = float(safe_values[local_index])
    lag_s = abs(float(t[peak_index] - t[center_index]))
    return peak_value, lag_s, int(peak_index)


def create_metric() -> BaseMetric:
    return AccTorqueJerkinessCountMetric()
