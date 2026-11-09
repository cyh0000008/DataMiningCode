import os
from pathlib import Path
import subprocess
import sys
import unittest


class CutinTtcTests(unittest.TestCase):
    def test_front_validity_and_ttc_do_not_depend_on_lateral_range(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from pathlib import Path
import tempfile
import h5py
import numpy as np
from signal_preprocessor import build_signal_frame, raw_dependencies_for_field
from metric_utils import distant_front_clear_mask

for field in ("front_valid", "front_ttc"):
    assert "front_lat_range" not in raw_dependencies_for_field(field)
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "targets.h5"
    values = {
        "acceleration": np.zeros(7),
        "ego_speed": np.full(7, 36.),
        "front_velocity_abs": np.full(7, 5.),
        "front_target_id": np.array([1., 1., 1., 0., 1., 1., 1.]),
        "front_range": np.array([40., 40., 40., 40., 0., np.nan, 40.]),
        "flt1_cutin_flag": np.array([0., 0., 0., 0., 0., 0., 1.]),
    }
    with h5py.File(path, "w") as h5:
        h5.create_group("metadata")
        sample = h5.create_group("sample")
        sample.create_dataset("timestamp", data=np.arange(7, dtype=float))
        signals = sample.create_group("signals")
        for key, value in values.items():
            signals.create_dataset(key, data=value)
    config = {"signals": {key: "sample/signals/" + key for key in values}}
    # Missing, large, non-finite and boundary lateral ranges all give identical targets.
    for lateral in (None, [-3., 3., np.nan, 0., 0., 0., 10.],
                    [-2., 2., np.inf, 0., 0., 0., -10.]):
        if lateral is not None:
            with h5py.File(path, "r+") as h5:
                if "sample/signals/front_lat_range" in h5:
                    del h5["sample/signals/front_lat_range"]
                h5.create_dataset("sample/signals/front_lat_range", data=lateral)
        config["signals"]["front_lat_range"] = "sample/signals/front_lat_range"
        frame = build_signal_frame(path, config)
        np.testing.assert_array_equal(frame.col("front_valid"),
                                      [True, True, True, False, False, False, False])
        np.testing.assert_array_equal(frame.col("cutin_valid"),
                                      [False, False, False, False, False, False, True])
        np.testing.assert_allclose(frame.col("front_ttc")[:3], [8., 8., 8.])
        assert frame.col("cutin_ttc_calc")[-1] == 8.
        assert not distant_front_clear_mask(frame)[:3].any()
        assert not frame.missing_signal_details(("front_valid", "front_ttc"))
'''],
            cwd=Path(__file__).resolve().parents[2] / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
clear_frame = deepcopy(frame)
clear_frame.fields["front_range"][:] = 40.
clear_frame.fields["front_ttc"][:] = 30.
for target in ("front", "cutin"):
    for distance, ttc, expected in (
        (30., 21., False), (30.01, 20., False), (30.01, 20.01, True),
        (40., 19., False), (29., 30., False), (40., np.nan, False),
        (40., np.inf, True), (np.nan, 30., False),
    ):
        changed = deepcopy(clear_frame)
        changed.fields["front_valid"][:] = target == "front"
        changed.fields["cutin_valid"][:] = target == "cutin"
        changed.fields["front_range"][:] = distance
        changed.fields["front_ttc" if target == "front" else "cutin_ttc_calc"][:] = ttc
        assert bool(curvature_segments(changed)) == expected, (target, distance, ttc)
        changed.fields["curv_enable_flag"][:] = 0
        changed.fields["ego_speed_mps"] = np.minimum(t, 8.)
        changed.fields["stationary_flag"] = t == 0
        changed.fields["moving_flag"] = t > 0
        assert bool(stop2go_segments(changed)) == expected, (target, distance, ttc)
changed = deepcopy(clear_frame)
changed.fields["front_valid"][:] = 0
changed.fields["front_range"][:] = 0
changed.fields["front_ttc"][:] = np.nan
assert len(curvature_segments(changed)) == 1  # No target remains eligible.
changed = deepcopy(clear_frame)
changed.fields["cutin_valid"][80] = 1
changed.fields["cutin_ttc_calc"][80] = 20.
assert [(s.start, s.end) for s in curvature_segments(changed)] == [(0, 79), (81, 160)]
changed.fields["front_ttc"][40] = 4.49
assert all(not (s.start <= 40 <= s.end or s.start <= 80 <= s.end)
           for s in curvature_segments(changed))

launch = deepcopy(clear_frame)
launch.fields["curv_enable_flag"][:] = 0
launch.fields["ego_speed_mps"] = np.minimum(t, 8.)
launch.fields["stationary_flag"] = t == 0
launch.fields["moving_flag"] = t > 0
assert stop2go_segments(launch)[0].end == 80
for index, expected in ((1, []), (40, []), (65, [(1, 64)])):
    changed = deepcopy(launch)
    changed.fields["cutin_valid"][index] = 1
    changed.fields["cutin_ttc_calc"][index] = 20.
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
    assert {"cutin_ttc_calc", "front_valid", "cutin_valid", "front_range"} <= set(
        metric_required_fields(import_module("filters." + name).create_metric())
    )
assert "flt1_cutin_flag" in raw_dependencies_for_field("cutin_ttc_calc")
metric = import_module("filters.follow_stop_ttc").create_metric()
event = metric.evaluate(follow).events[0]
case = CaseRow(2, metric.name, "synthetic", 0., 12., 1., "", event.values)
panel = build_evidence_panel(follow, case, np.ones(n, dtype=bool))
assert panel.lines[0].values[20] == 3.
assert np.flatnonzero(panel.highlight).tolist() == [20]
panels = build_panels(follow, metric.name, np.ones(n, dtype=bool))
assert any(
    line.label == "Cut-in TTC" and p.unit == "s"
    for p in panels for line in p.lines
)
assert np.all(effective_ttc(follow) < np.inf)
'''],
            cwd=Path(__file__).resolve().parents[2] / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
