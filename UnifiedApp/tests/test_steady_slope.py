import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SteadySlopeTests(unittest.TestCase):
    def test_both_platforms_all_three_metrics(self):
        for profile in ("J6B", "MMT"):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    [sys.executable, "-c", r'''
from copy import deepcopy
from pathlib import Path
import importlib
import sys
import numpy as np

profile = sys.argv[1]
t = np.round(np.arange(0, 20.01, 0.1), 6)
names = ("slope_no_frnt_acc", "slope_no_frnt_jerk", "slope_no_frnt_vel_fluct")
if profile == "J6B":
    from signal_preprocessor import SignalFrame, stitch_signal_frames
    from metric_utils import slope_no_front_segments, find_scene_segments
    fields = {
        "time_s": t, "ego_speed_mps": np.full(t.size, 10.),
        "set_speed": np.full(t.size, 36.), "acc_active_flag": np.ones(t.size, bool),
        "moving_flag": np.ones(t.size, bool), "slope_acc": np.full(t.size, .2),
        "front_valid": np.zeros(t.size, bool), "curv_speed_limit": np.full(t.size, 30.),
        "acceleration_total": np.zeros(t.size),
    }
    original = SignalFrame("synthetic", "", {}, fields, {})
    def select(data):
        data.runtime_cache.clear()
        outputs = []
        for name in names:
            metric = importlib.import_module("filters." + name).create_metric()
            outputs.append([(s.start, s.end) for s in find_scene_segments(data, metric.scene)])
        assert outputs[0] == outputs[1] == outputs[2]
        return outputs[0]
    def change(data, field, indices, value):
        data.fields[field][indices] = value
    speed_field, set_field, valid_field, invalid = "ego_speed_mps", "set_speed", "slope_acc", 0.
    set_value = 36.
    def truncate(data, count):
        data.fields = {key: values[:count].copy() for key, values in data.fields.items()}
        return data
else:
    sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
    modules = [importlib.import_module(name) for name in names]
    original = [{
        "_powertrain_type": "PHEV",
        "unp_timestamp_ms": float(ts * 1000),
        "unp_frame_data": {
            "NP_State": {"f_Np_on": True}, "Acc_info": {"SetSpeed": 10.},
            "curvature_info": {"is_curv_v_limit_enable": True, "curv_velocity_limit": 30.},
        },
        "matched_chassis": {"stationary": False, "vehicle_speed_average": 10.,
                            "accleration_on_wheel": 0., "timestamp_ms": float(ts * 1000)},
        "matched_msd_control": {"slope_acc": .2},
        "matched_fusion": {"f_potential_cipv_by_pos": False},
        "matched_body": {"velocity_on_dashboard": 36.},
    } for ts in t]
    def select(data):
        outputs = [
            [(next(i for i, frame in enumerate(data) if frame is segment[0]),
              next(i for i, frame in enumerate(data) if frame is segment[-1]))
             for segment in module._find_risk_segments(data)]
            for module in modules
        ]
        assert outputs[0] == outputs[1] == outputs[2]
        return outputs[0]
    def change(data, field, indices, value):
        indices = range(*indices.indices(len(data))) if isinstance(indices, slice) else [indices]
        for i in indices:
            if field == "speed":
                data[i]["matched_chassis"]["vehicle_speed_average"] = value
            elif field == "set":
                data[i]["unp_frame_data"]["Acc_info"]["SetSpeed"] = value
            else:
                data[i]["matched_msd_control"]["slope_acc"] = value
    speed_field, set_field, valid_field, invalid = "speed", "set", "slope", 0.
    set_value = 10.
    def truncate(data, count):
        return data[:count]

assert select(original) == [(10, 200)]
# Entry uses a full second and starts at confirmation, not the dwell start.
for count, expected in ((60, []), (61, [(10, 60)])):
    assert select(truncate(deepcopy(original), count)) == expected
data = deepcopy(original)
change(data, speed_field, slice(0, 30), 8.)
assert select(data) == [(40, 200)]
for value in (10. - 2 / 3.6, 10. + 2 / 3.6):
    data = deepcopy(original)
    change(data, speed_field, slice(None), value)
    assert select(data) == [(10, 200)]
data = deepcopy(original)
change(data, speed_field, slice(None), 10. + 2.01 / 3.6)
assert select(data) == []
# A pre-entry excursion restarts the full dwell period.
data = deepcopy(original)
change(data, speed_field, 5, 8.)
assert select(data) == [(16, 200)]
# A post-entry excursion is retained, including sustained off-target speed.
data = deepcopy(original)
change(data, speed_field, slice(70, None), 8.)
assert select(data) == [(10, 200)]
# One invalid slope frame always splits; no twenty-frame recovery.
data = deepcopy(original)
change(data, valid_field, 100, invalid)
assert select(data) == [(10, 99), (111, 200)]
data = deepcopy(original)
change(data, set_field, slice(100, None), set_value + .1)
assert select(data) == [(10, 99), (110, 200)]
for value in (float("nan"), 0.):
    data = deepcopy(original)
    change(data, set_field, slice(None), value)
    assert select(data) == []
# No first qualifying dwell may start in next-file-only context.
if profile == "MMT":
    for path, value in (
        (("unp_frame_data", "NP_State", "f_Np_on"), False),
        (("matched_fusion", "f_potential_cipv_by_pos"), True),
        (("matched_chassis", "stationary"), True),
        (("unp_frame_data", "curvature_info", "curv_velocity_limit"), 9.),
    ):
        data = deepcopy(original)
        target = data[100]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        assert select(data) == [(10, 99), (111, 200)], path
    data = deepcopy(original)
    data[100]["unp_frame_data"]["TrafficLight"] = {"f_RedLightOn": True, "stop_distance": 39.}
    assert select(data) == [(10, 99), (111, 200)]
    data = deepcopy(original)
    data[100]["unp_timestamp_ms"] = data[99]["unp_timestamp_ms"]
    assert select(data) == [(10, 99), (111, 200)]
    data = deepcopy(original)
    for frame in data:
        frame["_sequence_meta"] = {"source_index": 1}
    assert select(data) == []
    data = deepcopy(original)
    for frame in data[5:]:
        frame["_sequence_meta"] = {"source_index": 1}
    assert select(data) == [(10, 200)]
    # Entire confirmed segment is measured; do not crop out later fluctuations.
    data = deepcopy(original)
    for frame in data[70:]:
        frame["matched_body"]["velocity_on_dashboard"] = 33.
    segment = modules[2]._find_risk_segments(data)[0]
    measured = modules[2]._calc_segment_max_vel_fluct(segment)
    assert measured["vel_chassis_diff"] == 3., measured
    data = deepcopy(original)
    for i, frame in enumerate(data):
        frame["matched_body"]["velocity_on_dashboard"] = 33. if i >= 70 else 36.
        frame["matched_chassis"]["accleration_on_wheel"] = (
            100. if i < 10 else min(max((i - 70) * .2, 0.), 4.)
        )
    segment = modules[0]._find_risk_segments(data)[0]
    acceleration = modules[0]._calc_segment_max_acc_val(segment)
    assert abs(acceleration["max_acc_chassis_val"] - 4.2) < 1e-9, acceleration
    assert acceleration["max_acc_chassis_val_time"] >= 9000
    jerk = modules[1]._calc_segment_max_jerk_val(segment)
    assert 1.9 < jerk["max_jerk"] < 2.1, jerk
    assert jerk["max_jerk_time"] > 7000
    for module in modules:
        _, _, stats = module._core_process(data)
        assert stats["All Case Count"] == 1 and stats["Bad Case"] == 1, stats
else:
    def part(start, end):
        return SignalFrame("synthetic", "", {}, {
            key: values[start:end].copy() for key, values in original.fields.items()
        }, {})
    primary, follower = part(0, 7), part(7, 201)
    follower.fields["time_s"] -= .7
    stitched = stitch_signal_frames(primary, follower, .7)
    assert select(stitched) == [(10, 200)]
    previous = deepcopy(original)
    previous.has_continuous_previous = True
    assert select(previous) == []
    context_only = deepcopy(original)
    context_only.primary_length = 1
    change(context_only, valid_field, slice(0, 20), 0.)
    assert select(context_only) == []
    data = deepcopy(original)
    change(data, speed_field, slice(70, None), 8.)
    change(data, "acceleration_total", slice(0, 10), 100.)
    change(data, "acceleration_total", slice(70, None), 4.)
    for name in names:
        metric = importlib.import_module("filters." + name).create_metric()
        data.runtime_cache.clear()
        measured = metric.evaluate(data)
        assert measured.total_cases == 1 and measured.bad_cases == 1, (name, measured)
''', profile],
                    cwd=ROOT / f"{profile}_UnifiedMining",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
