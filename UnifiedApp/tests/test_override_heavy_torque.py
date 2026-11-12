import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class OverrideHeavyTorqueTests(unittest.TestCase):
    def test_both_platforms_torque_and_real_metrics(self):
        for profile in ("J6B", "MMT"):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    [sys.executable, "-c", r'''
from pathlib import Path
import tempfile
import numpy as np
import h5py
from override_braking_torque import find_torque_braking_events

# Test exact onset boundary and incomplete/invalid evidence independently of the
# acceleration detector so the acceptance deadline is directly measurable.
t = np.round(np.arange(0, 3.01, .1), 6)
release = 10
override = np.where(t < 1., 1., 0.)
active = np.ones(len(t))
drive = np.full(len(t), 200.)
def callback(r, onset, end):
    return {"start_index": r, "end_index": min(end, r + 3)} if end >= r + 2 else None
def find(brake, **kwargs):
    return find_torque_braking_events(t, active, override, drive, brake, callback, **kwargs)
brake = np.where(t >= 1.2, 250., 0.)
events = find(brake)
assert len(events) == 1 and t[events[0]["torque_onset_index"]] == 1.2, events
assert events[0]["torque_change"] >= 120
assert not find(np.where(t >= 1.3, 300., 0.))
assert not find(brake, primary_end=.99)
active[t >= 1.2] = 0
assert not find(brake)  # cannot complete the braking event after ACC exit
active[:] = 1
active[t >= 1.4] = 0
assert len(find(brake)) == 1  # already-completed event survives later ACC exit
active[:] = 1
bad = brake.copy()
bad[(t >= 1.2) & (t <= 1.4)] = np.nan
assert not find(bad)
override[t >= 1.2] = 1
assert not find(brake)
override[t >= 1.] = 0
override[t >= 1.4] = 1
assert len(find(brake)) == 1  # already-completed event survives later override
override[t >= 1.] = 0
# Opposite directions never qualify.
assert not find(np.where(t >= 1.2, -300., 0.))
falling_drive = np.where(t >= 1.2, -100., 200.)
events = find_torque_braking_events(t, active, override, falling_drive, np.zeros(len(t)), callback)
assert len(events) == 1 and events[0]["torque_type"] == "drive"
for invalid_drive, invalid_brake in ((np.full(len(t), np.nan), brake),
                                     (drive, np.full(len(t), np.nan))):
    try:
        find_torque_braking_events(t, active, override, invalid_drive, invalid_brake, callback)
    except ValueError:
        pass
    else:
        raise AssertionError("all-invalid torque must not be reported as a successful zero")

# Resolve the inclusive deadline to one millisecond, and reject a rise that
# was already in progress before the override release.
fine_t = np.round(np.arange(0, 3.001, .001), 6)
fine_active = np.ones(len(fine_t))
fine_override = (fine_t < 1.).astype(float)
fine_drive = np.full(len(fine_t), 200.)
def find_fine(start):
    return find_torque_braking_events(
        fine_t, fine_active, fine_override, fine_drive,
        np.where(fine_t >= start, 500., 0.),
        lambda r, onset, end: {"start_index": r, "end_index": min(end, r + 200)},
    )
assert len(find_fine(1.2)) == 1
assert not find_fine(1.201)
assert not find_fine(.95)

# Exercise both actual metric implementations with the same realistic shape.
t = np.round(np.arange(0, 3.01, .01), 6)
override = (t < 1.).astype(float)
active = np.ones(len(t))
accel = np.where(t < 1.05, 0., np.where(t < 1.45, -2.2 - 8 * (t - 1.05), -5.4))
brake = np.clip((t - 1.10) * 5000., 0., 500.)
drive = np.full(len(t), 200.)
import sys
profile = sys.argv[1]
if profile == "J6B":
    from signal_preprocessor import SignalFrame
    from filters.acc_heavy_braking_after_override_release_count import create_metric
    from reporting import sum_distance_km, write_summary_sheet, StreamingReportWriter
    from openpyxl import Workbook, load_workbook
    for fields in (
        {"actual_drive_torque": drive},
        {"actual_drive_torque": drive, "actual_brake_torque": np.full(len(t), np.nan)},
    ):
        frame = SignalFrame("invalid", "", {}, {
            "time_s": t, "acceleration": accel, "acc_active_flag": active,
            "pedal_override_flag": override, **fields,
        }, {})
        frame.distance_km = 10.0
        result = create_metric().run(frame)
        assert result.status in ("SKIPPED", "ERROR") and sum_distance_km([result]) == 0
        book = Workbook()
        write_summary_sheet(book.active, {create_metric().name: [result]})
        assert book.active["G2"].value == "未统计"
        book.close()
        with tempfile.TemporaryDirectory() as output:
            writer = StreamingReportWriter(Path(output), "report.xlsx", [create_metric()])
            writer.write_result(create_metric().name, result)
            path = writer.close()
            book = load_workbook(path)
            assert book["所有指标统计汇总"]["G2"].value == "未统计", [
                cell.value for cell in book["所有指标统计汇总"][2]
            ]
            book.close()
    def run(accel, brake, drive, override, active):
        frame = SignalFrame("synthetic", "", {}, {
            "time_s": t, "acceleration": accel, "acc_active_flag": active,
            "pedal_override_flag": override, "actual_brake_torque": brake,
            "actual_drive_torque": drive,
        }, {})
        return create_metric().evaluate(frame).events
else:
    from signal_lib import FolderSession
    from metrics.can.code import acc_heavy_braking_after_override_release_count as metric
    from DataMining import generate_unified_report
    import csv
    from openpyxl import load_workbook
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder)
        name = "can__acc_heavy_braking_after_override_release_count"
        timestamp = "2026-10-28_10-00-00"
        with (output / (name + "_" + timestamp + ".csv")).open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=["指标来源", "文件夹路径", "符合要求的内容",
                                                   "总Case", "Bad Case", "段里程(km)"])
            writer.writeheader()
            writer.writerow({"指标来源": "can", "文件夹路径": "invalid",
                             "符合要求的内容": "未统计：缺少信号", "段里程(km)": 0})
        path = generate_unified_report(output, "report", timestamp, {name: "MPI"})
        book = load_workbook(path)
        assert book["所有指标统计汇总"]["H2"].value == "未统计", [
            cell.value for cell in book["所有指标统计汇总"][2]
        ]
        book.close()
    def run(accel, brake, drive, override, active):
        signals = {
            metric.ACCELERATION_SIGNAL: accel, metric.ACC_ACTIVE_SIGNAL: active,
            metric.PEDAL_OVERRIDE_SIGNAL: override, metric.ACTUAL_BRAKE_TORQUE_SIGNAL: brake,
            metric.ACTUAL_DRIVE_TORQUE_SIGNAL: drive,
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.h5"
            by_message = {}
            for name, value in signals.items():
                message, field = name.split(".", 1)
                by_message.setdefault(message, {})[field] = value
            with h5py.File(path, "w") as h5:
                channel = h5.create_group("sample").create_group("Channel1")
                for message, fields in by_message.items():
                    data = np.zeros(len(t), dtype=[("timestamp", "f8")] + [(x, "f8") for x in fields])
                    data["timestamp"] = t
                    for field, values in fields.items():
                        data[field] = values
                    channel.create_dataset(message, data=data)
            return metric.count_acc_heavy_braking_after_override_release_events(FolderSession(path))["events"]

events = run(accel, brake, drive, override, active)
assert len(events) == 1, events
assert not run(accel, np.zeros(len(t)), drive, override, active)
assert not run(np.zeros(len(t)), brake, drive, override, active)
assert not run(accel, np.clip((t - 1.4) * 5000., 0., 500.), drive, override, active)
assert not run(accel, brake, drive, np.zeros(len(t)), active)
override2 = np.where(t >= 1.17, 1., override)
assert not run(accel, brake, drive, override2, active)
drive_fall = 200. - np.clip((t - 1.10) * 5000., 0., 500.)
assert len(run(accel, np.zeros(len(t)), drive_fall, override, active)) == 1
late_accel = np.where(t < 1.4, 0., np.where(t < 1.8, -2.2 - 8 * (t - 1.4), -5.4))
assert not run(late_accel, brake, drive, override, active)
for invalid_drive, invalid_brake in ((np.full(len(t), np.nan), brake),
                                     (drive, np.full(len(t), np.nan))):
    try:
        run(accel, invalid_brake, invalid_drive, override, active)
    except ValueError:
        pass
    else:
        raise AssertionError("all-invalid torque must fail the actual metric")
''', profile],
                    cwd=ROOT / f"{profile}_UnifiedMining",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, encoding="utf-8", errors="replace", timeout=90,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
