from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, enum_mask, event_from_segment, mask_to_segments, result_from_events
from signal_preprocessor import SignalFrame


class AccRollbackCountMetric(BaseMetric):
    name = "acc_rollback_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = (
        "wheel_dir_lf", "wheel_dir_rf", "wheel_dir_lr", "wheel_dir_rr",
        "ego_speed_non_driven", "acc_active_flag", "brake_request_type",
        "wheel_speed_lf", "wheel_speed_rf", "wheel_speed_lr", "wheel_speed_rr", "gear",
    )
    min_scene_duration_s = 0.20
    min_wheel_speed_abs = 0.01
    max_vehicle_speed_kph = 0.5

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        reverse = enum_mask(frame.col("wheel_dir_lf"), {4})
        for name in ("wheel_dir_rf", "wheel_dir_lr", "wheel_dir_rr"):
            reverse = np.logical_and(reverse, enum_mask(frame.col(name), {4}))
        wheel_valid = np.zeros(frame.length, dtype=bool)
        for name in ("wheel_speed_lf", "wheel_speed_rf", "wheel_speed_lr", "wheel_speed_rr"):
            wheel_valid = np.logical_or(wheel_valid, np.abs(frame.col(name).astype(float)) >= self.min_wheel_speed_abs)
        mask = np.logical_and.reduce([
            reverse,
            wheel_valid,
            frame.col("acc_active_flag"),
            enum_mask(frame.col("brake_request_type"), {5}),
            enum_mask(frame.col("gear"), {12}),
            frame.col("ego_speed_non_driven").astype(float) <= self.max_vehicle_speed_kph,
        ])
        events: List[MetricEvent] = []
        for seg in mask_to_segments(frame.time_s, mask):
            duration = seg.duration(frame.time_s)
            if duration < self.min_scene_duration_s:
                continue
            events.append(event_from_segment(frame, seg, round(duration, 3), "rollback detected for %.3fs" % duration))
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AccRollbackCountMetric()
