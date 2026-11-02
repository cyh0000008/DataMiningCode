import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


def run_script(cwd: Path, code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, encoding="utf-8", errors="replace", timeout=60,
    )


class OverrideSceneTruncationTests(unittest.TestCase):
    def test_j6b_scenes_and_mpis_truncate_on_override(self):
        result = run_script(ROOT / "J6B_UnifiedMining", r'''
from copy import deepcopy
from importlib import import_module
import numpy as np
from signal_preprocessor import SignalFrame
from metric_utils import (
    curvature_segments, stop2go_segments, follow_stop_segments, follow_start_segments,
    slope_no_front_segments, metric_required_fields, measure_segment, Segment,
    merge_short_gaps,
)

t = np.round(np.arange(0, 16.01, .1), 6)
n = len(t)
fields = {
    "time_s": t, "acc_active_flag": np.ones(n), "pedal_override_flag": np.zeros(n),
    "curv_enable_flag": np.ones(n), "curvature": np.full(n, .005), "ego_speed_mps": np.full(n, 5.),
    "moving_flag": np.ones(n), "stationary_flag": np.zeros(n),
    "front_ttc": np.full(n, 10.), "cutin_ttc_calc": np.full(n, np.inf),
    "front_valid": np.ones(n), "cutin_valid": np.zeros(n),
    "front_target_id": np.ones(n), "front_velocity_abs": np.zeros(n),
    "front_range": np.full(n, 3.5), "acceleration_total": np.full(n, 1.),
    "acceleration": np.full(n, 1.), "set_speed": np.full(n, 50.),
    "curv_speed_limit": np.full(n, np.inf), "slope_acc": np.full(n, .5),
}
frame = SignalFrame("synthetic", "", {}, fields, {})

changed = deepcopy(frame)
changed.fields["pedal_override_flag"][80] = 1
assert [(s.start, s.end) for s in curvature_segments(changed)] == [(0, 79), (81, 160)]

launch = deepcopy(frame)
launch.fields["curv_enable_flag"][:] = 0
launch.fields["ego_speed_mps"] = np.minimum(t, 8.)
launch.fields["stationary_flag"] = t == 0
launch.fields["moving_flag"] = t > 0
assert stop2go_segments(launch)[0].end == 80
for index, expected in ((1, []), (65, [(1, 64)])):
    changed = deepcopy(launch)
    changed.fields["pedal_override_flag"][index] = 1
    assert [(s.start, s.end) for s in stop2go_segments(changed)] == expected, index

slope = deepcopy(frame)
slope.fields["front_valid"][:] = 0
slope.fields["set_speed"][:] = 18.
assert [(s.start, s.end) for s in slope_no_front_segments(slope)] == [(10, 160)]
slope.fields["pedal_override_flag"][70] = 1
assert [(s.start, s.end) for s in slope_no_front_segments(slope)] == [(10, 69), (81, 160)]

follow = deepcopy(frame)
stopped = t >= 12.
follow.fields["stationary_flag"] = stopped
follow.fields["moving_flag"] = ~stopped
follow.fields["ego_speed_mps"] = np.where(stopped, 0., 5.)
assert [(s.start, s.end) for s in follow_stop_segments(follow)] == [(0, 120)]
changed = deepcopy(follow)
changed.fields["pedal_override_flag"][30] = 1
assert [(s.start, s.end) for s in follow_stop_segments(changed)] == [(31, 120)]
changed.fields["pedal_override_flag"][100] = 1
assert follow_stop_segments(changed) == []
changed = deepcopy(follow)
changed.fields["pedal_override_flag"][t == 13.] = 1
value, _, extra = measure_segment(changed, Segment(0, 120), "stop_front_distance")
assert value is None and "override" in str(extra), extra

start = deepcopy(frame)
start.fields["front_velocity_abs"] = np.where(t >= 1., 1., 0.)
start.fields["ego_speed_mps"] = np.where(t >= 3., 1., 0.)
start.fields["stationary_flag"] = t < 3.
assert [(s.start, s.end) for s in follow_start_segments(start)] == [(10, 30)]
start.fields["pedal_override_flag"][20] = 1
assert follow_start_segments(start, minimum_observation_s=3.0) == []
jerk_metric = import_module("filters.follow_start_jerk").create_metric()
assert jerk_metric.evaluate(start).total_cases == 0
start.fields["ego_speed_mps"][15:] = 1.
assert [(s.start, s.end) for s in follow_start_segments(start, minimum_observation_s=3.0)] == [(10, 15)]
assert measure_segment(start, Segment(10, 15), "start_latency")[0] == .5

# If override arrives before the powertrain-specific latency limit, discard the
# unmeasurable scene. At the limit, retain it so missing response is a bad case.
no_ego_start = deepcopy(start)
no_ego_start.fields["ego_speed_mps"][:] = 0.
no_ego_start.fields["stationary_flag"][:] = True
no_ego_start.fields["pedal_override_flag"][:] = 0.
for powertrain, limit in (("PHEV", 3.0), ("BEV", 3.0), ("ICE", 3.5)):
    metric = import_module("filters.follow_start_latency").create_metric()
    metric.powertrain_type = powertrain
    before_limit = deepcopy(no_ego_start)
    before_limit.fields["pedal_override_flag"][39] = 1  # 2.9 s after front starts.
    result = metric.evaluate(before_limit)
    assert result.total_cases == 0, (powertrain, result)
    at_limit = deepcopy(no_ego_start)
    at_limit.fields["pedal_override_flag"][int((1.0 + limit) * 10)] = 1
    result = metric.evaluate(at_limit)
    assert result.total_cases == result.bad_cases == 1, (powertrain, result)

for name in ("curvature_min_speed", "stop2go_max_acc", "slope_no_frnt_acc", "follow_stop_ttc",
             "follow_start_latency", "follow_stop_min_frntobj_dis", "acc_rollback_count",
             "acc_lost_arbitration"):
    metric = import_module("filters." + name).create_metric()
    assert "pedal_override_flag" in metric_required_fields(metric), name

t = np.round(np.arange(0, 2.01, .1), 6)
n = len(t)
rollback = SignalFrame("synthetic", "", {}, {
    "time_s": t, "acc_active_flag": np.ones(n), "pedal_override_flag": np.zeros(n),
    "brake_request_type": np.full(n, 5.), "gear": np.full(n, 12.), "ego_speed_non_driven": np.zeros(n),
    **{"wheel_dir_" + w: np.full(n, 4.) for w in ("lf", "rf", "lr", "rr")},
    **{"wheel_speed_" + w: np.ones(n) for w in ("lf", "rf", "lr", "rr")},
}, {})
metric = import_module("filters.acc_rollback_count").create_metric()
assert len(metric.evaluate(rollback).events) == 1
rollback.fields["pedal_override_flag"][10] = 1
assert len(metric.evaluate(rollback).events) == 2
rollback.fields["pedal_override_flag"][:] = 1
assert len(metric.evaluate(rollback).events) == 0

# Short-gap merging must not stitch heavy-braking events across override frames.
barrier_times = np.arange(0, 0.7, 0.1)
barrier_mask = np.ones(len(barrier_times), dtype=bool)
barrier_mask[2:4] = False
braking_mask = np.zeros(len(barrier_times), dtype=bool)
braking_mask[[0, 1, 4, 5]] = True
assert merge_short_gaps(
    barrier_times, braking_mask, 0.5, barrier_mask=barrier_mask
).tolist() == braking_mask.tolist()

# Rapid-toggle events require uninterrupted ACC control through both the
# 2-1-2 sequence and its one-second deceleration observation window.
toggle_times = np.round(np.arange(0, 3.01, 0.1), 6)
toggle_fields = {
    "time_s": toggle_times,
    "acc_active_flag": np.ones(len(toggle_times)),
    "pedal_override_flag": np.zeros(len(toggle_times)),
    "brake_request_type": np.full(len(toggle_times), 2.),
    "acceleration": np.zeros(len(toggle_times)),
}
toggle_fields["brake_request_type"][10] = 1.
toggle_fields["acceleration"][10] = -3.
toggle = SignalFrame("synthetic", "", {}, toggle_fields, {})
metric = import_module("filters.acc_brake_rapid_toggle_decel_jump_count").create_metric()
assert len(metric.evaluate(toggle).events) == 1
toggle.fields["pedal_override_flag"][9] = 1
assert len(metric.evaluate(toggle).events) == 0
toggle.fields["pedal_override_flag"][:] = 0
toggle.fields["pedal_override_flag"][15] = 1
assert len(metric.evaluate(toggle).events) == 0

t = np.round(np.arange(0, 12.01, .1), 6)
n = len(t)
lost = SignalFrame("synthetic", "", {}, {
    "time_s": t, "acc_active_flag": np.ones(n), "pedal_override_flag": np.zeros(n),
    "acc_request_state": np.full(n, 2.),
}, {})
metric = import_module("filters.acc_lost_arbitration").create_metric()
assert len(metric.evaluate(lost).events) == 1
lost.fields["pedal_override_flag"][30] = 1
events = metric.evaluate(lost).events
assert len(events) == 1 and abs(events[0].start_s - 3.1) < 1e-9, events
lost.fields["pedal_override_flag"][:] = 1
assert len(metric.evaluate(lost).events) == 0
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mmt_scenes_and_mpis_truncate_on_override(self):
        result = run_script(ROOT / "MMT_UnifiedMining", r'''
import importlib
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
import longitudinal_preprocessor as lonprepro
from signal_lib import FolderSession

on = {"unp_frame_data": {"NP_State": {"f_Np_on": True}}}
assert lonprepro.is_acc_controlling({**on, "matched_chassis": {"throttle_override": False}})
assert not lonprepro.is_acc_controlling({**on, "matched_chassis": {"throttle_override": True}})
assert not lonprepro.is_acc_controlling({**on, "matched_chassis": {"throttle_pedal_percent": 3.}})
assert lonprepro.is_acc_controlling({**on, "matched_chassis": {"throttle_pedal_percent": 0.}})
assert not lonprepro.is_acc_controlling({"unp_frame_data": {"NP_State": {"f_Np_on": False}}})

def frames(curve):
    return [{
        "unp_timestamp_ms": i * 100,
        "unp_frame_data": {
            "NP_State": {"f_Np_on": True},
            "Acc_info": {"SetSpeed": 20.},
            "curvature_info": {"is_curv_v_limit_enable": curve, "max_curvature": 0.005},
        },
        "matched_fusion": {"min_ttc": 10.},
        "matched_chassis": {
            "stationary": i == 0 and not curve, "throttle_override": False,
            "vehicle_speed_average": 10., "accleration_on_wheel": 1.,
        },
        "matched_msd_control": {"slope_acc": 0.},
    } for i in range(121)]

for curve, names in (
    (True, ("curvature_min_acc", "curvature_min_jerk", "curvature_min_speed")),
    (False, ("stop2go_max_acc", "stop2go_max_acc_time", "stop2go_max_jerk", "stop2go_start_time")),
):
    for name in names:
        module = importlib.import_module(name)
        start = 0 if curve else 1
        data = frames(curve)
        assert module._find_risk_segments(data) == [data[start:]], name
        data[60]["matched_chassis"]["throttle_override"] = True
        expected = [data[start:60], data[61:]] if curve else []
        assert module._find_risk_segments(data) == expected, name

t = np.round(np.arange(0, 12.01, .1), 6)
class SyntheticSession(FolderSession):
    def __init__(self, override):
        self.override = override
    def common_time_range(self, signals):
        assert any("IAccPdlOvrrdAtv" in s for s in signals)
        return float(t[0]), float(t[-1])
    def get_signal_slice(self, signal, start, end):
        return t.tolist(), [1] * len(t)
    def sample_signal_to_axis(self, signal, axis, **kwargs):
        axis = np.asarray(axis)
        if "IAccPdlOvrrdAtv" in signal:
            return self.override(axis).astype(int).tolist()
        values = {"RqStat": 2, "WhlRotDir": 4, "IVehSpdAvgNDrvn": 0, "AutBrkTp": 5, "Gear": 12}
        for key, value in values.items():
            if key in signal:
                return [value] * len(axis)
        return [1] * len(axis)

lost = importlib.import_module("metrics.can.code.acc_lost_arbitration").analyze_acc_lost_arbitration
assert len(lost(SyntheticSession(lambda a: a < 0))["scenes"]) == 1
scenes = lost(SyntheticSession(lambda a: np.abs(a - 3.) < .05))["scenes"]
assert len(scenes) == 1 and abs(scenes[0]["start_timestamp"] - 3.1) < 1e-9, scenes
assert lost(SyntheticSession(lambda a: a >= 0))["scenes"] == []

rollback = importlib.import_module("metrics.can.code.acc_rollback_count").analyze_acc_rollback_count
assert len(rollback(SyntheticSession(lambda a: a < 0))["scenes"]) == 1
assert len(rollback(SyntheticSession(lambda a: np.abs(a - 6.) < .05))["scenes"]) == 2
assert rollback(SyntheticSession(lambda a: a >= 0))["scenes"] == []

# Green-light stop-and-go scenes must not archive an unfinished recording when
# override interrupts it; a scene completed before override remains eligible.
from unittest.mock import patch
traffic_modules = [
    importlib.import_module("metrics.topic.code." + name)
    for name in (
        "traffic_light_stop2go_accel_time",
        "traffic_light_stop2go_max_accel",
        "traffic_light_stop2go_max_accel_time",
        "traffic_light_stop2go_max_jerk",
        "traffic_light_stop2go_time",
    )
]
def traffic_frames(override_at, reach_completion=False):
    data = []
    for i in range(15):
        red = i == 0
        speed = float(max(0, i - 1)) if reach_completion else float(max(0, i - 1)) * .3
        data.append({
            "unp_timestamp_ms": i * 100.,
            "unp_frame_data": {
                "NP_State": {"f_Np_on": True}, "Acc_info": {"SetSpeed": 20.},
                "TrafficLight": {"f_RedLightOn": red, "stop_distance": 4. if i < 2 else 100.},
                "meta": {"sensor_timestamp_us": int(i * 100000)},
            },
            "matched_fusion": {"f_potential_cipv_in_trafficlight": False},
            "matched_chassis": {
                "stationary": i < 2, "vehicle_speed_average": speed,
                "throttle_override": i == override_at,
            },
        })
    return data

for module in traffic_modules:
    with patch.object(
        module.lonprepro, "_get_unp_fusion_meta_time",
        side_effect=lambda frame: frame["meta"]["sensor_timestamp_us"] / 1000.,
    ):
        assert module._find_risk_segments(traffic_frames(13)) == [], module.__name__
        completed = module._find_risk_segments(traffic_frames(13, reach_completion=True))
        assert len(completed) == 1 and all(
            not lonprepro.is_driver_override(frame["matched_chassis"])
            for frame in completed[0]
        ), (module.__name__, completed)

# Stationary/final-speed evidence on an override frame cannot complete a
# follow-stop or stop-and-go candidate.
follow_stop_modules = [
    importlib.import_module("metrics.topic.code." + name)
    for name in (
        "follow_stop_max_acc", "follow_stop_min_frntobj_dis", "follow_stop_min_jerk",
        "follow_stop_min_ttc_frntobj_dec", "follow_stop_ttc",
    )
]
def follow_stop_frames(override_at):
    data = []
    for i in range(7):
        data.append({
            "unp_timestamp_ms": i * 100.,
            "unp_frame_data": {
                "NP_State": {"f_Np_on": True},
                "TrafficLight": {"f_RedLightOn": False, "stop_distance": 999.},
                "meta": {"sensor_timestamp_us": i * 100000},
            },
            "matched_fusion": {"min_ttc": 10., "f_potential_cipv_by_pos": True},
            "matched_chassis": {
                "stationary": i == 6, "vehicle_speed_average": 0. if i == 6 else 5.,
                "throttle_override": i == override_at,
            },
        })
    return data

for module in follow_stop_modules:
    with (
        patch.object(module.lonprepro, "_get_unp_fusion_meta_time", return_value=1.),
        patch.object(module, "_is_non_straight_driving_scene", return_value=False),
        patch.object(module, "_is_set_speed_stable", return_value=True),
        patch.object(module, "_is_primary_sequence_frame", return_value=True),
    ):
        assert module._find_risk_segments(follow_stop_frames(6)) == [], module.__name__
        completed = module._find_risk_segments(follow_stop_frames(-1))
        assert len(completed) == 1 and len(completed[0]) >= 6, (module.__name__, completed)
        assert all(not lonprepro.is_driver_override(frame["matched_chassis"]) for frame in completed[0])

stop2go_modules = [
    importlib.import_module("metrics.topic.code." + name)
    for name in ("stop2go_max_acc", "stop2go_max_acc_time", "stop2go_max_jerk", "stop2go_start_time")
]
def stop2go_frames(override_terminal):
    data = [{"index": 0}]
    for i in range(1, 53):
        terminal = i == 52
        data.append({"index": i, "params": {
            "f_redlight": False, "f_stop_dis": 999., "f_np_on": not (terminal and override_terminal),
            "is_non_straight_driving": False, "f_stationary": terminal,
            "velocity": 8. if terminal else 5., "accel": 0., "fusion_ttc": None,
            "f_potential_cipv_in_trafficlight": False, "f_potential_cipv_by_pos": False,
            "closest_obj_x": None, "closest_obj_vel": None, "closest_obj_acc": None,
            "set_speed": 20., "slope_acc": 0., "f_curvature_control_on": False,
            "curvature_val": 0., "curvature_spdlmt": 100., "has_valid_data": True,
        }})
    return data

for module in stop2go_modules:
    with (
        patch.object(module, "_get_frame_risk_params", side_effect=lambda frame: frame["params"] if "params" in frame else {
            "f_stationary": True, "f_np_on": True,
        }),
        patch.object(module, "_is_set_speed_stable", return_value=True),
        patch.object(module, "_is_primary_sequence_frame", return_value=True),
    ):
        interrupted = module._find_risk_segments(stop2go_frames(True))
        assert interrupted == [], (module.__name__, [len(segment) for segment in interrupted])
        completed = module._find_risk_segments(stop2go_frames(False))
        assert len(completed) == 1 and len(completed[0]) == 51, (module.__name__, completed)
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
