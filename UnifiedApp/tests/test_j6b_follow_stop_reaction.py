import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class FollowStopReactionTests(unittest.TestCase):
    def test_first_ego_decel_ttc_metric_and_plot(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from unittest.mock import patch
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image
from signal_preprocessor import SignalFrame
from metric_utils import Segment, measure_segment, metric_required_fields
from filters.follow_stop_ttc import create_metric
from bad_case_image_report import CaseRow, build_evidence_panel, draw_case_plot

t = np.arange(8, dtype=float)
def frame(acc, ttc):
    return SignalFrame("synthetic", "", {}, {
        "time_s": t, "acceleration": np.array(acc, dtype=float),
        "acceleration_total": np.array(acc, dtype=float),
        "front_ttc": np.array(ttc, dtype=float),
        "cutin_valid": np.zeros(8), "cutin_ttc_calc": np.full(8, np.inf),
        "front_accel": np.full(8, -2.),
        "ego_speed_mps": np.array([4., 4., 3., 2., 1., .5, 0., 0.]),
        "ego_speed": np.array([4., 4., 3., 2., 1., .5, 0., 0.]) * 3.6,
        "acc_active_flag": np.ones(8),
        "front_valid": np.ones(8), "front_range": np.full(8, 20.),
        "front_target_id": np.ones(8),
        "moving_flag": np.array([1, 1, 1, 1, 1, 1, 0, 0]),
        "stationary_flag": np.array([0, 0, 0, 0, 0, 0, 1, 1]),
        "front_velocity_abs": np.zeros(8),
    }, {})

metric = create_metric()
assert metric.measure == "ttc_at_ego_decel" and metric.lower == 3.5
assert {"front_ttc", "acceleration_total"} <= set(metric_required_fields(metric))
# Later smaller TTC must not replace the first qualifying sample.
good = frame([0, -.49, -.5, -1, -2, -1, 0, 0], [8, 7, 3.5, 2, 1, 2, 4, 5])
value, when, extra = measure_segment(good, Segment(0, 6), metric.measure)
assert value == 3.5 and when == 2.
assert extra["ego_acceleration_at_ttc_mps2"] == -.5
result = metric.evaluate(good)
assert result.total_cases == 1 and result.bad_cases == 0
bad = frame([0, -.49, -.5, -1, -2, -1, 0, 0], [8, 7, 3.4, 2, 1, 2, 4, 5])
result = metric.evaluate(bad)
assert result.total_cases == result.bad_cases == 1
event = result.events[0]
assert event.values["screening_value"] == 3.4
assert event.values["value_time_s"] == 2.
assert event.values["screening_lower"] == 3.5
case = CaseRow(2, metric.name, "synthetic", 0., 6., result.severity_score, "", event.values)
panel = build_evidence_panel(bad, case, np.ones(8, dtype=bool))
assert panel.unit == "s"
assert np.flatnonzero(panel.highlight).tolist() == [2]
assert panel.lines[0].values[4] == 1.  # Context retained, not counted as evidence.
with tempfile.TemporaryDirectory() as folder:
    image_path = Path(folder) / "reaction.png"
    draw_case_plot(bad, case, image_path)
    with Image.open(image_path) as image:
        image.verify()
# Ignore non-finite acceleration/TTC, never use a later scene's samples.
missing = frame([0, -np.inf, np.nan, -.5, -.8, -1, 0, -2],
                [8, 7, 6, np.inf, np.nan, 3.6, 2, .5])
value, when, _ = measure_segment(missing, Segment(0, 6), metric.measure)
assert value == 3.6 and when == 5.
no_decel = frame([0, -.49, -.49, -.49, -.49, -.49, 0, -2], [1]*8)
value, when, _ = measure_segment(no_decel, Segment(0, 6), metric.measure)
assert value is None and when is None
result = metric.evaluate(no_decel)
assert result.total_cases == 1 and result.bad_cases == 0
all_invalid = frame([-1]*8, [np.nan, np.inf, np.nan, np.inf, np.nan, np.inf, np.nan, .1])
assert measure_segment(all_invalid, Segment(0, 6), metric.measure)[0] is None
# Existing minimum-TTC measures used by other metrics keep their behavior.
assert measure_segment(good, Segment(0, 6), "min_ttc")[0] == 1.
'''],
            cwd=ROOT / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
