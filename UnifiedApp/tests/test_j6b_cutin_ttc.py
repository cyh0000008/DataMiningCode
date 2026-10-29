import os
from pathlib import Path
import subprocess
import sys
import unittest


class CutinTtcTests(unittest.TestCase):
    def test_scenes_same_target_measurements_and_plots(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from copy import deepcopy
from importlib import import_module
import numpy as np
from signal_preprocessor import SignalFrame, raw_dependencies_for_field
from metric_utils import (
    effective_ttc, curvature_segments, stop2go_segments, follow_stop_segments,
    metric_required_fields, measure_segment, Segment,
)
from bad_case_image_report import CaseRow, build_evidence_panel, build_panels

t = np.round(np.arange(0, 16.01, .1), 6)
n = len(t)
fields = {
    "time_s": t, "acc_active_flag": np.ones(n), "curv_enable_flag": np.ones(n),
    "curvature": np.full(n, .005), "ego_speed_mps": np.full(n, 5.),
    "moving_flag": np.ones(n), "stationary_flag": np.zeros(n),
    "front_ttc": np.full(n, 10.), "cutin_ttc_calc": np.full(n, np.inf),
    "front_valid": np.ones(n), "cutin_valid": np.zeros(n),
    "front_target_id": np.ones(n), "front_velocity_abs": np.zeros(n),
    "front_range": np.full(n, 3.5), "acceleration_total": np.full(n, 1.),
    "acceleration": np.full(n, 1.), "set_speed": np.full(n, 50.),
    "curv_speed_limit": np.full(n, np.inf),
}
frame = SignalFrame("synthetic", "", {}, fields, {})
for value in (4.5, np.inf, np.nan):
    changed = deepcopy(frame)
    changed.fields["cutin_ttc_calc"][:] = value
    assert len(curvature_segments(changed)) == 1
changed = deepcopy(frame)
changed.fields["cutin_ttc_calc"][80] = 4.49
assert [(s.start, s.end) for s in curvature_segments(changed)] == [(0, 79), (81, 160)]
changed.fields["front_ttc"][40] = 4.49
assert all(not (s.start <= 40 <= s.end or s.start <= 80 <= s.end)
           for s in curvature_segments(changed))

launch = deepcopy(frame)
launch.fields["curv_enable_flag"][:] = 0
launch.fields["ego_speed_mps"] = np.minimum(t, 8.)
launch.fields["stationary_flag"] = t == 0
launch.fields["moving_flag"] = t > 0
assert stop2go_segments(launch)[0].end == 80
for index, expected in ((1, []), (40, []), (65, [(1, 64)])):
    changed = deepcopy(launch)
    changed.fields["cutin_ttc_calc"][index] = 4.49
    assert [(s.start, s.end) for s in stop2go_segments(changed)] == expected

follow = deepcopy(frame)
stopped = t >= 12.
follow.fields["stationary_flag"] = stopped
follow.fields["moving_flag"] = ~stopped
follow.fields["ego_speed_mps"] = np.where(stopped, 0., 5.)
follow.fields["acceleration_total"] = np.where(t >= 2., -.5, 0.)
follow.fields["acceleration"] = follow.fields["acceleration_total"].copy()
# Same ID toggles between ordinary target and cut-in, including after stop.
cutin = ((t >= 1.) & (t < 4.)) | ((t >= 8.) & (t < 10.)) | (t >= 12.5)
follow.fields["cutin_valid"] = cutin
follow.fields["front_valid"] = ~cutin
follow.fields["front_ttc"] = np.where(cutin, np.inf, 8.)
follow.fields["cutin_ttc_calc"] = np.where(cutin, 3., np.inf)
follow.fields["cutin_ttc_calc"][t == 9.] = 1.
assert [(s.start, s.end) for s in follow_stop_segments(follow)] == [(0, 120)]
value, when, _ = measure_segment(follow, Segment(0, 120), "ttc_at_ego_decel")
assert value == 3. and when == 2.
assert measure_segment(follow, Segment(0, 120), "min_ttc")[:2] == (1., 9.)
assert measure_segment(follow, Segment(0, 120), "stop_front_distance")[0] == 3.5
# Changing ID discards old unfinished following, new ID starts independently.
changed = deepcopy(follow)
changed.fields["front_target_id"][t >= 6.] = 2.
assert [(s.start, s.end) for s in follow_stop_segments(changed)] == [(60, 120)]
changed.fields["front_target_id"][t >= 10.] = 3.
assert follow_stop_segments(changed) == []
changed = deepcopy(follow)
changed.fields["front_target_id"][t >= 12.] = 2.
assert follow_stop_segments(changed) == []  # Must not finish using a new target's stop.
changed = deepcopy(follow)
changed.fields["front_target_id"][t >= 13.] = 2.
assert measure_segment(changed, Segment(0, 120), "stop_front_distance")[0] is None
changed = deepcopy(follow)
changed.fields["front_target_id"][t == 6.] = 0.
assert [(s.start, s.end) for s in follow_stop_segments(changed)] == [(61, 120)]

for name in ("follow_stop_ttc", "follow_stop_min_ttc_frntobj_dec",
             "follow_stop_max_acc", "follow_stop_min_jerk", "follow_stop_min_frntobj_dis"):
    metric = import_module("filters." + name).create_metric()
    required = metric_required_fields(metric)
    assert {"cutin_ttc_calc", "cutin_valid", "front_target_id"} <= set(required)
    assert metric.evaluate(follow).total_cases == 1, name
    assert metric.evaluate(changed).total_cases == 1, name
for name in ("curvature_min_speed", "curvature_min_acc", "curvature_min_jerk",
             "stop2go_max_acc", "stop2go_max_jerk", "stop2go_start_time", "stop2go_max_acc_time"):
    assert "cutin_ttc_calc" in metric_required_fields(import_module("filters." + name).create_metric())
assert "flt1_cutin_flag" in raw_dependencies_for_field("cutin_ttc_calc")
metric = import_module("filters.follow_stop_ttc").create_metric()
event = metric.evaluate(follow).events[0]
case = CaseRow(2, metric.name, "synthetic", 0., 12., 1., "", event.values)
panel = build_evidence_panel(follow, case, np.ones(n, dtype=bool))
assert panel.lines[0].values[20] == 3.
assert np.flatnonzero(panel.highlight).tolist() == [20]
panels = build_panels(follow, metric.name, np.ones(n, dtype=bool))
assert any(p.lines[0].label == "Cut-in TTC" and p.unit == "s" for p in panels)
assert np.all(effective_ttc(follow) < np.inf)
'''],
            cwd=Path(__file__).resolve().parents[2] / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
