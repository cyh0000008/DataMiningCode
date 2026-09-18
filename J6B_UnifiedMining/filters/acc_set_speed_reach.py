from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, display_speed_array, event_from_segment, mask_to_segments, result_from_events
from signal_preprocessor import SignalFrame


class AccSetSpeedReachMetric(BaseMetric):
    name = "acc_set_speed_reach"
    category = "MPI"
    required_fields = (
        "ego_speed",
        "set_speed",
        "acc_active_flag",
        "pedal_override_flag",
        "front_valid",
        "cutin_valid",
    )
    target_band_kph = 1.5
    min_scene_duration_s = 8.0
    stable_range_kph = 2.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        speed = display_speed_array(frame, "ego_speed")
        set_speed_raw = frame.col("set_speed").astype(float)
        set_speed = display_speed_array(frame, "set_speed")
        no_front_target = np.logical_and(
            np.logical_not(frame.col("front_valid", 0).astype(bool)),
            np.logical_not(frame.col("cutin_valid", 0).astype(bool)),
        )
        base = np.logical_and.reduce(
            (
                frame.col("acc_active_flag").astype(bool),
                np.logical_not(frame.col("pedal_override_flag").astype(bool)),
                no_front_target,
                set_speed_raw > 0.0,
            )
        )
        near = np.logical_and(base, np.abs(speed - set_speed) <= self.target_band_kph)

        all_cases = 0
        bad_events: List[MetricEvent] = []
        for seg in mask_to_segments(frame.time_s, near):
            if seg.duration(frame.time_s) < self.min_scene_duration_s:
                continue
            speed_window = speed[seg.start : seg.end + 1]
            set_window = set_speed[seg.start : seg.end + 1]
            if speed_window.size == 0:
                continue
            if np.nanmax(speed_window) - np.nanmin(speed_window) > self.stable_range_kph:
                continue
            if np.nanmax(set_window) - np.nanmin(set_window) > 1e-6:
                continue
            all_cases += 1
            target = float(np.nanmean(set_window))
            max_speed = float(np.nanmax(speed_window))
            if max_speed >= target:
                continue
            severity = round(max(target - max_speed, 0.0), 3)
            bad_events.append(
                event_from_segment(
                    frame,
                    seg,
                    severity,
                    "display speed %.1f did not reach set speed %.1f" % (max_speed, target),
                    {"set_speed": round(target, 3), "max_display_speed": round(max_speed, 3)},
                )
            )
        return result_from_events(self.name, self.category, frame, all_cases, bad_events)


def create_metric() -> BaseMetric:
    return AccSetSpeedReachMetric()
