from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class UninterferedMaxAccelerationTests(unittest.TestCase):
    def test_event_bounds_and_unchanged_measurements(self):
        for profile in ("J6B", "MMT"):
            with self.subTest(profile=profile):
                checked = subprocess.run(
                    [sys.executable, "-c", """
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd()))
profile = sys.argv[1]
if profile == "J6B":
    from filters import _driving_uninterfered_accel as scene
    from reporting import metric_value_display
else:
    from metrics.topic.code import _driving_uninterfered_accel_scene as scene
for powertrain in ("ICE", "PHEV", "BEV"):
    for band, speed in (("0_30", 10), ("30_60", 40), ("60_90", 70), ("90_plus", 100)):
        observations = [
            dict(initial_speed_kph=speed, target_speed_kph=speed+20, start_s=i, end_s=i+1,
                 max_acceleration_mps2=value, acceleration_build_time_s=0.8,
                 average_acceleration_mps2=1.2)
            for i, value in enumerate((1.49, 1.5, 2.0, 2.5, 2.51))
        ]
        if profile == "J6B":
            frame = SimpleNamespace(paths={key: key for key in scene.REQUIRED_PATHS},
                source_path="synthetic", distance_km=1.0, distance_status="OK")
            with patch.object(scene, "_find_observations", return_value=observations):
                metric=scene.create_metric_class(band, "max_acc")()
                metric.powertrain_type=powertrain
                result=metric.evaluate(frame)
                assert result.total_cases==5 and result.bad_cases==2
                assert result.result_mode=="case_rate" and len(result.events)==2
                assert result.severity_score>0
                assert metric_value_display("CPI",5,2,1.0)=="2.5(5/2)"
                for measure, field, samples in (
                    ("avg_acc","average_acceleration_mps2",(1.49,1.5,1.51)),
                    ("accel_build_time","acceleration_build_time_s",(3.49,3.5,3.51)),
                ):
                    observations[:] = [dict(observations[0], **{field: value}) for value in samples]
                    other_metric=scene.create_metric_class(band,measure)()
                    other_metric.powertrain_type=powertrain
                    other=other_metric.evaluate(frame)
                    assert other.result_mode=="case_rate" and other.total_cases==3 and other.bad_cases==2
            with patch.object(scene, "_find_observations", return_value=[]):
                assert metric.evaluate(frame).total_cases==0
        else:
            frames=[{"_powertrain_type":powertrain}]
            with patch.object(scene,"extract_observations",return_value=observations):
                bad,details,stats=scene.run_metric(frames,band,"max_acc")
                assert bad and stats["All Case Count"]==5 and stats["Bad Case"]==2
                assert stats["Severity Score"]>0
                for measure, field, samples in (
                    ("avg_acc","average_acceleration_mps2",(1.49,1.5,1.51)),
                    ("accel_build_time","acceleration_build_time_s",(3.49,3.5,3.51)),
                ):
                    observations[:] = [dict(observations[0], **{field: value}) for value in samples]
                    _,_,other=scene.run_metric(frames,band,measure)
                    assert other["Bad Case"]==2 and other["All Case Count"]==3
            with patch.object(scene,"extract_observations",return_value=[]):
                _,_,empty=scene.run_metric(frames,band,"max_acc")
                assert empty["All Case Count"]==0 and empty["Bad Case"]==0
print(profile, "all three powertrains and four speed bands: OK")
""", profile],
                    cwd=ROOT / (profile + "_UnifiedMining"),
                    capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                )
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
