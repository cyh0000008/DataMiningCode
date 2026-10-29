import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class J6BStop2goTests(unittest.TestCase):
    def test_continuous_acceleration_setspeed_and_ttc_gates(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from copy import deepcopy
import numpy as np
from metric_utils import stop2go_segments, metric_required_fields
from signal_preprocessor import SignalFrame, stitch_signal_frames
from filters.stop2go_max_acc import create_metric
from filters.stop2go_max_acc_time import create_metric as create_time
from filters.stop2go_max_jerk import create_metric as create_jerk
from filters.stop2go_start_time import create_metric as create_start

t = np.round(np.arange(0, 10.1, 0.1), 6)
speed = np.minimum(t, 8)
fields = {
    "time_s": t, "ego_speed_mps": speed,
    "stationary_flag": t == 0, "moving_flag": t > 0,
    "acc_active_flag": np.ones(t.size, dtype=bool),
    "front_ttc": np.full(t.size, np.inf),
    "cutin_ttc_calc": np.full(t.size, np.inf),
    "acceleration_total": np.full(t.size, 1.5),
    "set_speed": np.full(t.size, 50.),
    "curv_enable_flag": np.zeros(t.size, dtype=bool),
    "curv_speed_limit": np.full(t.size, np.inf),
}
frame = SignalFrame("synthetic", "", {}, fields, {})
segments = stop2go_segments(frame)
assert [(seg.start, seg.end) for seg in segments] == [(1, 80)], segments
# Changed conditions are checked throughout, not only at launch.
for field, value in (
    ("acceleration_total", -0.01), ("acceleration_total", np.nan),
    ("set_speed", 49.), ("set_speed", np.nan), ("front_ttc", 4.49),
):
    changed = deepcopy(frame)
    changed.fields[field][40] = value
    assert not stop2go_segments(changed), (field, value)
    changed = deepcopy(frame)
    changed.fields[field][65] = value
    segments = stop2go_segments(changed)
    assert [(seg.start, seg.end) for seg in segments] == [(1, 64)], (field, value, segments)
# Inclusive thresholds and preserved unavailable-TTC behavior.
for field, value in (
    ("acceleration_total", 0.), ("front_ttc", 4.5),
    ("front_ttc", np.inf), ("front_ttc", np.nan),
):
    changed = deepcopy(frame)
    changed.fields[field][:] = value
    assert stop2go_segments(changed)[0].end == 80, (field, value)
for delta, accepted in ((0.5e-6, True), (2e-6, False)):
    changed = deepcopy(frame)
    changed.fields["set_speed"][40:] += delta
    assert bool(stop2go_segments(changed)) == accepted
# SetSpeed is km/h, while scene speed is m/s: 28.8 km/h equals 8 m/s.
changed = deepcopy(frame)
changed.fields["set_speed"][:] = 28.8
assert stop2go_segments(changed)[0].end == 80
changed.fields["set_speed"][:] = 10.8
assert not stop2go_segments(changed)
# Check at launch, including a SetSpeed change from the stationary frame.
for field, value in (("acceleration_total", -1), ("set_speed", 49), ("front_ttc", 3)):
    changed = deepcopy(frame)
    changed.fields[field][1] = value
    assert not stop2go_segments(changed)
# All four metrics require the new inputs and skip missing signals explicitly.
for factory in (create_metric, create_time, create_jerk, create_start):
    metric = factory()
    assert {"set_speed", "acceleration_total"} <= set(metric_required_fields(metric))
    for field in ("set_speed", "acceleration_total"):
        changed = deepcopy(frame)
        del changed.fields[field]
        result = metric.run(changed)
        assert result.status == "SKIPPED" and field in result.missing_fields, result
    changed = deepcopy(frame)
    changed.fields["acceleration_total"][65] = -100
    result = metric.run(changed)
    assert result.error is None and result.total_cases == 1, result
    assert all(event.end_s == 6.4 for event in result.events)
# Continuation across a file boundary must retain the new required signals.
first, second = deepcopy(frame), deepcopy(frame)
first.fields = {key: values[:50] for key, values in fields.items()}
second.fields = {key: values[50:] for key, values in fields.items()}
stitched = stitch_signal_frames(first, second, 5)
assert stop2go_segments(stitched)[0].end == 80
stitched.fields["set_speed"][60:] = 40
assert stop2go_segments(stitched)[0].end == 59
'''],
            cwd=ROOT / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
