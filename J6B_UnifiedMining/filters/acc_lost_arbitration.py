from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, enum_mask, event_from_segment, mask_to_segments, result_from_events
from signal_preprocessor import SignalFrame


class AccLostArbitrationMetric(BaseMetric):
    name = "acc_lost_arbitration"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("acc_active_flag", "acc_request_state")
    min_bad_case_duration_s = 5.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        mask = np.logical_and(
            frame.col("acc_active_flag").astype(bool),
            enum_mask(frame.col("acc_request_state"), {2}),
        )
        events: List[MetricEvent] = []
        for seg in mask_to_segments(frame.time_s, mask):
            duration = seg.duration(frame.time_s)
            if duration < self.min_bad_case_duration_s:
                continue
            events.append(
                event_from_segment(
                    frame,
                    seg,
                    round(duration, 3),
                    "ACC lost arbitration for %.3fs" % duration,
                )
            )
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AccLostArbitrationMetric()
