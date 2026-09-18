from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, derivative, event_from_segment, mask_to_segments, merge_short_gaps, moving_average, result_from_events
from signal_preprocessor import SignalFrame


class AccHeavyBrakingCountMetric(BaseMetric):
    name = "acc_heavy_braking_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("acceleration", "acc_active_flag", "pedal_override_flag")
    acceleration_smoothing_window = 5
    jerk_smoothing_window = 5
    heavy_brake_accel_threshold = -2.0
    decel_jump_threshold = 3.0
    decel_slope_threshold = 3.0
    min_brake_duration_s = 0.20
    merge_gap_s = 0.50
    decel_jump_window_s = 1.00

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        accel = moving_average(frame.col("acceleration").astype(float), self.acceleration_smoothing_window)
        jerk = moving_average(derivative(frame.time_s, accel), self.jerk_smoothing_window)
        valid = np.logical_and(frame.col("acc_active_flag"), np.logical_not(frame.col("pedal_override_flag")))

        mask = np.logical_and.reduce((
            valid,
            accel < self.heavy_brake_accel_threshold,
        ))
        events: List[MetricEvent] = []
        for seg in mask_to_segments(frame.time_s, merge_short_gaps(frame.time_s, mask, self.merge_gap_s)):
            if seg.duration(frame.time_s) < self.min_brake_duration_s:
                continue

            event_accel = accel[seg.start : seg.end + 1]
            finite_event_accel = event_accel[np.isfinite(event_accel)]
            if finite_event_accel.size == 0:
                continue

            window_start = int(np.searchsorted(frame.time_s, frame.time_s[seg.start] - self.decel_jump_window_s, side="left"))
            window_start = max(0, min(window_start, seg.start))
            window_valid = valid[window_start : seg.end + 1]
            jump_values = accel[window_start : seg.end + 1]
            jump_values = np.where(window_valid, jump_values, np.nan)
            finite_jump_values = jump_values[np.isfinite(jump_values)]
            if finite_jump_values.size == 0:
                continue

            slope_values = jerk[window_start : seg.end + 1]
            finite_slope_values = slope_values[np.isfinite(slope_values)]
            min_acc = float(np.min(finite_event_accel))
            decel_jump = float(np.max(finite_jump_values) - np.min(finite_jump_values))
            decel_slope = max(0.0, -float(np.min(finite_slope_values))) if finite_slope_values.size else 0.0
            if decel_jump < self.decel_jump_threshold or decel_slope < self.decel_slope_threshold:
                continue

            severity = round(
                max(abs(min_acc) - abs(self.heavy_brake_accel_threshold), 0.0) * 50.0
                + max(decel_jump - self.decel_jump_threshold, 0.0) * 25.0
                + max(decel_slope - self.decel_slope_threshold, 0.0) * 10.0,
                3,
            )
            events.append(event_from_segment(
                frame,
                seg,
                severity,
                "heavy braking min_acc=%.3f decel_jump=%.3f decel_slope=%.3f" % (
                    min_acc,
                    decel_jump,
                    decel_slope,
                ),
                {
                    "min_acc": round(min_acc, 3),
                    "decel_jump": round(decel_jump, 3),
                    "decel_slope": round(decel_slope, 3),
                },
            ))
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AccHeavyBrakingCountMetric()
