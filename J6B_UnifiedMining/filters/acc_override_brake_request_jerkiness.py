from __future__ import annotations

import numpy as np

from filters.acc_torque_jerkiness_count import AccTorqueJerkinessCountMetric
from metric_utils import BaseMetric, MetricResult
from signal_preprocessor import SignalFrame


class AccOverrideBrakeRequestJerkinessMetric(AccTorqueJerkinessCountMetric):
    name = "acc_override_brake_request_jerkiness"
    category = "MPI"
    result_mode = "event_count"
    required_fields = (
        "acceleration",
        "acc_active_flag",
        "pedal_override_flag",
        "brake_request_accel",
        "accelerator_pedal_pos",
        "actual_drive_torque",
        "actual_brake_torque",
    )

    brake_request_accel_threshold = -0.2

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        valid = frame.col("acc_active_flag").astype(bool)
        valid = np.logical_and(valid, frame.col("pedal_override_flag").astype(bool))
        valid = np.logical_and(
            valid,
            frame.col("brake_request_accel").astype(float) < self.brake_request_accel_threshold,
        )
        valid = np.logical_and(valid, frame.col("accelerator_pedal_pos").astype(float) > 0.0)
        if frame.has("ego_speed"):
            valid = np.logical_and(valid, frame.col("ego_speed").astype(float) > 0.0)
        return self._evaluate_with_valid(frame, valid)


def create_metric() -> BaseMetric:
    return AccOverrideBrakeRequestJerkinessMetric()
