from __future__ import annotations

from typing import List

from metric_utils import BaseMetric, MetricEvent, MetricResult, enum_mask, event_from_segment, mask_to_segments, result_from_events
from signal_preprocessor import SignalFrame


class AbsActivationCountMetric(BaseMetric):
    name = "abs_activation_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("abs_active",)
    active_values = {1}

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        mask = enum_mask(frame.col("abs_active"), self.active_values)
        events: List[MetricEvent] = []
        for seg in mask_to_segments(frame.time_s, mask):
            severity = round(seg.duration(frame.time_s), 3)
            events.append(
                event_from_segment(
                    frame,
                    seg,
                    severity,
                    "ABS active for %.3fs" % severity,
                )
            )
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AbsActivationCountMetric()
