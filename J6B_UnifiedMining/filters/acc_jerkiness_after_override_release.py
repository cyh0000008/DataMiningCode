from __future__ import annotations

import numpy as np

from filters.acc_torque_jerkiness_count import AccTorqueJerkinessCountMetric
from metric_utils import BaseMetric, MetricResult, Segment, event_from_segment, result_from_events
from signal_preprocessor import SignalFrame


class AccJerkinessAfterOverrideReleaseMetric(AccTorqueJerkinessCountMetric):
    name = "acc_jerkiness_after_override_release"
    category = "MPI"
    result_mode = "event_count"
    required_fields = AccTorqueJerkinessCountMetric.required_fields

    post_override_window_s = 2.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        active = frame.col("acc_active_flag").astype(bool)
        override = frame.col("pedal_override_flag").astype(bool)
        base_valid = self._base_valid(frame)
        release_indices = np.flatnonzero(override[:-1] & ~override[1:]) + 1
        total_windows = 0
        bad_windows = []
        for start in release_indices:
            end = int(np.searchsorted(
                frame.time_s, frame.time_s[start] + self.post_override_window_s, side="right"
            )) - 1
            # Use the available tail when fewer than two seconds remain.
            if end <= start:
                continue
            window = slice(start, end + 1)
            if not np.all(active[window]) or np.any(override[window]):
                continue
            total_windows += 1
            valid = np.zeros(frame.length, dtype=bool)
            valid[window] = base_valid[window]
            result = self._evaluate_with_valid(frame, valid)
            if not result.events:
                continue
            bad_windows.append(event_from_segment(
                frame, Segment(int(start), end),
                max(event.severity for event in result.events),
                "override释放后窗口内检出%d次顿挫，记1个Bad Case" % len(result.events),
                {
                    "release_timestamp": float(frame.time_s[start]),
                    "event_count": len(result.events),
                    "jerkiness_events": [
                        {"start_s": event.start_s, "end_s": event.end_s,
                         "severity": event.severity, "values": event.values}
                        for event in result.events
                    ],
                },
            ))
        return result_from_events(
            self.name, self.category, frame, total_windows, bad_windows, self.result_mode
        )


def create_metric() -> BaseMetric:
    return AccJerkinessAfterOverrideReleaseMetric()
