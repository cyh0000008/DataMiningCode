import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SignalPlotPanelTests(unittest.TestCase):
    def test_separate_units_scales_and_states(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image
from signal_preprocessor import SignalFrame
from bad_case_image_report import build_panels, draw_case_plot, CaseRow, format_tick, y_range, wants_front_panel

t = np.linspace(0, 10, 101)
fields = {"time_s": t, "acceleration": np.sin(t)}
for name, value in {
    "ego_speed": 20., "set_speed": 30., "curv_speed_limit": 50.,
    "front_range": 100., "front_ttc": 200., "front_velocity_abs": 2.,
    "control_drive_request_torque": 5., "actual_drive_torque": 5000.,
    "control_brake_request_torque": 10., "actual_brake_torque": 10000.,
    "slope_deg": 2., "curvature": .004,
    "acc_active_flag": 1., "pedal_override_flag": 0.,
    "brake_request_type": 5., "abs_active": 1., "tcs_active": 0.,
    "vse_active": 0., "acc_request_state": 7.,
    "wheel_dir_lf": 1., "wheel_dir_rf": 1., "wheel_dir_lr": 1.,
    "wheel_dir_rr": 1., "gear": 8., "wheel_speed_lf": .1,
    "wheel_speed_rf": .1, "wheel_speed_lr": .1, "wheel_speed_rr": .1,
    "ego_speed_non_driven": 2.,
}.items():
    fields[name] = np.full(t.size, value)
frame = SignalFrame("synthetic", "", {}, fields, {})
frame.fields.update({
    "front_valid": np.ones(t.size),
    "cutin_valid": np.ones(t.size),
    "cutin_range_x": np.full(t.size, 40.),
    "cutin_velocity_abs": np.full(t.size, 3.),
    "cutin_ttc_calc": np.full(t.size, 15.),
})
mask = np.ones(t.size, dtype=bool)
name = "follow_slope_curv_torque_rollback"
panels = build_panels(frame, name, mask)
assert len({panel.title for panel in panels}) == len(panels)
for panel in panels:
    if panel.title == "纵向加速度":
        assert len(panel.lines) == 2 and panel.unit == "m/s2"
    elif panel.title.startswith("前车 / Cut-in："):
        assert 1 <= len(panel.lines) <= 2
    else:
        assert len(panel.lines) == 1, (panel.title, len(panel.lines))
by_label = {line.label: panel for panel in panels for line in panel.lines}
for label, unit in (("前车距离", "m"), ("TTC", "s"), ("前车速度", "km/h"),
                    ("坡度", "deg"), ("曲率", "1/m"), ("挡位", "enum"),
                    ("左前轮速", "raw"), ("非驱动轮车速", "km/h")):
    assert by_label[label].unit == unit, (label, by_label[label].unit)
assert len([p for p in panels if p.title.startswith("状态：")]) == 3
assert not {"ABS", "TCS", "VSE", "仲裁状态"} & by_label.keys()
assert by_label["前车距离"] is by_label["Cut-in距离"]
assert by_label["TTC"] is by_label["Cut-in TTC"]
assert by_label["前车速度"] is by_label["Cut-in速度"]
np.testing.assert_allclose(by_label["前车速度"].lines[1].values, 10.8)
for metric in ("curvature_min_acc", "stop2go_start_time", "slope_no_frnt_acc",
               "follow_start_latency", "acc_set_speed_reach", "acc_unreasonable_low_speed_count"):
    assert wants_front_panel(metric), metric
    assert any(p.title.startswith("前车 / Cut-in：") for p in build_panels(frame, metric, mask)), metric
assert not wants_front_panel("acc_rollback_count")
np.testing.assert_allclose(by_label["TTC"].lines[0].values, 200.)
np.testing.assert_allclose(by_label["曲率"].lines[0].values, .004)
np.testing.assert_allclose(by_label["弯道限速"].lines[0].values, 180.)
np.testing.assert_allclose(by_label["前车速度"].lines[0].values, 7.2)
assert format_tick(.004) != format_tick(.005)
assert format_tick(100.001, .001) != format_tick(100.002, .001)
low, high = y_range(by_label["曲率"].lines)
assert high - low < .001
# Missing signals must not shift unit assignment to remaining front signals.
del frame.fields["front_range"]
remaining = build_panels(frame, "follow_stop_min_jerk", mask)
assert next(p for p in remaining if p.title == "前车 / Cut-in：TTC").unit == "s"
frame.fields["cutin_valid"][:50] = 0
masked = build_panels(frame, "stop2go_max_acc", mask)
distance = next(p for p in masked if p.title == "前车 / Cut-in：距离")
assert np.all(np.isnan(distance.lines[0].values[:50]))
frame.fields["cutin_valid"][:] = 0
assert not any(
    line.label == "Cut-in距离"
    for p in build_panels(frame, "stop2go_max_acc", mask) for line in p.lines
)
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "separate_panels.png"
    case = CaseRow(2, name, "synthetic", 3., 6., 10., "")
    draw_case_plot(frame, case, path)
    with Image.open(path) as image:
        count = len(build_panels(frame, name, mask))
        assert image.height >= 180 + count * 240
        image.verify()
'''],
            cwd=ROOT / "J6B_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
