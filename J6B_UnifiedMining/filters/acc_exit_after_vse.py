from __future__ import annotations

from typing import List

from filters._acc_exit_correlation import find_correlated_acc_exits
from metric_utils import BaseMetric, MetricEvent, MetricResult
from signal_preprocessor import SignalFrame


class AccExitAfterVseMetric(BaseMetric):
    name = "acc_exit_after_vse"
    category = "MPI"
    result_mode = "event_count"
    required_fields = ("acc_active", "vse_active")

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        events: List[MetricEvent] = []
        correlations = find_correlated_acc_exits(
            frame.time_s,
            frame.col("acc_active"),
            frame.col("vse_active"),
        )
        for trigger_time, exit_time in correlations:
            delay = round(exit_time - trigger_time, 3)
            events.append(
                MetricEvent(
                    start_s=trigger_time,
                    end_s=exit_time,
                    severity=delay,
                    message="VSE activated before ACC exit (%.3fs)" % delay,
                    values={
                        "trigger_time_s": trigger_time,
                        "acc_exit_time_s": exit_time,
                        "delay_s": delay,
                    },
                )
            )
        return MetricResult(
            metric_name=self.name,
            category=self.category,
            result_mode=self.result_mode,
            source_path=frame.source_path,
            distance_km=frame.distance_km,
            distance_status=frame.distance_status,
            total_cases=len(events),
            bad_cases=len(events),
            severity_score=max((event.severity for event in events), default=0.0),
            events=events,
            details=[event.message for event in events],
        )


def create_metric() -> BaseMetric:
    return AccExitAfterVseMetric()
