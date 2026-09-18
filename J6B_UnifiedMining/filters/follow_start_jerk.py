from __future__ import annotations

from typing import List

from metric_utils import BaseMetric, MetricEvent, MetricResult, event_from_segment, find_scene_segments, measure_segment, result_from_events, severity_outside, within_bounds
from signal_preprocessor import SignalFrame


class FollowStartJerkMetric(BaseMetric):
    name = "follow_start_jerk"
    category = "CPI"
    required_fields = ("time_s", "acceleration_total")
    scene = "follow_start"
    measure = "max_jerk"
    lower = 1.0
    upper = 2.5
    severity_base = 1.0
    bad_when_missing = False

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        segments = find_scene_segments(frame, self.scene)
        events: List[MetricEvent] = []
        measured = 0
        for seg in segments:
            value, value_time, extra = measure_segment(frame, seg, self.measure)
            if value is None:
                if self.bad_when_missing:
                    events.append(event_from_segment(frame, seg, 100.0, "%s missing" % self.measure, extra))
                continue
            measured += 1
            lower = self.lower
            upper = self.upper
            if self.measure == "min_acc_dynamic":
                lower = extra.get("dynamic_lower", lower)
            if self.measure == "curvature_min_speed":
                lower = extra.get("lower_bound")
                upper = extra.get("upper_bound")
            if within_bounds(value, lower, upper):
                continue
            severity = severity_outside(value, lower, upper, self.severity_base)
            extra = dict(extra)
            extra[self.measure] = round(float(value), 6)
            if value_time is not None:
                extra["value_time_s"] = round(float(value_time), 3)
            events.append(
                event_from_segment(
                    frame,
                    seg,
                    severity,
                    "%s=%.6f outside [%s, %s]" % (self.measure, value, lower, upper),
                    extra,
                )
            )
        total_cases = len(segments) if segments else measured
        return result_from_events(self.name, self.category, frame, total_cases, events)


def create_metric() -> BaseMetric:
    return FollowStartJerkMetric()
