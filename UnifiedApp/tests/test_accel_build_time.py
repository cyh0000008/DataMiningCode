import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class AccelBuildTimeTests(unittest.TestCase):
    def test_green_light_jerk_has_only_upper_limit(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
import traffic_light_stop2go_max_jerk as module
segment = [{"unp_frame_data": {}, "matched_chassis": {}}]
for value, bad in ((-0.1, False), (0., False), (.5, False), (.999, False),
                   (1., False), (2.5, False), (2.51, True)):
    with patch.object(module, "_find_risk_segments", return_value=[segment]), \
         patch.object(module, "_calc_segment_max_jerk", return_value=(value, 1000., 0.)), \
         patch.object(module.lonprepro, "_get_unp_fusion_meta_time", return_value=0.):
        found, _, stats = module._core_process(segment)
    assert found is bad and stats["Bad Case"] == int(bad), stats
    assert stats["All Case Count"] == 1
    expected = 1. if bad else 0.
    assert abs(stats["Severity Score"] - expected) < 1e-8, stats
    assert abs(module._cal_severity_score(value) - expected) < 1e-8
'''],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_follow_start_jerk_has_only_upper_limit(self):
        for profile in ("J6B", "MMT"):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    [sys.executable, "-c", r'''
from pathlib import Path
import sys
from unittest.mock import patch
import numpy as np

if sys.argv[1] == "J6B":
    import filters.follow_start_jerk as module
    from metric_utils import Segment
    from signal_preprocessor import SignalFrame
    metric = module.create_metric()
    frame = SignalFrame("synthetic", "", {}, {"time_s": np.array([0., 10.])}, {})
    assert metric.lower is None and metric.upper == 2.
    for value, bad in ((-0.1, False), (0., False), (.5, False), (.999, False),
                       (1., False), (2., False), (2.01, True)):
        with patch.object(module, "find_scene_segments", return_value=[Segment(0, 1)]), \
             patch.object(module, "measure_segment", return_value=(value, 1., {})):
            result = metric.evaluate(frame)
        assert result.total_cases == 1 and result.bad_cases == int(bad), result
        assert abs(result.severity_score - (1. if bad else 0.)) < 1e-8
        if bad:
            assert result.events[0].values["screening_lower"] is None
            assert result.events[0].values["screening_upper"] == 2.
else:
    sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
    import follow_start_jerk as module
    for value, valid in ((-0.1, True), (0., True), (.5, True), (.999, True),
                         (1., True), (1.5, True), (1.51, False)):
        measured = {"max_jerk": value}
        assert module._check_max_jerk_valid(measured) is valid
        assert abs(module._cal_severity_score(measured) - (0. if valid else 1.)) < 1e-8
''', profile],
                    cwd=ROOT / f"{profile}_UnifiedMining",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_removed_minimum_and_preserved_upper_bounds(self):
        for profile in ("J6B", "MMT"):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    [sys.executable, "-c", r'''
from pathlib import Path
import sys
from unittest.mock import patch
import numpy as np

if sys.argv[1] == "J6B":
    from filters.stop2go_max_acc_time import create_metric
    from metric_utils import Segment
    from signal_preprocessor import SignalFrame
    import filters.stop2go_max_acc_time as module
    frame = SignalFrame("synthetic", "", {}, {"time_s": np.array([0., 10.])}, {})
    for powertrain, upper in (("ICE", 4.5), ("PHEV", 4.), ("BEV", 4.)):
        metric = create_metric()
        metric.powertrain_type = powertrain
        for value, bad in ((0., False), (.5, False), (.999, False), (1., False),
                           (upper, False), (upper + .01, True)):
            with patch.object(module, "find_scene_segments", return_value=[Segment(0, 1)]), \
                 patch.object(module, "measure_segment", return_value=(value, value, {})):
                result = metric.evaluate(frame)
            assert result.total_cases == 1 and result.bad_cases == int(bad), (powertrain, value, result)
            if not bad:
                assert result.severity_score == 0
            else:
                assert abs(result.severity_score - .5) < 1e-8
                assert result.events[0].values["screening_lower"] is None
                assert result.events[0].values["screening_upper"] == upper
else:
    sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
    import stop2go_max_acc_time as module
    for powertrain, upper in (("ICE", 4.), ("PHEV", 3.5), ("BEV", 3.5)):
        for value, valid in ((0., True), (.5, True), (.999, True), (1., True),
                             (upper, True), (upper + .01, False)):
            measured = {"acc_build_time": value}
            assert module._check_max_accel_time_valid(measured, upper) is valid
            severity = module._cal_severity_score(measured, upper)
            assert abs(severity - (0. if valid else .5)) < 1e-8
''', profile],
                    cwd=ROOT / f"{profile}_UnifiedMining",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
