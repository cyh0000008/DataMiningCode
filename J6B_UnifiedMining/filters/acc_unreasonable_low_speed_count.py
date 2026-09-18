from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import (
    BaseMetric,
    MetricEvent,
    MetricResult,
    Segment,
    display_speed_array,
    event_from_segment,
    filter_segments_for_primary,
    mask_to_segments,
    result_from_events,
)
from signal_preprocessor import SignalFrame


class AccUnreasonableLowSpeedCountMetric(BaseMetric):
    name = "acc_unreasonable_low_speed_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = (
        "time_s",
        "ego_speed",
        "set_speed",
        "acc_active_flag",
        "pedal_override_flag",
        "front_valid",
        "cutin_valid",
        "curv_enable_flag",
        "curv_speed_limit",
    )
    uses_h5_stitching = True
    stable_duration_s = 6.0
    stable_range_kph = 2.0
    min_deficit_kph = 5.0
    min_display_speed_kph = 5.0
    set_speed_stable_range_kph = 0.1
    min_set_speed_kph = 1.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        t = frame.time_s
        display_speed = display_speed_array(frame, "ego_speed")
        set_speed_raw = frame.col("set_speed").astype(float)
        set_speed_display = display_speed_array(frame, "set_speed")
        acc_active = frame.col("acc_active_flag").astype(bool)
        pedal_override = frame.col("pedal_override_flag").astype(bool)
        no_front_target = np.logical_and(
            np.logical_not(frame.col("front_valid", 0).astype(bool)),
            np.logical_not(frame.col("cutin_valid", 0).astype(bool)),
        )

        curv_enabled = frame.col("curv_enable_flag", 0).astype(bool)
        curv_speed_limit_display = display_speed_array(frame, "curv_speed_limit", np.inf)
        curv_not_limiting = np.logical_or.reduce(
            (
                np.logical_not(curv_enabled),
                np.logical_not(np.isfinite(curv_speed_limit_display)),
                curv_speed_limit_display >= set_speed_display,
            )
        )

        base = np.logical_and.reduce(
            (
                acc_active,
                np.logical_not(pedal_override),
                no_front_target,
                curv_not_limiting,
                np.isfinite(display_speed),
                np.isfinite(set_speed_display),
                set_speed_display >= self.min_set_speed_kph,
            )
        )
        stable_segments = filter_segments_for_primary(
            frame,
            self._stable_display_speed_segments(t, display_speed, set_speed_display, base),
        )

        qualifying_segments: List[Segment] = []
        bad_events: List[MetricEvent] = []
        for seg in stable_segments:
            display_window = display_speed[seg.start : seg.end + 1]
            set_window = set_speed_display[seg.start : seg.end + 1]
            if display_window.size == 0 or set_window.size == 0:
                continue
            target_set_speed = float(np.nanmedian(set_window))
            min_display_speed = float(np.nanmin(display_window))
            max_display_speed = float(np.nanmax(display_window))
            speed_range_kph = max_display_speed - min_display_speed
            if min_display_speed < self.min_display_speed_kph or max_display_speed < self.min_display_speed_kph:
                continue
            qualifying_segments.append(seg)
            deficit_kph = target_set_speed - max_display_speed
            if deficit_kph < self.min_deficit_kph:
                continue
            stable_display_speed = float(np.nanmedian(display_window))
            severity = round(deficit_kph, 3)
            bad_events.append(
                event_from_segment(
                    frame,
                    seg,
                    severity,
                    "stable display speed range %.1fkph, max %.1fkph below set speed %.1fkph by %.1fkph"
                    % (speed_range_kph, max_display_speed, target_set_speed, deficit_kph),
                    {
                        "set_speed_kph": round(target_set_speed, 3),
                        "stable_display_speed_kph": round(stable_display_speed, 3),
                        "speed_deficit_kph": round(deficit_kph, 3),
                        "min_display_speed_kph": round(min_display_speed, 3),
                        "max_display_speed_kph": round(max_display_speed, 3),
                        "min_display_speed_threshold_kph": round(self.min_display_speed_kph, 3),
                        "speed_range_kph": round(speed_range_kph, 3),
                        "scene_duration_s": round(seg.duration(t), 3),
                    },
                )
            )

        return result_from_events(self.name, self.category, frame, len(qualifying_segments), bad_events)

    def _stable_display_speed_segments(
        self,
        t: np.ndarray,
        display_speed: np.ndarray,
        set_speed: np.ndarray,
        base_mask: np.ndarray,
    ) -> List[Segment]:
        segments: List[Segment] = []
        for base_seg in mask_to_segments(t, base_mask):
            start = base_seg.start
            while start <= base_seg.end:
                end = start
                set_reference = float(set_speed[start])
                min_speed = float(display_speed[start])
                max_speed = float(display_speed[start])

                while end + 1 <= base_seg.end:
                    candidate = end + 1
                    if abs(float(set_speed[candidate]) - set_reference) > self.set_speed_stable_range_kph:
                        break
                    candidate_speed = float(display_speed[candidate])
                    next_min = min(min_speed, candidate_speed)
                    next_max = max(max_speed, candidate_speed)
                    if next_max - next_min > self.stable_range_kph:
                        break
                    min_speed = next_min
                    max_speed = next_max
                    end = candidate

                if float(t[end] - t[start]) >= self.stable_duration_s:
                    segments.append(Segment(start, end))
                    start = end + 1
                else:
                    start += 1
        return segments


def create_metric() -> BaseMetric:
    return AccUnreasonableLowSpeedCountMetric()
