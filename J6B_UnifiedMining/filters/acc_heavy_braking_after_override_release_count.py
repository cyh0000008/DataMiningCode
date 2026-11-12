from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, Segment, JERK_SMOOTHING_WINDOW_S, event_from_segment, jerk_from_acceleration, mask_to_segments, merge_short_gaps, result_from_events, time_window_moving_average
from override_braking_torque import find_torque_braking_events
from signal_preprocessor import SignalFrame


class AccHeavyBrakingAfterOverrideReleaseCountMetric(BaseMetric):
    name = "acc_heavy_braking_after_override_release_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("acceleration", "acc_active_flag", "pedal_override_flag",
                       "actual_drive_torque", "actual_brake_torque")
    heavy_brake_accel_threshold = -2.0
    heavy_brake_jerk_threshold = -3.0
    min_brake_duration_s = 0.20
    merge_gap_s = 0.50

    def run(self, frame: SignalFrame) -> MetricResult:
        result = super().run(frame)
        if result.status in ("SKIPPED", "ERROR"):
            result.distance_km = None
        return result

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        acceleration = frame.col("acceleration").astype(float)
        t = frame.time_s
        primary_length = frame.primary_length if frame.primary_length is not None else len(t)
        if any(not np.any(np.isfinite(frame.col(name)[:primary_length]))
               for name in ("actual_drive_torque", "actual_brake_torque")):
            raise ValueError("实车驱动或制动扭矩无有效数据，Override后重制动未统计")

        def detect_braking(release: int, onset: int, end: int) -> dict | None:
            left = max(0, int(np.searchsorted(t, t[release] - 0.6)) - 1)
            local_t = t[left:end + 1]
            local_accel = time_window_moving_average(local_t, acceleration[left:end + 1], JERK_SMOOTHING_WINDOW_S)
            local_jerk = jerk_from_acceleration(local_t, acceleration[left:end + 1])
            mask = (local_accel < self.heavy_brake_accel_threshold) & (local_jerk < self.heavy_brake_jerk_threshold)
            local_release = release - left
            if mask[local_release - 1]:
                return None
            scene_mask = mask[local_release:]
            merged = merge_short_gaps(t[release:end + 1], scene_mask, self.merge_gap_s)
            for candidate in mask_to_segments(t[release:end + 1], merged):
                seg = Segment(candidate.start + release, candidate.end + release)
                if (t[seg.start] > t[release] + 0.2 + 1e-9
                        or seg.end < onset or seg.duration(t) + 1e-9 < self.min_brake_duration_s):
                    continue
                return {"start_index": seg.start, "end_index": seg.end,
                        "min_acc": float(np.nanmin(local_accel[seg.start - left:seg.end - left + 1])),
                        "min_jerk": float(np.nanmin(local_jerk[seg.start - left:seg.end - left + 1]))}
            return None

        primary_end = t[frame.primary_length - 1] if frame.primary_length is not None else float("inf")
        matches = find_torque_braking_events(
            t, frame.col("acc_active_flag"), frame.col("pedal_override_flag"),
            frame.col("actual_drive_torque"), frame.col("actual_brake_torque"),
            detect_braking, primary_end,
        )
        events: List[MetricEvent] = []
        for match in matches:
            seg = Segment(match["start_index"], match["end_index"])
            min_acc = match["min_acc"]
            min_jerk = match["min_jerk"]
            severity = round(
                max(abs(min_acc) - abs(self.heavy_brake_accel_threshold), 0.0) * 50.0
                + max(abs(min_jerk) - abs(self.heavy_brake_jerk_threshold), 0.0) * 25.0,
                3,
            )
            events.append(event_from_segment(
                frame,
                seg,
                severity,
                "heavy braking min_acc=%.3f min_jerk=%.3f torque=%s rate=%.1f change=%.1f"
                % (min_acc, min_jerk, match["torque_type"], match["torque_rate"], match["torque_change"]),
                {"min_acc": round(min_acc, 3), "min_jerk": round(min_jerk, 3),
                 "release_timestamp": float(t[match["release_index"]]),
                 "torque_onset_time_s": float(t[match["torque_onset_index"]]),
                 "torque_confirm_time_s": float(t[match["torque_confirm_index"]]),
                 "torque_type": match["torque_type"], "torque_rate": match["torque_rate"],
                 "torque_change": match["torque_change"]},
            ))
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AccHeavyBrakingAfterOverrideReleaseCountMetric()
