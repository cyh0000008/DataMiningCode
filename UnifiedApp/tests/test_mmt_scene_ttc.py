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
sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))

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
        "matched_fusion": {"min_ttc": ttc},
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
        for value in (4.5, 5., None, 0., float("inf"), float("nan")):
            data = frames(curve, value)
            segments = module._find_risk_segments(data)
            assert len(segments) == 1, (name, value, segments)
            assert segments[0] == data[start:], (name, value)
        for value in (3.5, 4., 4.49):
            assert not module._find_risk_segments(frames(curve, value)), (name, value)
        # A low TTC early in a scene prevents combining short candidates.
        data = frames(curve, 4.5)
        data[40]["matched_fusion"]["min_ttc"] = 4.49
        data = data[:81]
        assert not module._find_risk_segments(data), name
        # A late violation excludes its frame; stop2go needs a new stationary launch.
        data = frames(curve, 4.5)
        data[60]["matched_fusion"]["min_ttc"] = 4.49
        segments = module._find_risk_segments(data)
        expected = [data[start:60], data[61:]] if curve else [data[1:60]]
        assert segments == expected, (name, segments)
        data = frames(curve, 4.5)
        data[start]["matched_fusion"]["min_ttc"] = 4.49
        expected = [data[1:]] if curve else []
        assert module._find_risk_segments(data) == expected, name
'''],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
