from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, derivative, event_from_segment, mask_to_segments, merge_short_gaps, moving_average, post_override_release_mask, result_from_events
from signal_preprocessor import SignalFrame


class AccHeavyBrakingAfterOverrideReleaseCountMetric(BaseMetric):
    name = "acc_heavy_braking_after_override_release_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("acceleration", "acc_active_flag", "pedal_override_flag")
    acceleration_smoothing_window = 5
    jerk_smoothing_window = 5
    heavy_brake_accel_threshold = -2.0
    heavy_brake_jerk_threshold = -3.0
    min_brake_duration_s = 0.20
    merge_gap_s = 0.50
    post_override_window_s = 5.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        accel = moving_average(frame.col("acceleration").astype(float), self.acceleration_smoothing_window)
        jerk = moving_average(derivative(frame.time_s, accel), self.jerk_smoothing_window)
        valid = np.logical_and(frame.col("acc_active_flag"), np.logical_not(frame.col("pedal_override_flag")))
        valid = np.logical_and(valid, post_override_release_mask(frame, self.post_override_window_s))
        mask = np.logical_and.reduce((
            valid,
            accel < self.heavy_brake_accel_threshold,
            jerk < self.heavy_brake_jerk_threshold,
        ))
        events: List[MetricEvent] = []
        for seg in mask_to_segments(frame.time_s, merge_short_gaps(frame.time_s, mask, self.merge_gap_s)):
            if seg.duration(frame.time_s) < self.min_brake_duration_s:
                continue
            min_acc = float(np.nanmin(accel[seg.start : seg.end + 1]))
            min_jerk = float(np.nanmin(jerk[seg.start : seg.end + 1]))
            severity = round(
                max(abs(min_acc) - abs(self.heavy_brake_accel_threshold), 0.0) * 50.0
                + max(abs(min_jerk) - abs(self.heavy_brake_jerk_threshold), 0.0) * 25.0,
                3,
            )
            events.append(event_from_segment(
                frame,
                seg,
                severity,
                "heavy braking min_acc=%.3f min_jerk=%.3f" % (min_acc, min_jerk),
                {"min_acc": round(min_acc, 3), "min_jerk": round(min_jerk, 3)},
            ))
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AccHeavyBrakingAfterOverrideReleaseCountMetric()
