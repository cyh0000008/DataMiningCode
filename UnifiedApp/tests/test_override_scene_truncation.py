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
assert [(s.start, s.end) for s in follow_start_segments(start)] == [(10, 19)]
assert measure_segment(start, Segment(10, 19), "start_latency")[0] is None

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
        expected = [data[start:60], data[61:]] if curve else [data[1:60]]
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
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
