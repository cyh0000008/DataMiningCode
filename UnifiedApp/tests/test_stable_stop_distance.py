import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class StableStopDistanceTests(unittest.TestCase):
    def run_project(self, project, script):
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT / project,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_j6b_generated_reports_keep_median_and_exclusion_reason(self):
        self.run_project("J6B_UnifiedMining", r'''
import csv
import json
from pathlib import Path
import tempfile
import h5py
import numpy as np
from openpyxl import load_workbook
from DataMining import main
from bad_case_image_report import header_columns, read_cases

with tempfile.TemporaryDirectory() as folder:
    work = Path(folder)
    t = np.round(np.arange(0, 9.01, 0.1), 6)
    data = {
        "acceleration": np.zeros(t.size),
        "ego_speed": np.where(t < 6, 18., 0.),
        "acc_active": np.ones(t.size),
        "pedal_override": np.zeros(t.size),
        "front_range": np.where(t < 7, 2., 5.),
        "front_target_id": np.ones(t.size),
        "front_lat_range": np.zeros(t.size),
        "front_velocity_abs": np.zeros(t.size),
        "flt1_cutin_flag": np.zeros(t.size),
    }
    h5path = work / "synthetic.h5"
    with h5py.File(h5path, "w") as h5:
        h5.create_group("metadata")
        sample = h5.create_group("sample")
        sample.create_dataset("timestamp", data=t)
        signals = sample.create_group("signals")
        for key, values in data.items():
            signals.create_dataset(key, data=values)
    config = {
        "input_path": [str(work)], "powertrain_type": "PHEV",
        "signals": {key: "sample/signals/" + key for key in data},
        "enabled_metrics": ["follow_stop_min_frntobj_dis"],
        "bad_case_signal_plots": True, "output_dir": str(work / "run"),
        "bad_case_extract_config": str(work / "bad_case_extract.json"),
        "report_name": "Longitudinal_CPI_Report",
    }
    cfg = work / "config.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    (work / "bad_case_extract.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    assert main(["DataMining.py", str(cfg)]) == 0
    report = next((work / "run").rglob("bad_case_report.xlsx"))
    wb = load_workbook(report)
    try:
        cases = read_cases(wb.active, header_columns(wb.active))
        assert len(cases) == len(wb.active._images) == 1
        assert cases[0].values["screening_value"] == 5
        assert cases[0].values["stable_window_start_time_s"] == 7
        assert cases[0].end_s == 8
    finally:
        wb.close()
    csv_path = next((work / "run").rglob("follow_stop_min_frntobj_dis.csv"))
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert rows[0]["总Case"] == "1"
    with h5py.File(h5path, "r+") as h5:
        h5["sample/signals/front_velocity_abs"][65] = 0.5
    config["output_dir"] = str(work / "excluded")
    cfg.write_text(json.dumps(config), encoding="utf-8")
    assert main(["DataMining.py", str(cfg)]) == 0
    csv_path = next((work / "excluded").rglob("follow_stop_min_frntobj_dis.csv"))
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert rows[0]["总Case"] == "0" and "未统计" in str(rows)
    assert "前车未静止或重新起步" in str(rows)
''')

    def test_j6b_median_valid_counts_exclusions_and_plot(self):
        self.run_project("J6B_UnifiedMining", r'''
from copy import deepcopy
from pathlib import Path
import tempfile
import numpy as np
from unittest.mock import patch
from metric_utils import Segment, measure_segment
from filters.follow_stop_min_frntobj_dis import create_metric
from signal_preprocessor import SignalFrame, stitch_signal_frames
from bad_case_image_report import CaseRow, build_evidence_panel, evidence_markers, draw_case_plot

t = np.round(np.arange(0, 9.01, 0.1), 6)
stationary = t >= 6
fields = {
    "time_s": t, "acceleration": np.zeros(t.size), "acceleration_total": np.zeros(t.size),
    "stationary_flag": stationary, "moving_flag": ~stationary,
    "ego_speed_mps": np.where(stationary, 0, 5), "ego_speed": np.where(stationary, 0, 18),
    "acc_active_flag": np.ones(t.size, dtype=bool), "pedal_override_flag": np.zeros(t.size),
    "front_valid": np.ones(t.size, dtype=bool), "front_target_id": np.ones(t.size),
    "front_velocity_abs": np.zeros(t.size), "front_ttc": np.ones(t.size),
    "cutin_valid": np.zeros(t.size), "cutin_ttc_calc": np.full(t.size, np.inf),
    "front_range": np.where(t < 7, 2.0, 3.5),
}
fields["front_range"][t == 7.5] = 15
frame = SignalFrame("synthetic", "", {}, fields, {})
metric = create_metric()
metric.powertrain_type = "PHEV"
result = metric.run(frame)
assert result.error is None, result.details
assert result.total_cases == 1 and result.bad_cases == 0, result
value, when, data = measure_segment(frame, Segment(0, 60), "stop_front_distance")
assert value == 3.5 and when == 8 and data["stable_sample_count"] == 11
# The endpoint confirms coverage but contributes no sample if strictly outside.
irregular = deepcopy(frame)
irregular.fields["time_s"][80] = 8.01
irregular.fields["front_range"][80] = 100
assert measure_segment(irregular, Segment(0, 60), "stop_front_distance")[0] == 3.5

for field, replacement, reason in (
    ("stationary_flag", False, "自车"), ("front_valid", False, "目标"),
    ("front_target_id", 2, "切换"), ("front_velocity_abs", 0.3, "起步"),
    ("front_range", float("nan"), "距离"), ("acc_active_flag", False, "ACC"),
):
    changed = deepcopy(frame)
    changed.fields[field][65] = replacement
    result = metric.run(changed)
    assert result.error is None, result.details
    assert result.total_cases == result.bad_cases == 0, (field, result)
    assert any("未统计" in detail and reason in detail for detail in result.details), result.details
short = deepcopy(frame)
short.fields = {key: values[:75] for key, values in short.fields.items()}
result = metric.run(short)
assert result.total_cases == result.bad_cases == 0 and "不足完整2秒" in str(result.details)
gap = deepcopy(frame)
keep = (t < 6.5) | (t > 6.8)
gap.fields = {key: values[keep] for key, values in gap.fields.items()}
result = metric.run(gap)
assert result.total_cases == 0 and "采样缺口" in str(result.details)

# No exact physical sample equals the median: plot must show an event statistic.
bad = deepcopy(frame)
bad.fields["front_range"][t >= 7] = 5
result = metric.run(bad)
assert result.total_cases == result.bad_cases == 1
event = result.events[0]
assert event.end_s == 8 and event.values["screening_value"] == 5
assert event.values["stop_time_s"] == 6 and event.values["stable_window_start_time_s"] == 7
case = CaseRow(2, metric.name, "synthetic", event.start_s, event.end_s, event.severity, event.message, event.values)
panel = build_evidence_panel(bad, case, np.ones(t.size, dtype=bool))
assert "事件统计值" in panel.title
np.testing.assert_allclose(panel.lines[0].values[(t >= 7) & (t <= 8)], 5)
assert not np.any(panel.highlight[t < 7]) and not np.any(panel.highlight[t > 8])
assert dict(evidence_markers(case))["稳定取值终点"] == 8
with tempfile.TemporaryDirectory() as folder:
    draw_case_plot(bad, case, Path(folder) / "distance.png")
    assert (Path(folder) / "distance.png").is_file()
for powertrain, distance, expected_bad in (
    ("ICE", 2.5, 0), ("ICE", 4.5, 0), ("PHEV", 3, 0),
    ("BEV", 4, 0), ("PHEV", 2.99, 1), ("BEV", 4.01, 1),
):
    changed = deepcopy(frame)
    changed.fields["front_range"][t >= 7] = distance
    metric.powertrain_type = powertrain
    result = metric.run(changed)
    assert result.total_cases == 1 and result.bad_cases == expected_bad, result
# The stop starts in the primary file; the stable interval is supplied by its peer.
first, second = deepcopy(bad), deepcopy(bad)
first.fields = {key: values[:71] for key, values in bad.fields.items()}
second.fields = {key: values[71:] for key, values in bad.fields.items()}
stitched = stitch_signal_frames(first, second, 7.1)
metric.powertrain_type = "PHEV"
result = metric.run(stitched)
assert result.total_cases == result.bad_cases == 1
assert result.events[0].values["cross_h5"]
''')

    def test_mmt_stable_window_target_identity_and_exclusions(self):
        self.run_project("MMT_UnifiedMining", r'''
from copy import deepcopy
import math
import numpy as np
from metrics.topic.code import follow_stop_min_frntobj_dis as module
from DataMining import build_two_done_frames

def make_frame(t):
    timestamp = round((t + 1) * 1000, 6)
    distance = 2 if t < 2 else 3.5
    return {
        "unp_timestamp_ms": timestamp,
        "unp_frame_data": {
            "InputMessageInfo": {"messageInfos": {
                "/perception/fusion/object_auto": {"metaTimeMs": timestamp},
                "/vehicle/chassis_report": {"metaTimeMs": timestamp},
            }},
            "NP_State": {"f_Np_on": True}, "Acc_info": {"SetSpeed": 50},
        },
        "matched_chassis": {"stationary": t >= 1, "vehicle_speed_average": 0 if t >= 1 else 5, "timestamp_ms": timestamp},
        "matched_fusion": {
            "timestamp_ms": timestamp, "min_ttc": 10, "f_potential_cipv_by_pos": True,
            "has_valid_fusion_data": True, "closest_obj_id": 7,
            "closest_obj_vel": 0, "closest_obj_x": distance + 2.0 + 3.8,
            "closest_obj_len": 4.0, "closest_obj_y": 0.1,
        },
    }

frames = [make_frame(float(t)) for t in np.round(np.arange(0, 4.01, 0.1), 6)]
frames[25]["matched_fusion"]["closest_obj_x"] = 50
bad, details, stats = module._core_process(frames)
assert not bad and stats["All Case Count"] == 1 and stats["Bad Case"] == 0, (details, stats)
assert "lon_dis:3.500000" in str(details) and "sample_count:11" in str(details)
assert "stable_window_ms:[3000.000,4000.000]" in str(details)
for key, value, reason in (
    ("closest_obj_id", 8, "切换"), ("closest_obj_id", None, "目标"),
    ("closest_obj_vel", 0.21, "起步"), ("closest_obj_len", None, "车长"),
    ("has_valid_fusion_data", False, "目标"), ("timestamp_ms", 0, "过期"),
):
    changed = deepcopy(frames)
    changed[15]["matched_fusion"][key] = value
    bad, details, stats = module._core_process(changed)
    assert not bad and stats["All Case Count"] == stats["Bad Case"] == 0, (key, stats, details)
    assert stats["Excluded Case"] == 1 and reason in str(details), details
changed = deepcopy(frames)
changed[15]["matched_chassis"]["stationary"] = False
bad, details, stats = module._core_process(changed)
assert stats["All Case Count"] == 0 and "自车重新移动" in str(details)
for changed, reason in ((frames[:25], "不足完整2秒"), (frames[:15] + frames[19:], "采样缺口")):
    bad, details, stats = module._core_process(changed)
    assert stats["All Case Count"] == 0 and reason in str(details), details
# Repeated UNP references must not overweight the median.
repeated = deepcopy(frames)
repeated.insert(26, deepcopy(repeated[25]))
bad, details, stats = module._core_process(repeated)
assert stats["All Case Count"] == 1 and "lon_dis:3.500000" in str(details)
for distance, expected_bad in ((3, 0), (4, 0), (2.99, 1), (4.01, 1), (0, 1)):
    changed = deepcopy(frames)
    for frame in changed[20:]:
        frame["matched_fusion"]["closest_obj_x"] = distance + 2 + 3.8
    bad, details, stats = module._core_process(changed)
    assert stats["All Case Count"] == 1 and stats["Bad Case"] == expected_bad, (distance, details, stats)
# Cross-done observation still belongs to the scene that began in the primary.
combined = build_two_done_frames(frames[:22], "primary", frames[22:], "peer", 10)
bad, details, stats = module._core_process(combined)
assert stats["All Case Count"] == 1 and not bad
''')

    def test_mmt_fusion_parser_retains_chosen_track_id(self):
        self.run_project("MMT_UnifiedMining", r'''
from unittest.mock import patch
import longitudinal_preprocessor as module

def obj(track_id, x, available=True):
    return f"""
track_id: {track_id}
available: {available}
crash_risk_ttc: 10
relative_position:
  x: {x}
  y: 0.1
velocity_relative_to_ground:
  x: 0.0
acceleration_relative_to_ground:
  x: 0.0
shape:
  length: 4.0
extra_json: ""
"""
text = "meta:\n sensor_timestamp_us: 1000000\nperception_fusion_objects_data:\n" + obj(10, 15) + obj(20, 9.3) + obj(30, 8, False)
with patch.object(module, "_split_timestamped_paragraphs", return_value=[("1", text)]):
    frames = module._parse_fusion_obj_auto(text)
assert len(frames) == 1 and frames[0]["closest_obj_id"] == 20, frames
assert frames[0]["closest_obj_x"] == 9.3 and frames[0]["closest_obj_len"] == 4
''')


if __name__ == "__main__":
    unittest.main()
