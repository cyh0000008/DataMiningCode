from __future__ import annotations

from typing import List

from metric_utils import BaseMetric, MetricEvent, MetricResult, Segment, event_from_segment, find_scene_segments, measure_segment, result_from_events, severity_outside, within_bounds
from signal_preprocessor import SignalFrame
from powertrain_utils import value_for_powertrain


class FollowStopMinFrntobjDisMetric(BaseMetric):
    name = "follow_stop_min_frntobj_dis"
    category = "CPI"
    required_fields = ("time_s", "acceleration_total")
    scene = "follow_stop"
    measure = "stop_front_distance"
    lower = 3.0
    upper = 4.0
    severity_base = 2.0
    bad_when_missing = False

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        segments = find_scene_segments(frame, self.scene)
        events: List[MetricEvent] = []
        excluded: List[str] = []
        measured = 0
        for seg in segments:
            value, value_time, extra = measure_segment(frame, seg, self.measure)
            if value is None:
                excluded.append(
                    "未统计：刹停时刻%.3fs，%s" % (
                        frame.time_s[seg.end], extra["excluded_reason"],
                    )
                )
                continue
            measured += 1
            lower, upper = value_for_powertrain(
                self.powertrain_type, ice_value=(2.5, 4.5),
                phev_value=(3.0, 4.0), bev_value=(3.0, 4.0)
            )
            if self.measure == "min_acc_dynamic":
                lower = extra.get("dynamic_lower", lower)
            if self.measure == "curvature_min_speed":
                lower = extra.get("lower_bound")
                upper = extra.get("upper_bound")
            if within_bounds(value, lower, upper):
                continue
            severity = severity_outside(value, lower, upper, self.severity_base)
            extra = dict(extra)
            extra.update(screening_measure=self.measure, screening_value=value, screening_lower=lower, screening_upper=upper)
            extra[self.measure] = round(float(value), 6)
            if value_time is not None:
                extra["value_time_s"] = round(float(value_time), 3)
            events.append(
                event_from_segment(
                    frame,
                    Segment(seg.start, int(extra["observation_end_index"])),
                    severity,
                    "%s=%.6f outside [%s, %s]" % (self.measure, value, lower, upper),
                    extra,
                )
            )
        result = result_from_events(self.name, self.category, frame, measured, events)
        result.details.extend(excluded)
        return result


def create_metric() -> BaseMetric:
    return FollowStopMinFrntobjDisMetric()
