from __future__ import annotations

from typing import List

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, result_from_events
from signal_preprocessor import SignalFrame


class AccBrakeRapidToggleDecelJumpCountMetric(BaseMetric):
    name = "acc_brake_rapid_toggle_decel_jump_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("acceleration", "brake_request_type", "acc_active_flag", "pedal_override_flag")
    mid_state_max_duration_s = 0.10
    decel_window_s = 1.00
    decel_range_threshold = 2.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        t = frame.time_s
        state = frame.col("brake_request_type")
        accel = frame.col("acceleration").astype(float)
        valid = np.logical_and(frame.col("acc_active_flag"), np.logical_not(frame.col("pedal_override_flag")))
        events: List[MetricEvent] = []
        index = 1
        while index < frame.length:
            if not (valid[index] and int(state[index - 1]) == 2 and int(state[index]) == 1):
                index += 1
                continue
            start = index
            while index + 1 < frame.length and int(state[index + 1]) == 1:
                index += 1
            state1_end = index
            duration = t[state1_end] - t[start]
            return_index = state1_end + 1
            if return_index >= frame.length or duration > self.mid_state_max_duration_s or int(state[return_index]) != 2:
                index += 1
                continue
            end_time = t[return_index] + self.decel_window_s
            end = int(np.searchsorted(t, end_time, side="right") - 1)
            end = max(return_index, min(end, frame.length - 1))
            decel_range = float(np.nanmax(accel[start : end + 1]) - np.nanmin(accel[start : end + 1]))
            if decel_range > self.decel_range_threshold:
                severity = round(decel_range - self.decel_range_threshold, 3)
                events.append(MetricEvent(
                    start_s=float(t[start]),
                    end_s=float(t[end]),
                    severity=severity,
                    message="brake request toggled 2-1-2 with decel range %.3f" % decel_range,
                    values={"decel_range": round(decel_range, 3)},
                ))
            index = return_index + 1
        return result_from_events(self.name, self.category, frame, len(events), events)


def create_metric() -> BaseMetric:
    return AccBrakeRapidToggleDecelJumpCountMetric()
