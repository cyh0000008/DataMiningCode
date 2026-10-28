import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class J6BContinuousSceneTests(unittest.TestCase):
    def test_curve_and_slope_never_bridge_invalid_samples(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from copy import deepcopy
import importlib
import numpy as np
from metric_utils import curvature_segments, slope_no_front_segments, find_scene_segments
from signal_preprocessor import SignalFrame

t = np.round(np.arange(0, 12.01, 0.1), 6)
fields = {
    "time_s": t, "acc_active_flag": np.ones(t.size, dtype=bool),
    "moving_flag": np.ones(t.size, dtype=bool),
    "ego_speed_mps": np.full(t.size, 10.),
    "curv_enable_flag": np.ones(t.size, dtype=bool),
    "front_ttc": np.full(t.size, np.inf), "curvature": np.full(t.size, 0.005),
    "slope_acc": np.full(t.size, 0.2), "front_valid": np.zeros(t.size, dtype=bool),
    "curv_speed_limit": np.full(t.size, 20.), "set_speed": np.full(t.size, 36.),
}
frame = SignalFrame("synthetic", "", {}, fields, {})
for function, changes in (
    (curvature_segments, (
        ("acc_active_flag", False), ("curv_enable_flag", False),
        ("moving_flag", False), ("ego_speed_mps", 1), ("front_ttc", 4.49),
    )),
    (slope_no_front_segments, (
        ("acc_active_flag", False), ("moving_flag", False), ("slope_acc", 0),
        ("front_valid", True), ("curv_speed_limit", 9), ("ego_speed_mps", 14),
    )),
):
    assert [(s.start, s.end) for s in function(frame)] == [(0, 120)]
    for field, value in changes:
        for count in (1, 2, 4):
            changed = deepcopy(frame)
            changed.fields[field][60:60 + count] = value
            segments = function(changed)
            assert [(s.start, s.end) for s in segments] == [(0, 59), (60 + count, 120)], (field, count, segments)
            assert all(not (s.start <= 60 <= s.end) for s in segments)
    # Individually too-short halves must not qualify through a one-frame gap.
    short = deepcopy(frame)
    short.fields = {key: values[:81].copy() for key, values in fields.items()}
    short.fields["acc_active_flag"][40] = False
    assert function(short) == []

# Minimum lengths and curvature frame count retain their original boundaries.
for value in (4.5, 5.0, np.inf, np.nan):
    changed = deepcopy(frame)
    changed.fields["front_ttc"][:] = value
    assert [(s.start, s.end) for s in curvature_segments(changed)] == [(0, 120)]
for value in (3.5, 4.0, 4.49):
    changed = deepcopy(frame)
    changed.fields["front_ttc"][:] = value
    assert not curvature_segments(changed)
for count, expected in ((49, False), (50, True)):
    changed = deepcopy(frame)
    changed.fields = {key: values[:count].copy() for key, values in fields.items()}
    assert bool(curvature_segments(changed)) == expected
for count, expected in ((50, False), (51, True)):
    changed = deepcopy(frame)
    changed.fields = {key: values[:count].copy() for key, values in fields.items()}
    assert bool(slope_no_front_segments(changed)) == expected
for qualified, expected in ((4, False), (5, True)):
    changed = deepcopy(frame)
    changed.fields["curvature"][:] = 0.004
    changed.fields["curvature"][:qualified] = 0.005
    assert bool(curvature_segments(changed)) == expected

# All six CPI metrics use the same strict scene selection through the dispatcher.
changed = deepcopy(frame)
changed.fields["acc_active_flag"][60] = False
for name in (
    "curvature_min_acc", "curvature_min_jerk", "curvature_min_speed",
    "slope_no_frnt_acc", "slope_no_frnt_jerk", "slope_no_frnt_vel_fluct",
):
    metric = importlib.import_module("filters." + name).create_metric()
    segments = find_scene_segments(changed, metric.scene)
    assert [(s.start, s.end) for s in segments] == [(0, 59), (61, 120)], name
'''],
            cwd=ROOT / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
