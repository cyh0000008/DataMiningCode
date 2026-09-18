from __future__ import annotations

import numpy as np

from filters.acc_torque_jerkiness_count import AccTorqueJerkinessCountMetric
from metric_utils import BaseMetric, MetricResult, acc_activation_start_mask
from signal_preprocessor import SignalFrame


class AccStartNoOverrideJerkinessMetric(AccTorqueJerkinessCountMetric):
    name = "acc_start_no_override_jerkiness"
    category = "MPI"
    result_mode = "event_count"
    required_fields = AccTorqueJerkinessCountMetric.required_fields

    start_window_s = 1.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        valid = self._base_valid(frame)
        valid = np.logical_and(valid, acc_activation_start_mask(frame, self.start_window_s))
        return self._evaluate_with_valid(frame, valid)


def create_metric() -> BaseMetric:
    return AccStartNoOverrideJerkinessMetric()
