import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SETUP = r'''
from copy import deepcopy
import importlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
import longitudinal_preprocessor as p

def target(track_id, x=30., y=0., ttc=9., velocity=0.):
    return dict(id=track_id, x=x, y=y, ttc=ttc, velocity=velocity,
                acceleration=-1., length=4.)

def fusion(first, lead, lead_id=2, timestamp=1000.):
    objects = {item["id"]: item for item in (first, lead) if item is not None}
    original = {
        "timestamp_ms": timestamp, "has_valid_fusion_data": True,
        "objects_by_id": objects, "closest_obj_id": first["id"] if first else None,
        "in_lane_front": first,
    }
    return p.with_leadone_candidate(original, {"object_id": lead_id})

def frame(index, first, lead, lead_id=2, stationary=False, speed=5.):
    timestamp = 1000. + index * 100.
    return {
        "unp_timestamp_ms": timestamp,
        "unp_frame_data": {
            "InputMessageInfo": {"messageInfos": {
                "/perception/fusion/object_auto": {"metaTimeMs": timestamp},
                "/vehicle/chassis_report": {"metaTimeMs": timestamp},
            }},
            "NP_State": {"f_Np_on": True}, "Acc_info": {"SetSpeed": 20.},
        },
        "matched_chassis": {
            "timestamp_ms": timestamp, "stationary": stationary,
            "vehicle_speed_average": speed, "accleration_on_wheel": -1.,
        },
        "matched_fusion": fusion(first, lead, lead_id, timestamp),
    }

def object_text(track_id, x, y, ttc=9.):
    ttc_line = "" if ttc is None else f"    crash_risk_ttc: {ttc}\n"
    return (
        f"  - \n    track_id: {track_id}\n"
        "    type_info:\n      type_properties:\n        - \n          value: 1\n"
        "    available: True\n"
        f"{ttc_line}    relative_position:\n      x: {x}\n      y: {y}\n"
        "    velocity_relative_to_ground:\n      x: 0\n"
        "    acceleration_relative_to_ground:\n      x: -1\n"
        '    shape:\n      length: 4\n    extra_json: ""\n'
    )
'''


class MMTLeadoneCandidateTests(unittest.TestCase):
    def run_script(self, script):
        result = subprocess.run(
            [sys.executable, "-c", SETUP + script],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_candidate_matching_clearance_and_parsers(self):
        self.run_script(r'''
from unittest.mock import patch
import json

original = target(1)
lead = target(2, x=25., y=6.6)
combined = fusion(original, lead)
assert [item["id"] for item in combined["following_candidates"]] == [1, 2]
assert p.is_front_clear(combined)
assert len(fusion(original, lead, 1)["following_candidates"]) == 1
assert len(fusion(None, lead)["following_candidates"]) == 1
assert len(fusion(original, lead, -1)["following_candidates"]) == 1
assert not fusion(None, None, -1)["following_candidates"]
assert p.is_front_clear(fusion(None, None, -1))
assert not p.is_front_clear(fusion(original, lead, 999))
for key, value in (("x", 20.), ("ttc", 8.), ("ttc", None), ("x", float("nan"))):
    changed = deepcopy(lead)
    changed[key] = value
    assert not p.is_front_clear(fusion(original, changed)), (key, value)
assert not p.is_front_clear(fusion(target(1, x=19., ttc=100.), target(2, x=40., y=5., ttc=7.)))
shared = {"objects_by_id": {1: original, 2: lead}, "closest_obj_id": 1, "in_lane_front": original,
          "timestamp_ms": 1000.}
before = deepcopy(shared)
assert len(p.with_leadone_candidate(shared, {"object_id": 2})["following_candidates"]) == 2
assert shared == before
assert len(p.with_leadone_candidate(shared, {"object_id": -1})["following_candidates"]) == 1
assert p.with_leadone_candidate(shared, {"object_id": 2}, 1400.)["leadone_unmatched"]
assert p.with_leadone_candidate({"objects_by_id": {0: target(0)}}, {"object_id": 0})["following_candidates"]

payload = {"leadone_info": {"object_id": 2, "time_distance": 1.},
           "InputMessageInfo": {"messageInfos": {"/perception/fusion/object_auto": {"metaTimeMs": 1000.}}}}
escaped = json.dumps(payload).replace('"', r'\"').replace(r'\"object_id\"', '\\\n  ' + r'\"object_id\"')
assert p.parse_leadone_info('frame_id: "' + escaped + '"') == payload["leadone_info"]
assert p.parse_leadone_info('{"other_info": {}}') is None

text = "meta:\n sensor_timestamp_us: 1000000\nperception_fusion_objects_data:\n"
text += object_text(1, 30, 0, 9) + object_text(2, 12, 6.6, 1000) + object_text(3, 5, 5, 1)
with patch.object(p, "_split_timestamped_paragraphs", return_value=[("1", text)]):
    parsed = p._parse_fusion_obj_auto(text)[0]
assert parsed["closest_obj_id"] == 1
assert parsed["objects_by_id"][2]["ttc"] == 1000
assert [x["id"] for x in p.with_leadone_candidate(parsed, payload["leadone_info"])["following_candidates"]] == [1, 2]
assert not p.is_front_clear(p.with_leadone_candidate(parsed, payload["leadone_info"]))
assert p.is_front_clear(p.with_leadone_candidate(parsed, {"object_id": -1}))
data = [{"timestamp_ms": 1000., "frame_data": {
    "leadone_info": {"object_id": lead_id},
    "InputMessageInfo": {"messageInfos": {"/perception/fusion/object_auto": {"metaTimeMs": 1000.}}},
}} for lead_id in (2, -1, 1)]
joined = p._build_comprehensive_frame_dict(data, [parsed], [], [], [], [])
assert [len(f["matched_fusion"]["following_candidates"]) for f in joined] == [2, 1, 1]
assert "following_candidates" not in parsed
''')

    def test_non_following_scenes_check_both_targets(self):
        self.run_script(r'''
from slope_scene_utils import find_steady_slope_segments
data = [frame(i, target(1), target(2, x=10., y=6.), speed=20.) for i in range(121)]
for f in data:
    f["unp_frame_data"]["curvature_info"] = {"is_curv_v_limit_enable": True, "max_curvature": .005}
    f["matched_msd_control"] = {"slope_acc": 0.}
for name in ("curvature_min_acc", "curvature_min_jerk", "curvature_min_speed"):
    module = importlib.import_module(name)
    assert not module._find_risk_segments(data), name
    clear = deepcopy(data)
    for f in clear:
        f["matched_fusion"] = p.with_leadone_candidate(f["matched_fusion"], {"object_id": -1})
    assert len(module._find_risk_segments(clear)) == 1, name
params = {
    "velocity": 20., "slope_acc": .2, "curv_spd_limit": 30.,
    "has_valid_data": True, "f_np_on": True, "f_stationary": False,
    "f_redlight": False, "f_stop_dis": 999.,
}
assert not find_steady_slope_segments(data, lambda f: params)
assert len(find_steady_slope_segments(clear, lambda f: params)) == 1
''')

    def test_seven_following_metrics_keep_targets_independent(self):
        self.run_script(r'''
names = ("follow_stop_max_acc", "follow_stop_min_jerk", "follow_stop_ttc",
         "follow_stop_min_ttc_frntobj_dec", "follow_stop_min_frntobj_dis")
data = [frame(i, target(1, x=9.3, ttc=10.), target(2, x=12.3, y=5.8, ttc=15.),
              stationary=i >= 10, speed=0. if i >= 10 else 5.) for i in range(31)]
for name in names:
    module = importlib.import_module(name)
    segments = module._find_risk_segments(data)
    assert len(segments) == 2, (name, segments)
    assert {s[0]["matched_fusion"]["closest_obj_id"] for s in segments} == {1, 2}
    for segment in segments:
        target_id = segment[0]["matched_fusion"]["closest_obj_id"]
        assert all(f["matched_fusion"]["closest_obj_id"] == target_id for f in segment)
        assert all(f["matched_fusion"]["min_ttc"] == (10. if target_id == 1 else 15.) for f in segment)
    same = deepcopy(data)
    for f in same:
        f["matched_fusion"] = p.with_leadone_candidate(f["matched_fusion"], {"object_id": 1})
    assert len(module._find_risk_segments(same)) == 1, name
    missing = deepcopy(data)
    missing[5]["matched_fusion"]["following_candidates"] = missing[5]["matched_fusion"]["following_candidates"][:1]
    assert len(module._find_risk_segments(missing)) == 1, name

module = importlib.import_module("follow_stop_min_frntobj_dis")
bad, details, stats = module._core_process(data)
assert stats["All Case Count"] == 2 and stats["Bad Case"] == 1, (details, stats)
assert "lon_dis:3.500000" in str(details) and "lon_dis:6.500000" in str(details), details
assert "target_id:1" in str(details) and "target_id:2" in str(details), details
changed = deepcopy(data)
changed[15]["matched_fusion"]["following_candidates"] = changed[15]["matched_fusion"]["following_candidates"][:1]
bad, details, stats = module._core_process(changed)
assert stats["All Case Count"] == 1 and stats["Excluded Case"] == 1, (details, stats)

data = [frame(i, target(1, velocity=0. if i < 10 else 1.),
              target(2, y=6., velocity=0. if i < 12 else 1.),
              stationary=i <= 12, speed=0. if i <= 12 else min(6., (i - 12) * .5))
        for i in range(31)]
for f in data:
    f["matched_chassis"]["accleration_on_wheel"] = 1.
for name in ("follow_start_latency", "follow_start_jerk"):
    module = importlib.import_module(name)
    segments = module._find_risk_segments(data)
    assert len(segments) == 2, (name, segments)
    assert {(s[0]["matched_fusion"]["closest_obj_id"], s[0]["unp_timestamp_ms"]) for s in segments} == {
        (1, 2200.), (2, 2200.),
    }, name
    only_lead = deepcopy(data)
    for f in only_lead:
        f["matched_fusion"]["following_candidates"] = f["matched_fusion"]["following_candidates"][1:]
    assert len(module._find_risk_segments(only_lead)) == 1, name
    interrupted = deepcopy(only_lead)
    interrupted[11]["matched_fusion"]["following_candidates"] = []
    assert not module._find_risk_segments(interrupted), name
''')

    def test_following_ids_cannot_switch_or_bridge_gaps(self):
        self.run_script(r'''
def lead_only(data):
    for f in data:
        f["matched_fusion"]["following_candidates"] = [
            item for item in f["matched_fusion"]["following_candidates"] if item["id"] != 1
        ]
    return data

def replace_id(f, replacement):
    f["matched_fusion"]["following_candidates"][0]["id"] = replacement

stopping = lead_only([
    frame(i, target(1), target(2, y=6.), stationary=i >= 10, speed=0. if i >= 10 else 5.)
    for i in range(31)
])
for name in ("follow_stop_max_acc", "follow_stop_min_jerk", "follow_stop_ttc",
             "follow_stop_min_ttc_frntobj_dec", "follow_stop_min_frntobj_dis"):
    module = importlib.import_module(name)
    assert len(module._find_risk_segments(stopping)) == 1, name
    for mode in ("switch", "returning-id", "missing-id", "invalid"):
        changed = deepcopy(stopping)
        if mode == "switch":
            for f in changed[7:]:
                replace_id(f, 3)
        elif mode == "returning-id":
            replace_id(changed[7], 3)
        elif mode == "missing-id":
            changed[7]["matched_fusion"]["following_candidates"] = []
        else:
            changed[7]["matched_fusion"]["following_candidates"][0]["velocity"] = None
        assert not module._find_risk_segments(changed), (name, mode)

starting = lead_only([
    frame(i, target(1), target(2, y=6., velocity=0. if i < 10 else 1.),
          stationary=i <= 12, speed=0. if i <= 12 else min(6., (i - 12) * .5))
    for i in range(31)
])
for f in starting:
    f["matched_chassis"]["accleration_on_wheel"] = 1.
for name in ("follow_start_latency", "follow_start_jerk"):
    module = importlib.import_module(name)
    assert len(module._find_risk_segments(starting)) == 1, name
    # A new ID's moving state cannot use the old ID's stationary history.
    switched = deepcopy(starting)
    for f in switched[10:]:
        replace_id(f, 3)
    assert not module._find_risk_segments(switched), name
    # Loss/switch after ego launch cannot complete an undersized old scene.
    for mode in ("switch", "returning-id", "missing-id", "invalid"):
        changed = deepcopy(starting)
        if mode == "switch":
            for f in changed[14:]:
                replace_id(f, 3)
        elif mode == "returning-id":
            replace_id(changed[14], 3)
        elif mode == "missing-id":
            changed[14]["matched_fusion"]["following_candidates"] = []
        else:
            changed[14]["matched_fusion"]["following_candidates"][0]["velocity"] = None
        assert not module._find_risk_segments(changed), (name, mode)

module = importlib.import_module("follow_stop_min_frntobj_dis")
changed = deepcopy(stopping)
replace_id(changed[15], 3)
bad, details, stats = module._core_process(changed)
assert stats["All Case Count"] == 0 and stats["Excluded Case"] == 1, (details, stats)
''')

    def test_can_set_speed_reach_uses_lead_and_context_files(self):
        self.run_script(r'''
import json
import tempfile
import numpy as np
from signal_lib import FolderSession
from metrics.can.code import acc_set_speed_reach as reach
from metrics.can.code import acc_unreasonable_low_speed_count as low

t = np.round(np.arange(1., 13.01, .1), 6)
class Session(FolderSession):
    def __init__(self, folder):
        self.primary_h5_file = str(folder / "synthetic.h5")
    def common_time_range(self, signals):
        return float(t[0]), float(t[-1])
    def get_signal_slice(self, signal, start, end):
        return t.tolist(), [50.] * len(t)
    def sample_signal_to_axis(self, signal, axis, **kwargs):
        value = 0 if signal == reach.PEDAL_OVERRIDE_SIGNAL else 1 if signal == reach.ACC_ACTIVE_SIGNAL else 50.
        return [value] * len(axis)

def write(folder, indexes, lead_id, lead_x=10., lead_ttc=9., stale=False):
    unp_text, fusion_text = [], []
    for i in indexes:
        ms = float(t[i]) * 1000.
        payload = {"leadone_info": {"object_id": lead_id, "time_distance": .1},
                   "InputMessageInfo": {"messageInfos": {
                       "/perception/fusion/object_auto": {"inputMessageInfo": [
                           {"metaTimeMs": ms + (10000. if stale else 0.)}]},
                       "/vehicle/body_report": {"metaTimeMs": ms + 100.},
                   }}}
        stamp = f"stamp:\n  secs: {int(ms // 1000)}\n  nsecs: {int(round(ms % 1000)) * 1000000}\n"
        escaped = json.dumps(payload).replace('"', r'\"')
        escaped = escaped.replace(r'\"metaTimeMs', r'\"' + '\\' + '\n  ' + 'metaTimeMs')
        unp_text.append(f"{1000000000000000000 + i} seq: {i}\n" + stamp
                        + 'frame_id: "' + escaped + '"\n')
        fusion_text.append(f"{1000000000000000000 + i} header:\n" + stamp
                           + f"meta:\n  sensor_timestamp_us: {round(ms * 1000)}\nperception_fusion_objects_data:\n"
                           + object_text(1, 30., 0.) + object_text(2, lead_x, 6., lead_ttc))
    (folder / "_unp_planning_info.txt").write_text("".join(unp_text), encoding="utf-8")
    (folder / "_perception_fusion_object_auto.txt").write_text("".join(fusion_text), encoding="utf-8")
    low._UNP_PARSE_CACHE.clear()
    low._FUSION_PARSE_CACHE.clear()

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    session = Session(folder)
    for lead_id, x, ttc, accepted in (
        (-1, 10., 9., True), (1, 10., 9., True), (2, 10., 9., False),
        (2, 21., 8., False), (2, 20., 9., False), (2, 21., 9., True),
        (2, 21., None, False), (999, 30., 9., False),
    ):
        write(folder, range(len(t)), lead_id, x, ttc)
        result = reach.analyze_acc_set_speed_reach(session)
        assert result["all_case_count"] == int(accepted), (lead_id, x, ttc, result)
    parsed = low._parse_fusion_object_file(folder / "_perception_fusion_object_auto.txt")
    assert set(parsed[0]["objects_by_id"]) == {1, 2}
    assert not parsed[0]["has_front_target"]
    write(folder, range(len(t)), 2, 30., stale=True)
    assert not reach.analyze_acc_set_speed_reach(session)["all_case_count"]
    peer = folder / "peer"
    peer.mkdir()
    write(folder, range(61), 2, 30.)
    write(peer, range(61, len(t)), 2, 30.)
    assert reach.analyze_acc_set_speed_reach(session, folder_path=[folder, peer])["all_case_count"] == 1
    assert not reach.analyze_acc_set_speed_reach(session, folder_path=[folder])["all_case_count"]
''')


if __name__ == "__main__":
    unittest.main()
