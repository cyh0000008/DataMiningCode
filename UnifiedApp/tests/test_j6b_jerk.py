import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class J6BJerkTests(unittest.TestCase):
    def run_j6b(self, script):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_windowed_slope_and_numerical_stability(self):
        self.run_j6b(r'''
import numpy as np
from metric_utils import (
    JERK_REGRESSION_WINDOW_S, JERK_SMOOTHING_WINDOW_S,
    jerk_from_acceleration, time_window_linear_slope, time_window_moving_average,
)

assert JERK_SMOOTHING_WINDOW_S == JERK_REGRESSION_WINDOW_S == 0.3
rng = np.random.default_rng(42)
t = np.cumsum(rng.uniform(0.005, 0.015, 12000))
values = -0.7 * t + np.sin(t) + rng.normal(0, 0.02, t.size)
for offset in (0.0, 1_700_000_000.0):
    times = t + offset
    fitted = time_window_linear_slope(times, values, 0.3)
    for i in list(range(0, t.size, 97)) + [2047, 2048, 4095, 4096, t.size - 1]:
        window = (times >= times[i] - 0.15) & (times <= times[i] + 0.15)
        x, y = times[window], values[window]
        if x.size < 3 or x[-1] - x[0] < 0.15 - 1e-9:
            assert np.isnan(fitted[i])
            continue
        x = x - x.mean()
        expected = np.dot(x, y - y.mean()) / np.dot(x, x)
        np.testing.assert_allclose(fitted[i], expected, atol=2e-6, rtol=2e-6)

t = np.arange(0, 5, 0.01)
for slope in (-4.0, 0.0, 3.0):
    values = 2 + slope * t
    jerk = jerk_from_acceleration(t, values)
    np.testing.assert_allclose(jerk[(t > 0.31) & (t < 4.68)], slope, atol=1e-8)

local = np.arange(0, 10, 0.01)
t = np.r_[local, 1_000_000 + local]
values = np.r_[3 * local, 3 * local]
supported = ((t > 0.31) & (t < 9.69)) | ((t > 1_000_000.31) & (t < 1_000_009.69))
np.testing.assert_allclose(jerk_from_acceleration(t, values)[supported], 3, atol=1e-7)

# Near-coincident samples and a quantized acceleration step must not create
# the enormous jerk produced by dividing one moving-mean jump by a tiny dt.
t = np.sort(np.r_[np.arange(0, 5, 0.01), 2.0 + 1e-7])
values = np.where(t >= 2.15, 0.02, 0.0)
smoothed = time_window_moving_average(t, values, 0.3)
adjacent_peak = np.max(np.abs(np.diff(smoothed) / np.diff(t)))
jerk = jerk_from_acceleration(t, values)
assert np.nanmax(np.abs(jerk)) < 0.2
assert adjacent_peak > np.nanmax(np.abs(jerk)) * 100

for t in (np.array([]), np.array([0.]), np.array([0., 0.1]),
          np.array([0., 1e-7, 2e-7])):
    result = time_window_linear_slope(t, np.ones(t.size), 0.3)
    assert result.shape == t.shape and np.all(np.isnan(result))
t = np.arange(0, 1, 0.01)
values = 2 * t
values[20:40] = np.nan
result = time_window_linear_slope(t, values, 0.3)
np.testing.assert_allclose(result[np.isfinite(result)], 2, atol=1e-9)
assert np.all(np.isnan(time_window_linear_slope(t, np.full(t.size, np.nan), 0.3)))
for times, values, window in (
    ([0, 0], [1, 2], 0.3), ([1, 0], [1, 2], 0.3),
    ([0, np.nan], [1, 2], 0.3), ([0, 1], [1], 0.3),
    ([0, 1], [1, 2], 0), ([0, 1], [1, 2], float("nan")),
):
    try:
        time_window_linear_slope(np.array(times), np.array(values), window)
    except ValueError:
        pass
    else:
        raise AssertionError((times, values, window))
''')

    def test_metrics_and_plots_share_the_same_jerk(self):
        self.run_j6b(r'''
import numpy as np
from unittest.mock import patch
import metric_utils
from metric_utils import Segment, jerk_from_acceleration, measure_segment
from signal_preprocessor import SignalFrame
from bad_case_image_report import build_panels
from filters import (
    acc_heavy_braking_count, acc_heavy_braking_after_override_release_count,
    acc_torque_jerkiness_count,
)

t = np.arange(0, 4, 0.01)
acceleration = np.sin(3 * t)
frame = SignalFrame("synthetic", "", {}, {
    "time_s": t, "acceleration": acceleration, "acceleration_total": acceleration,
    "acc_active_flag": np.ones(t.size, dtype=bool),
    "pedal_override_flag": np.zeros(t.size, dtype=bool),
    "actual_drive_torque": np.zeros(t.size), "actual_brake_torque": np.zeros(t.size),
}, {})
expected = jerk_from_acceleration(t, acceleration)
for name, calculation in (("min_jerk", np.nanmin), ("max_jerk", np.nanmax),
                          ("max_abs_jerk", lambda x: np.nanmax(np.abs(x)))):
    value, when, _ = measure_segment(frame, Segment(30, 350), name)
    np.testing.assert_allclose(value, calculation(expected[30:351]))
    assert t[30] <= when <= t[350]
for module in (acc_heavy_braking_count, acc_heavy_braking_after_override_release_count,
               acc_torque_jerkiness_count):
    with patch.object(module, "jerk_from_acceleration", wraps=jerk_from_acceleration) as calculate:
        module.create_metric().evaluate(frame)
        calculate.assert_called_once()
        np.testing.assert_array_equal(calculate.call_args.args[1], acceleration)
with patch.object(metric_utils, "jerk_from_acceleration", wraps=jerk_from_acceleration) as calculate:
    metric_utils.jerkiness_events(frame, np.ones(t.size, dtype=bool), acceleration)
    calculate.assert_called_once()
panels = build_panels(frame, "follow_stop_min_jerk", np.ones(t.size, dtype=bool))
acc_panel = next(panel for panel in panels if panel.title == "纵向加速度")
jerk_panel = next(panel for panel in panels if "持续 jerk" in panel.title)
assert acc_panel.unit == "m/s2" and jerk_panel.unit == "m/s3"
assert len(acc_panel.lines) == 2
np.testing.assert_allclose(jerk_panel.lines[0].values, expected)
''')


if __name__ == "__main__":
    unittest.main()
