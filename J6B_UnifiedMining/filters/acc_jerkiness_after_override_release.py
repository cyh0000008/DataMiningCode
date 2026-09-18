from __future__ import annotations

import numpy as np

from filters.acc_torque_jerkiness_count import AccTorqueJerkinessCountMetric
from metric_utils import BaseMetric, MetricResult, post_override_release_mask
from signal_preprocessor import SignalFrame


class AccJerkinessAfterOverrideReleaseMetric(AccTorqueJerkinessCountMetric):
    name = "acc_jerkiness_after_override_release"
    category = "MPI"
    result_mode = "event_count"
    required_fields = AccTorqueJerkinessCountMetric.required_fields

    post_override_window_s = 5.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        valid = self._base_valid(frame)
        valid = np.logical_and(valid, post_override_release_mask(frame, self.post_override_window_s))
        return self._evaluate_with_valid(frame, valid)


def create_metric() -> BaseMetric:
    return AccJerkinessAfterOverrideReleaseMetric()
