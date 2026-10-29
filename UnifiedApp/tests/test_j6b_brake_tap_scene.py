import os
from pathlib import Path
import subprocess
import sys
import unittest


class BrakeTapSceneTests(unittest.TestCase):
    def test_follow_speed_range_boundaries_and_no_front_unchanged(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
import numpy as np
from signal_preprocessor import SignalFrame
from filters.acc_brake_tap_count import create_metric

metric = create_metric()
t = np.round(np.arange(0, 14.01, .1), 5)
n = len(t)
def frame():
    active = ((t >= 2) & (t <= 12))
    torque = np.where((t >= 6) & (t <= 6.4), 300., 0.)
    acceleration = np.where((t >= 6) & (t <= 6.6), -.6, 0.)
    return SignalFrame("synthetic", "", {}, {
        "time_s": t, "acceleration": acceleration,
        "ego_speed": np.full(n, 36.), "ego_speed_mps": np.full(n, 10.),
        "set_speed": np.full(n, 36.), "acc_active_flag": active,
        "pedal_override_flag": np.zeros(n), "cutin_valid": np.zeros(n),
        "front_valid": np.ones(n), "front_range": np.full(n, 20.),
        "front_velocity_abs": np.full(n, 10.),
        "control_brake_request_torque": torque, "actual_brake_torque": torque,
    }, {})
def scenes(f):
    return [s for s in metric._stable_scene_segments(f) if s.scene == "stable_follow"]

f = frame()
assert len(scenes(f)) == 1
assert metric.evaluate(f).bad_cases == 1
# Inclusive 30km/h threshold; lower speed must not survive gap merging.
f = frame()
f.fields["ego_speed"][:] = 30.
f.fields["ego_speed_mps"][:] = 30/3.6
f.fields["front_velocity_abs"][:] = 30/3.6
assert len(scenes(f)) == 1
f.fields["ego_speed"][t == 7] = 29.99
assert scenes(f) == []
assert metric.evaluate(f).bad_cases == 0
# Front-speed span at the exact 4km/h limit is accepted, above it rejected.
f = frame()
half_span = 2. / 3.6
v = np.interp(t, [0, 3, 5, 7, 9, 14],
              [10-half_span, 10-half_span, 10, 10, 10+half_span, 10+half_span])
f.fields["front_velocity_abs"] = v
assert len(scenes(f)) == 1
f.fields["front_velocity_abs"] = np.interp(
    t, [0, 3, 5, 7, 9, 14],
    [10-half_span-.01, 10-half_span-.01, 10, 10, 10+half_span+.01, 10+half_span+.01])
assert scenes(f) == []
# Both start and end windows must pass, including the out-of-scene context.
for index, at in ((20, .5), (120, .5)):
    f = frame()
    f.fields["front_velocity_abs"][index - 10] = 10. + at
    assert len(scenes(f)) == 1
    f.fields["front_velocity_abs"][index - 10] += .001
    assert scenes(f) == []
f = frame()
f.fields["front_velocity_abs"][t == 13] = 10.6
assert scenes(f) == []
for signal in ("front_velocity_abs", "front_valid"):
    f = frame()
    f.fields[signal][t == 1] = np.nan if signal == "front_velocity_abs" else 0
    assert scenes(f) == []
# Full +/-1s context required even if stable scene itself exceeds 8s.
for start, end in ((0, 12), (2, 14)):
    f = frame()
    f.fields["acc_active_flag"] = (t >= start) & (t <= end)
    assert scenes(f) == []
# No-front low-speed scene and pulse thresholds remain unchanged.
f = frame()
f.fields["front_valid"][:] = 0
f.fields["ego_speed"][:] = 10.
f.fields["set_speed"][:] = 10.
assert [s.scene for s in metric._stable_scene_segments(f)] == ["no_front_steady"]
assert metric.evaluate(f).bad_cases == 1
assert metric.brake_prominence_min_nm == 180
assert metric.min_pulse_duration_s == .08 and metric.max_pulse_duration_s == 1.5
'''],
            cwd=Path(__file__).resolve().parents[2] / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
