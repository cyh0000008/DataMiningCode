import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MMTSceneTTCTests(unittest.TestCase):
    def test_all_curve_and_stop2go_ttc_gates(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
import longitudinal_preprocessor as lonprepro

for fusion, accepted in (
    ({"closest_front_x": None}, True),
    ({"closest_front_x": 20., "closest_front_ttc": 9.}, False),
    ({"closest_front_x": 21., "closest_front_ttc": 8.}, False),
    ({"closest_front_x": 20.01, "closest_front_ttc": 8.01}, True),
    ({"closest_front_x": 30., "closest_front_ttc": None}, False),
    ({"closest_front_x": None, "f_potential_cipv_by_pos": True}, False),
):
    assert lonprepro.is_front_clear(fusion) == accepted, fusion

def frames(curve, ttc):
    return [{
        "unp_timestamp_ms": i * 100,
        "unp_frame_data": {
            "NP_State": {"f_Np_on": True},
            "Acc_info": {"SetSpeed": 20.},
            "curvature_info": {
                "is_curv_v_limit_enable": curve, "max_curvature": 0.005,
            },
        },
        "matched_fusion": {"min_ttc": ttc, "closest_front_x": 30., "closest_front_ttc": ttc},
        "matched_chassis": {
            "stationary": i == 0 and not curve,
            "vehicle_speed_average": 10., "accleration_on_wheel": 1.,
        },
        "matched_msd_control": {"slope_acc": 0.},
    } for i in range(121)]

for curve, names in (
    (True, ("curvature_min_acc", "curvature_min_jerk", "curvature_min_speed")),
    (False, ("stop2go_max_acc", "stop2go_max_acc_time",
             "stop2go_max_jerk", "stop2go_start_time")),
):
    for name in names:
        module = importlib.import_module(name)
        start = 0 if curve else 1
        for value in (8.01, 20., float("inf")):
            data = frames(curve, value)
            segments = module._find_risk_segments(data)
            assert len(segments) == 1, (name, value, segments)
            assert segments[0] == data[start:], (name, value)
        for value in (3.5, 8., None, 0., float("nan")):
            assert not module._find_risk_segments(frames(curve, value)), (name, value)
        # A low TTC early in a scene prevents combining short candidates.
        data = frames(curve, 9.)
        data[40]["matched_fusion"]["closest_front_ttc"] = 8.
        data = data[:81]
        assert not module._find_risk_segments(data), name
        # A late violation excludes its frame; stop2go needs a new stationary launch.
        data = frames(curve, 9.)
        data[60]["matched_fusion"]["closest_front_ttc"] = 8.
        segments = module._find_risk_segments(data)
        expected = [data[start:60], data[61:]] if curve else [data[1:60]]
        assert segments == expected, (name, segments)
        data = frames(curve, 9.)
        data[start]["matched_fusion"]["closest_front_ttc"] = 8.
        expected = [data[1:]] if curve else []
        assert module._find_risk_segments(data) == expected, name
        for distance, accepted in ((20., False), (20.01, True), (float("nan"), False)):
            data = frames(curve, 9.)
            for frame in data:
                frame["matched_fusion"]["closest_front_x"] = distance
            assert bool(module._find_risk_segments(data)) == accepted, (name, distance)
        data = frames(curve, None)
        for frame in data:
            frame["matched_fusion"]["closest_front_x"] = None
        assert module._find_risk_segments(data), name
        if not curve:
            data = frames(False, 9.)[:62]
            for frame in data[:-1]:
                frame["matched_chassis"]["vehicle_speed_average"] = 5.
            data[-1]["matched_fusion"]["closest_front_x"] = 20.
            assert not module._find_risk_segments(data), name
            data[-2]["matched_chassis"]["vehicle_speed_average"] = 8.
            assert module._find_risk_segments(data) == [data[1:-1]], name

def light_frames(green, target_at=None, distance=30., ttc=9., complete=False):
    data = []
    for i in range(16):
        red = i == 0 if green else True
        stationary = i < 2 if green else i == 15
        data.append({
            "unp_timestamp_ms": i * 100.,
            "unp_frame_data": {
                "NP_State": {"f_Np_on": True}, "Acc_info": {"SetSpeed": 20.},
                "TrafficLight": {"f_RedLightOn": red, "stop_distance": 4.},
            },
            "matched_fusion": {
                "closest_front_x": distance if target_at is None or i == target_at else None,
                "closest_front_ttc": ttc,
            },
            "matched_chassis": {
                "stationary": stationary,
                "vehicle_speed_average": (
                    float(max(0, i - 1)) * (1. if complete else .3) if green
                    else (0. if stationary else 5.)
                ),
                "accleration_on_wheel": 1.,
            },
        })
    return data

for name in (
    "traffic_light_stop2go_time", "traffic_light_stop2go_accel_time",
    "traffic_light_stop2go_max_accel", "traffic_light_stop2go_max_accel_time",
    "traffic_light_stop2go_max_jerk", "redlight_stop_dis", "redlight_min_jerk",
):
    module = importlib.import_module(name)
    green = name.startswith("traffic")
    with (
        patch.object(module.lonprepro, "_get_unp_fusion_meta_time", return_value=1.),
        patch.object(module, "_is_non_straight_driving_scene", return_value=False),
        patch.object(module, "_is_set_speed_stable", return_value=True),
        patch.object(module, "_is_primary_sequence_frame", return_value=True),
    ):
        for distance, ttc, accepted in (
            (20., 9., False), (21., 8., False), (20.01, 8.01, True),
            (30., None, False), (None, None, True),
        ):
            result = module._find_risk_segments(light_frames(green, distance=distance, ttc=ttc, complete=True))
            assert bool(result) == accepted, (name, distance, ttc, result)
        # A late target failure must not complete an unfinished scene or bridge
        # to a later qualifying red-light stop.
        assert module._find_risk_segments(
            light_frames(green, target_at=13, distance=20., complete=False)
        ) == [], name
        if green:
            assert module._find_risk_segments(
                light_frames(True, target_at=13, distance=20., complete=True)
            ), name  # Completed before the target violation.

# CAN set-speed reach uses Fusion timing and the same lateral corridor gate.
import tempfile
from signal_lib import FolderSession
from metrics.can.code import acc_set_speed_reach as reach
from metrics.can.code import acc_unreasonable_low_speed_count as low

class Session(FolderSession):
    def __init__(self, folder):
        self.primary_h5_file = str(folder / "synthetic.h5")
    def common_time_range(self, signals):
        return 0., 12.
    def get_signal_slice(self, signal, start, end):
        return [i / 10 for i in range(121)], [40.] * 121
    def sample_signal_to_axis(self, signal, axis, **kwargs):
        value = 40. if signal == reach.SET_SPEED_SIGNAL else int(signal == reach.ACC_ACTIVE_SIGNAL)
        return [value] * len(axis)

def object_text(x, y, ttc):
    return (
        f"  - track_id: 1\n    available: True\n    crash_risk_ttc: {ttc}\n"
        f"    relative_position:\n      x: {x}\n      y: {y}\n"
        "    velocity_relative_to_ground:\n      x: 0\n"
        "    acceleration_relative_to_ground:\n      x: 0\n"
        '    shape:\n      length: 4\n    extra_json: ""\n'
    )

with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    fusion_file = folder / "_perception_fusion_object_auto.txt"
    session = Session(folder)
    assert reach.analyze_acc_set_speed_reach(session)["skip_reason"]
    for x, y, ttc, accepted in (
        (20., 0., 9., False), (21., 0., 8., False), (20.01, 1.5, 8.01, True),
        (10., 1.5, 1., False), (10., 1.51, 1., True),
        (10., -1.5, 1., False), (10., -1.51, 1., True), (-5., 0., 1., True),
    ):
        text = "".join(
            f"{1000000000000000000 + i} header:\nmeta:\n  sensor_timestamp_us: {i * 100000}\n"
            "perception_fusion_objects_data:\n" + object_text(x, y, ttc)
            for i in range(121)
        )
        fusion_file.write_text(text, encoding="utf-8")
        low._FUSION_PARSE_CACHE.clear()
        measured = reach.analyze_acc_set_speed_reach(session)
        assert bool(measured["all_case_count"]) == accepted, (x, y, ttc, measured)
        paragraph = "meta:\n sensor_timestamp_us: 0\nperception_fusion_objects_data:\n" + object_text(x, y, ttc)
        with patch.object(lonprepro, "_split_timestamped_paragraphs", return_value=[("1", paragraph)]):
            parsed = lonprepro._parse_fusion_obj_auto(paragraph)[0]
        assert lonprepro.is_front_clear(parsed) == accepted, (x, y, parsed)
    # A single invalid frame splits a 12 s candidate into two <8 s fragments.
    text = "".join(
        f"{1000000000000000000 + i} header:\nmeta:\n  sensor_timestamp_us: {i * 100000}\n"
        "perception_fusion_objects_data:\n" + object_text(10. if i == 60 else 30., 0., 9.)
        for i in range(121)
    )
    fusion_file.write_text(text, encoding="utf-8")
    low._FUSION_PARSE_CACHE.clear()
    assert reach.analyze_acc_set_speed_reach(session)["all_case_count"] == 0
'''],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
