from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from metric_utils import (
    BaseMetric,
    MetricEvent,
    MetricResult,
    Segment,
    display_speed_array,
    event_from_segment,
    mask_to_segments,
    merge_short_gaps,
    moving_average,
    result_from_events,
)
from signal_preprocessor import SignalFrame


@dataclass(frozen=True)
class StableSceneSegment:
    scene: str
    segment: Segment


class AccBrakeTapCountMetric(BaseMetric):
    name = "acc_brake_tap_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = (
        "time_s",
        "acceleration",
        "ego_speed",
        "set_speed",
        "acc_active_flag",
        "pedal_override_flag",
        "front_valid",
        "cutin_valid",
        "control_brake_request_torque",
        "actual_brake_torque",
    )

    min_speed_kph = 5.0
    stable_duration_s = 8.0
    stable_speed_range_kph = 2.0
    set_speed_stable_range_kph = 0.1
    follow_range_min_m = 5.0
    follow_range_max_m = 100.0
    follow_speed_range_kph = 5.0
    follow_range_stable_span_m = 10.0
    follow_rel_speed_abs_max_mps = 1.5
    brake_prominence_min_nm = 180.0
    brake_threshold_min_nm = 40.0
    min_pulse_duration_s = 0.08
    max_pulse_duration_s = 1.50
    pulse_merge_gap_s = 0.10
    accel_smoothing_window = 5
    accel_context_s = 0.60
    min_acc_drop_mps2 = 0.20

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        t = frame.time_s
        if frame.length < 2:
            return result_from_events(self.name, self.category, frame, 0, [], self.result_mode)

        brake_request = positive_finite(frame.col("control_brake_request_torque").astype(float))
        brake_actual = positive_finite(frame.col("actual_brake_torque").astype(float))
        brake_signal = np.maximum(brake_request, brake_actual)
        accel = moving_average(frame.col("acceleration").astype(float), self.accel_smoothing_window)

        events: List[MetricEvent] = []
        for stable_scene in self._stable_scene_segments(frame):
            events.extend(
                self._brake_tap_events_in_segment(
                    frame,
                    stable_scene,
                    brake_signal,
                    brake_request,
                    brake_actual,
                    accel,
                )
            )

        events.sort(key=lambda item: (item.start_s, item.end_s))
        return result_from_events(self.name, self.category, frame, len(events), events, self.result_mode)

    def _stable_scene_segments(self, frame: SignalFrame) -> List[StableSceneSegment]:
        t = frame.time_s
        display_speed = display_speed_array(frame, "ego_speed")
        set_speed = display_speed_array(frame, "set_speed")
        acc_active = frame.col("acc_active_flag", 0).astype(bool)
        no_override = np.logical_not(frame.col("pedal_override_flag", 0).astype(bool))
        no_cutin = np.logical_not(frame.col("cutin_valid", 0).astype(bool))

        segments: List[StableSceneSegment] = []

        no_front = np.logical_and(no_cutin, np.logical_not(frame.col("front_valid", 0).astype(bool)))
        curv_enabled = frame.col("curv_enable_flag", 0).astype(bool)
        curv_speed_limit = display_speed_array(frame, "curv_speed_limit", np.inf)
        curv_not_limiting = np.logical_or.reduce(
            (
                np.logical_not(curv_enabled),
                np.logical_not(np.isfinite(curv_speed_limit)),
                curv_speed_limit >= set_speed,
            )
        )
        no_front_base = np.logical_and.reduce(
            (
                acc_active,
                no_override,
                no_front,
                curv_not_limiting,
                np.isfinite(display_speed),
                np.isfinite(set_speed),
                display_speed > self.min_speed_kph,
                set_speed >= 1.0,
            )
        )
        for seg in stable_range_segments(
            t,
            no_front_base,
            ((display_speed, self.stable_speed_range_kph), (set_speed, self.set_speed_stable_range_kph)),
            self.stable_duration_s,
        ):
            segments.append(StableSceneSegment("no_front_steady", seg))

        front_range = frame.col("front_range", np.inf).astype(float)
        front_velocity = frame.col("front_velocity_abs", np.nan).astype(float)
        ego_speed_mps = frame.col("ego_speed_mps", np.nan).astype(float)
        follow_base = np.logical_and.reduce(
            (
                acc_active,
                no_override,
                no_cutin,
                frame.col("front_valid", 0).astype(bool),
                np.isfinite(display_speed),
                display_speed > self.min_speed_kph,
                np.isfinite(front_range),
                front_range >= self.follow_range_min_m,
                front_range <= self.follow_range_max_m,
                np.isfinite(front_velocity),
                np.isfinite(ego_speed_mps),
                np.abs(ego_speed_mps - front_velocity) <= self.follow_rel_speed_abs_max_mps,
            )
        )
        for seg in stable_range_segments(
            t,
            merge_short_gaps(t, follow_base, 0.50),
            ((display_speed, self.follow_speed_range_kph), (front_range, self.follow_range_stable_span_m)),
            self.stable_duration_s,
        ):
            segments.append(StableSceneSegment("stable_follow", seg))

        return segments

    def _brake_tap_events_in_segment(
        self,
        frame: SignalFrame,
        stable_scene: StableSceneSegment,
        brake_signal: np.ndarray,
        brake_request: np.ndarray,
        brake_actual: np.ndarray,
        accel: np.ndarray,
    ) -> List[MetricEvent]:
        seg = stable_scene.segment
        t = frame.time_s
        segment_brake = brake_signal[seg.start : seg.end + 1]
        if not np.isfinite(segment_brake).any():
            return []

        baseline = float(np.nanpercentile(segment_brake, 20))
        threshold = baseline + max(self.brake_threshold_min_nm, self.brake_prominence_min_nm * 0.35)
        above = brake_signal > threshold
        pulse_mask = np.zeros(frame.length, dtype=bool)
        pulse_mask[seg.start : seg.end + 1] = above[seg.start : seg.end + 1]
        pulse_mask = merge_short_gaps(t, pulse_mask, self.pulse_merge_gap_s)

        events: List[MetricEvent] = []
        for pulse in mask_to_segments(t, pulse_mask):
            pulse_duration = pulse.duration(t)
            if pulse_duration < self.min_pulse_duration_s or pulse_duration > self.max_pulse_duration_s:
                continue

            brake_window = brake_signal[pulse.start : pulse.end + 1]
            brake_peak = float(np.nanmax(brake_window))
            brake_prominence = brake_peak - baseline
            if brake_prominence < self.brake_prominence_min_nm:
                continue

            context_start = max(seg.start, int(np.searchsorted(t, t[pulse.start] - self.accel_context_s, side="left")))
            context_end = min(seg.end, int(np.searchsorted(t, t[pulse.end] + self.accel_context_s, side="right") - 1))
            acc_before = finite_median(accel[context_start : pulse.start + 1])
            min_acc = finite_min(accel[pulse.start : context_end + 1])
            if acc_before is None or min_acc is None:
                continue
            acc_drop = float(acc_before - min_acc)
            if acc_drop < self.min_acc_drop_mps2:
                continue

            request_peak = float(np.nanmax(brake_request[pulse.start : pulse.end + 1]))
            actual_peak = float(np.nanmax(brake_actual[pulse.start : pulse.end + 1]))
            severity = round(brake_prominence / 100.0 + acc_drop * 10.0, 3)
            values = {
                "scene": stable_scene.scene,
                "scene_duration_s": round(seg.duration(t), 3),
                "brake_source": brake_source(request_peak, actual_peak, threshold),
                "brake_peak_nm": round(brake_peak, 3),
                "brake_prominence_nm": round(brake_prominence, 3),
                "brake_request_peak_nm": round(request_peak, 3),
                "brake_actual_peak_nm": round(actual_peak, 3),
                "pulse_duration_s": round(pulse_duration, 3),
                "acc_before_mps2": round(acc_before, 3),
                "min_acc_mps2": round(min_acc, 3),
                "acc_drop_mps2": round(acc_drop, 3),
                "display_speed_kph": round(
                    finite_median(display_speed_array(frame, "ego_speed")[pulse.start : pulse.end + 1]) or 0.0,
                    3,
                ),
                "set_speed_kph": round(
                    finite_median(display_speed_array(frame, "set_speed")[pulse.start : pulse.end + 1]) or 0.0,
                    3,
                ),
                "front_range_m": finite_round(frame.col("front_range", np.inf)[pulse.start : pulse.end + 1]),
                "relative_speed_mps": finite_round(
                    frame.col("ego_speed_mps", np.nan)[pulse.start : pulse.end + 1]
                    - frame.col("front_velocity_abs", np.nan)[pulse.start : pulse.end + 1]
                ),
            }
            events.append(
                event_from_segment(
                    frame,
                    pulse,
                    severity,
                    "%s brake tap peak=%.1fNm acc_drop=%.3fm/s2 duration=%.2fs"
                    % (stable_scene.scene, brake_peak, acc_drop, pulse_duration),
                    values,
                )
            )

        return events


def stable_range_segments(
    t: np.ndarray,
    base_mask: np.ndarray,
    series_and_spans: Tuple[Tuple[np.ndarray, float], ...],
    min_duration_s: float,
) -> List[Segment]:
    segments: List[Segment] = []
    for base_seg in mask_to_segments(t, base_mask):
        start = base_seg.start
        end = start
        mins, maxs = initial_ranges(series_and_spans, start)
        if mins is None or maxs is None:
            continue

        index = start + 1
        while index <= base_seg.end:
            next_mins, next_maxs = extend_ranges(series_and_spans, mins, maxs, index)
            if next_mins is None or next_maxs is None or ranges_exceed_spans(next_mins, next_maxs, series_and_spans):
                if t[end] - t[start] >= min_duration_s:
                    segments.append(Segment(start, end))
                start = index
                end = index
                mins, maxs = initial_ranges(series_and_spans, start)
                index += 1
                continue
            mins, maxs = next_mins, next_maxs
            end = index
            index += 1

        if mins is not None and maxs is not None and t[end] - t[start] >= min_duration_s:
            segments.append(Segment(start, end))

    return segments


def initial_ranges(
    series_and_spans: Tuple[Tuple[np.ndarray, float], ...],
    index: int,
) -> Tuple[List[float] | None, List[float] | None]:
    values = []
    for series, _span in series_and_spans:
        value = float(series[index])
        if not np.isfinite(value):
            return None, None
        values.append(value)
    return list(values), list(values)


def extend_ranges(
    series_and_spans: Tuple[Tuple[np.ndarray, float], ...],
    mins: List[float],
    maxs: List[float],
    index: int,
) -> Tuple[List[float] | None, List[float] | None]:
    next_mins = list(mins)
    next_maxs = list(maxs)
    for item_index, (series, _span) in enumerate(series_and_spans):
        value = float(series[index])
        if not np.isfinite(value):
            return None, None
        next_mins[item_index] = min(next_mins[item_index], value)
        next_maxs[item_index] = max(next_maxs[item_index], value)
    return next_mins, next_maxs


def ranges_exceed_spans(
    mins: List[float],
    maxs: List[float],
    series_and_spans: Tuple[Tuple[np.ndarray, float], ...],
) -> bool:
    return any((maxs[index] - mins[index]) > span for index, (_series, span) in enumerate(series_and_spans))


def positive_finite(values: np.ndarray) -> np.ndarray:
    out = np.where(np.isfinite(values), values, 0.0)
    return np.where(out > 0.0, out, 0.0)


def finite_median(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(np.nanmedian(finite))


def finite_min(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(np.nanmin(finite))


def finite_round(values: np.ndarray) -> float | None:
    median = finite_median(values.astype(float))
    if median is None:
        return None
    return round(median, 3)


def brake_source(request_peak: float, actual_peak: float, threshold: float) -> str:
    request_active = request_peak > threshold
    actual_active = actual_peak > threshold
    if request_active and actual_active:
        return "request_and_actual"
    if request_active:
        return "request"
    if actual_active:
        return "actual"
    return "combined"


def create_metric() -> BaseMetric:
    return AccBrakeTapCountMetric()
