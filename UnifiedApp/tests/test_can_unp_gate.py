import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CanUnpGateTests(unittest.TestCase):
    def test_pipeline_combined_activation_and_missing_topic(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
import csv
import importlib
import json
from pathlib import Path
import tempfile
import numpy as np
import h5py
from openpyxl import load_workbook
from DataMining import (PipelineConfig, MetricModule, RunState, process_can_folder,
                        generate_unified_report, TASK_STATUS_SKIPPED_UNP, TASK_STATUS_SUCCESS)
from can_unp_gate import UnpActiveGate, UnpAlignmentError, requires_unp, validate_coverage
from signal_lib import FolderSession
from metrics.can.code import acc_unreasonable_low_speed_count as parser

def write_unp(folder, times, states):
    chunks = []
    for i, (timestamp, state) in enumerate(zip(times, states)):
        payload = {} if state is None else {"FrameInfo": {"function": "UNP" if state else "OFF"}}
        escaped = json.dumps(payload).replace('"', r'\"')
        ns = round(float(timestamp) * 1_000_000_000)
        chunks.append(f"{1000000000000000000 + i} seq: {i}\n"
                      f"stamp:\n  secs: {ns // 1000000000}\n  nsecs: {ns % 1000000000}\n"
                      + 'frame_id: "' + escaped + '"\n')
    (folder / "_unp_planning_info.txt").write_text("".join(chunks), encoding="utf-8")
    parser._UNP_PARSE_CACHE.clear()

def write_h5(path, times, signals):
    by_message = {}
    for name, value in signals.items():
        message, key = name.split(".", 1)
        by_message.setdefault(message, {})[key] = value
    with h5py.File(path, "w") as h5:
        channel = h5.create_group("sample").create_group("Channel1")
        for message, fields in by_message.items():
            data = np.zeros(len(times), dtype=[("timestamp", "f8")] + [(key, "f8") for key in fields])
            data["timestamp"] = times
            for key, values in fields.items():
                data[key] = values
            channel.create_dataset(message, data=data)

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)
    folder = work / "20261109-120000_done"
    folder.mkdir()
    peer = work / "20261109-120001_done"
    peer.mkdir()
    t = np.round(np.arange(100., 110.01, .1), 6)
    lost = importlib.import_module("metrics.can.code.acc_lost_arbitration")
    tcs = importlib.import_module("metrics.can.code.tcs_activation_count")
    signals = {
        lost.ACC_ACTIVE_SIGNAL: np.ones(t.size),
        lost.REQUEST_STATE_SIGNAL: np.full(t.size, 2.),
        lost.PEDAL_OVERRIDE_SIGNAL: np.zeros(t.size),
        tcs.TCS_ACTIVE_SIGNAL: np.where((t >= 102.) & (t <= 103.), 1., 0.),
    }
    h5path = folder / "_msd_can_data.h5"
    write_h5(h5path, t, signals)
    names = ("acc_lost_arbitration", "tcs_activation_count")
    modules = [importlib.import_module("metrics.can.code." + name) for name in names]
    metrics = [MetricModule("can", "can", name, name + ".py", Path(module.__file__),
                            module, module.__run_haoran_data_run__, "MPI")
               for name, module in zip(names, modules)]

    def run(label, next_folder=None):
        out = work / label
        stamp = "2026-11-09_16-34-04"
        pipeline = PipelineConfig("can", "can", True, Path.cwd() / "metrics" / "can" / "code",
                                  out, "PHEV", preprocess_signals=list(signals))
        state = RunState(out / "state.json", {"tasks": {}, "run_timestamp": stamp})
        process_can_folder(pipeline, metrics, str(folder), 1, 1, stamp,
                           next_folder_path=next_folder, run_state=state)
        rows = {}
        for metric in metrics:
            with (out / f"{metric.output_prefix}_{stamp}.csv").open(encoding="utf-8-sig") as file:
                rows[metric.name] = list(csv.DictReader(file))[0]
        return rows, state, out

    # Topic pipeline need not be enabled: the CAN pipeline reads its UNP dependency directly.
    write_unp(folder, t, [True] * len(t))
    rows, state, out = run("on")
    assert rows["acc_lost_arbitration"]["Bad Case"] == "1", rows
    assert all(task["status"] == TASK_STATUS_SUCCESS for task in state.tasks.values())
    write_unp(folder, t, [False] * len(t))
    rows, state, out = run("off")
    assert rows["acc_lost_arbitration"]["Bad Case"] == "0"
    assert rows["tcs_activation_count"]["Bad Case"] == "1"  # No ACC requirement.
    write_unp(folder, t, [True] * len(t))
    signals[lost.ACC_ACTIVE_SIGNAL] = np.zeros(t.size)
    write_h5(h5path, t, signals)
    rows, state, out = run("can_off")
    assert rows["acc_lost_arbitration"]["Bad Case"] == "0"
    signals[lost.ACC_ACTIVE_SIGNAL] = np.ones(t.size)
    write_h5(h5path, t, signals)
    # A short UNP-off interval splits CAN scenes; it cannot be bridged to satisfy the 5 s dwell.
    write_unp(folder, t, [not (104.9 <= value <= 105.1) for value in t])
    rows, state, out = run("split")
    assert rows["acc_lost_arbitration"]["Bad Case"] == "0"
    write_unp(folder, t, [False] * len(t))
    # Combined gate never turns an originally inactive CAN flag on.
    session = FolderSession(h5path)
    session.unp_active_gate = UnpActiveGate([str(folder)])
    axis, raw = session.get_signal_slice(lost.ACC_ACTIVE_SIGNAL, 100., 110.)
    assert all(value == 1 for value in raw)
    assert not any(session.sample_signal_to_axis(lost.ACC_ACTIVE_SIGNAL, axis))
    session.unp_active_gate = None
    assert all(session.sample_signal_to_axis(lost.ACC_ACTIVE_SIGNAL, axis))  # Cache stores original CAN.
    session.unp_active_gate = UnpActiveGate([str(folder)])
    assert not any(session.sample_signal_to_axis(lost.ACC_ACTIVE_SIGNAL, axis))

    # UNP holes, missing flags/files and stale timestamps skip every ACC-related metric in the folder.
    for label, times, states in (
        ("gap", t[(t < 104.) | (t > 106.)], [True] * np.count_nonzero((t < 104.) | (t > 106.))),
        ("unknown", t, [None if index == 20 else True for index in range(len(t))]),
        ("stale", t + 20., [True] * len(t)),
    ):
        write_unp(folder, times, states)
        rows, state, out = run(label)
        assert all("未统计：Topic UNP" in rows[name]["符合要求的内容"]
                   for name in ("acc_lost_arbitration",)), rows
        assert rows["acc_lost_arbitration"]["检查结果"] == "未统计"
        assert rows["tcs_activation_count"]["Bad Case"] == "1"
        assert sum(task["status"] == TASK_STATUS_SKIPPED_UNP for task in state.tasks.values()) == 1
        report = generate_unified_report(out, "report", "2026-11-09_16-34-04",
                                        {metric.output_prefix: "MPI" for metric in metrics})
        wb = load_workbook(report)
        try:
            header = {cell.value: cell.column for cell in wb.active[1]}
            row = next(row for row in wb.active.iter_rows(min_row=2)
                       if row[header["指标名称"] - 1].value == "acc_lost_arbitration")
            assert row[header["MPI"] - 1].value == "未统计"
            assert "未统计" in row[header["统计状态"] - 1].value
        finally:
            wb.close()
    (folder / "_unp_planning_info.txt").unlink()
    rows, state, out = run("missing")
    assert "缺少 Topic" in rows["acc_lost_arbitration"]["符合要求的内容"]
    assert rows["acc_lost_arbitration"]["段里程(km)"] == "0.0"
    assert rows["tcs_activation_count"]["Bad Case"] == "1"

    mixed = work / "mixed"
    mixed.mkdir()
    stamp = "2026-11-09_16-34-04"
    with (work / "on" / f"can__acc_lost_arbitration_{stamp}.csv").open(encoding="utf-8-sig") as file:
        included = next(csv.DictReader(file))
    with (mixed / f"can__acc_lost_arbitration_{stamp}.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(included))
        writer.writeheader()
        writer.writerows([included, rows["acc_lost_arbitration"]])
    report = generate_unified_report(mixed, "report", stamp, {"can__acc_lost_arbitration": "MPI"})
    wb = load_workbook(report)
    try:
        header = {cell.value: cell.column for cell in wb.active[1]}
        assert "部分未统计" in wb.active.cell(2, header["统计状态"]).value
        assert wb.active.cell(2, header["MPI"]).value != "未统计"
        assert wb.active.cell(2, header["统计状态"]).comment is not None
    finally:
        wb.close()

    # Same 300 ms nearest-frame tolerance as the existing CAN/Topic scene logic.
    write_unp(folder, [100.], [True])
    gate = UnpActiveGate([str(folder)])
    assert gate.states([100.3]) == [True]
    try:
        gate.states([100.30001])
    except UnpAlignmentError:
        pass
    else:
        raise AssertionError("out-of-tolerance frame must be rejected")
    for invalid in ("", " ", None, True, 1, [], {}):
        write_unp(folder, [100.], [True])
        path = folder / "_unp_planning_info.txt"
        text = path.read_text(encoding="utf-8").replace(
            r'\"UNP\"', json.dumps(invalid).replace('"', r'\"'))
        path.write_text(text, encoding="utf-8")
        parser._UNP_PARSE_CACHE.clear()
        try:
            UnpActiveGate([str(folder)]).states([100.])
        except UnpAlignmentError:
            pass
        else:
            raise AssertionError(f"invalid function must not be interpreted as off: {invalid!r}")
    write_unp(folder, [100., 100.], [True, False])
    try:
        UnpActiveGate([str(folder)])
    except UnpAlignmentError:
        pass
    else:
        raise AssertionError("conflicting duplicate timestamps must be rejected")
    write_unp(folder, t[:51], [True] * 51)
    write_unp(peer, t[51:], [False] * (len(t) - 51))
    gate = UnpActiveGate([str(folder), str(peer)])
    assert gate.states([100., 110.]) == [True, False]
    # Actual cross-H5 continuation requires the peer's Topic state too.
    peer_times = np.round(np.arange(110.1, 112.01, .1), 6)
    write_h5(peer / "_msd_can_data.h5", peer_times,
             {name: np.full(len(peer_times), values[-1]) for name, values in signals.items()})
    write_unp(folder, t, [True] * len(t))
    write_unp(peer, peer_times, [False] * len(peer_times))
    rows, state, out = run("continued", str(peer))
    assert rows["acc_lost_arbitration"]["Bad Case"] == "1"
    (peer / "_unp_planning_info.txt").unlink()
    rows, state, out = run("peer_missing", str(peer))
    assert rows["acc_lost_arbitration"]["检查结果"] == "未统计"
    assert "缺少 Topic" in rows["acc_lost_arbitration"]["符合要求的内容"]

    # Signal plots expose the matched UNP prerequisite independently of raw CAN.
    write_unp(folder, t, [True if value < 104. else False for value in t])
    session = FolderSession(h5path)
    session.unp_active_gate = UnpActiveGate([str(folder)])
    from bad_case_signal_plots import can_series
    plotted, _missing = can_series(session, lost.REQUIRED_SIGNALS, 100.,
                                  unp_gate=session.unp_active_gate)
    unp_series = next(item for item in plotted if item.label == "Topic UNP on")
    assert unp_series.values[0] == 1 and unp_series.values[-1] == 0
    # All remaining ACC-required metrics are covered; chassis-only metrics are not.
    affected = []
    for path in (Path.cwd() / "metrics" / "can" / "code").glob("*.py"):
        module = importlib.import_module("metrics.can.code." + path.stem)
        if requires_unp(module):
            affected.append(path.stem)
    assert len(affected) == 10, affected
    assert all(name.startswith("acc_") for name in affected), affected
    assert not requires_unp(tcs)
'''],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
