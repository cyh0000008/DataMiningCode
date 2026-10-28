import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtWidgets import QApplication

from UnifiedApp.unified_app.job_manager import Job, JobManager, STATUS_SUCCESS
from UnifiedApp.unified_app.job_spec import CompareJobSpec
from UnifiedApp.unified_app.progress_parser import parse_line
from UnifiedApp.unified_app.ui.job_panel import JobPanel, COL_SYNC, COL_SYNC_PLOT


ROOT = Path(__file__).resolve().parents[2]
J6B = ROOT / "J6B_UnifiedMining"


class ActivationSyncTests(unittest.TestCase):
    def test_wrapped_layout_targeted_fallback_and_read_failures(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from pathlib import Path
import tempfile
from unittest.mock import patch
import h5py
import numpy as np
import activation_sync_check as sync

with tempfile.TemporaryDirectory() as folder:
    work = Path(folder)
    wrapped = work / "wrapped.h5"
    with h5py.File(wrapped, "w") as h5:
        for path, offset in ((sync.BUS_SIGNAL_CANDIDATES[0], 0.),
                             (sync.ETHERNET_SIGNAL_CANDIDATES[0], .3)):
            path = "recording/" + path
            h5.create_dataset(path, data=[0, 1, 1, 0])
            h5.create_dataset(sync.timestamp_path_for_signal(path),
                              data=np.array([0., 1., 2., 3.]) + offset)
    with patch.object(sync, "discover_signal_paths", side_effect=AssertionError("wrapped exact path must not scan")):
        data = sync.read_file_signals(wrapped)
        assert data.bus_signal_path.startswith("recording/ch3/")
        assert data.ethernet_signal_path.startswith("recording/ch4/")

    fallback = work / "alternate_version.h5"
    with h5py.File(fallback, "w") as h5:
        for msg, signal, lag in (
            ("0x194_ACC_AxlTrqReq_0x194", "ILCCC_Act", 0.),
            ("0xA84_DAebMPart1Obj21", "LCCC_Act", -.3),
        ):
            prefix = "recording/ch9/Network/NewDBC/" + msg
            h5.create_dataset(prefix + "/signals/" + signal, data=[0, 1, 1, 0])
            h5.create_dataset(prefix + "/timestamp", data=np.array([0., 1., 2., 3.]) + lag)
        for i in range(1000):
            h5.create_dataset(f"recording/ch9/Network/NewDBC/Unrelated_{i}/signals/other", data=[0])
        h5.create_dataset("recording/ExpensiveTopic/subtree/data", data=[0])
    original_getitem = h5py.Group.__getitem__
    def guarded_getitem(group, name):
        assert "Unrelated_" not in str(name) and "ExpensiveTopic" not in str(name), (group.name, name)
        return original_getitem(group, name)
    with patch.object(h5py.Group, "__getitem__", guarded_getitem), \
         patch.object(h5py.Group, "visititems", side_effect=AssertionError("full scan")):
        data = sync.read_file_signals(fallback)
        assert data.ethernet_signal_path.endswith("/signals/LCCC_Act")
        assert data.bus_value.tolist() == [0, 1, 1, 0]

    corrupt = work / "unreadable.h5"
    reader = sync.read_file_signals
    def read(path):
        if path == corrupt:
            raise RuntimeError("Unable to synchronously check link existence (free block size is zero?)")
        return reader(path)
    with patch.object(sync, "read_file_signals", side_effect=read):
        result = sync.write_mining_sync_report([wrapped, corrupt, fallback], work)
    assert result["failed_files"] == 1
    assert result["matched_edges"] == 4
    assert result["has_large_delay"] is True
    assert result["read_errors"][0]["file_path"] == str(corrupt)
    assert "不完整" in result["detail"]
    assert Path(result["plot_path"]).is_file()
    with patch.object(sync, "read_file_signals", side_effect=read):
        result = sync.write_mining_sync_report([corrupt], work)
    assert result["failed_files"] == 1 and result["matched_edges"] == 0
    assert result["max_abs_lag_ms"] is None
    assert "不完整" in result["detail"]
'''],
            cwd=J6B, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scheduling_without_comparison_and_failure_states(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            for compare_status in (None, "失败", "已取消"):
                manager = JobManager()
                source = Job(
                    "source", "j6b", "mining", [sys.executable], J6B,
                    report_path=Path(folder) / "report.xlsx", pending_sync=True,
                )
                manager._jobs[source.id] = source
                with patch.object(manager, "_try_start_next"):
                    if compare_status is None:
                        manager._on_finished(source.id, 0, None)
                    else:
                        compare = Job(
                            "compare", "compare", "compare", [], J6B,
                            status=compare_status, sync_source_job_id=source.id,
                        )
                        manager._finish_sync_dependency(compare)
                    children = [job for job in manager.jobs() if job.kind == "lcc_sync"]
                    if compare_status == "已取消":
                        self.assertEqual(children, [])
                        self.assertEqual(source.sync_status, "未完成")
                        continue
                    self.assertEqual(len(children), 1)
                    self.assertFalse(source.pending_sync)
                    self.assertIn("--mining-manifest", children[0].command)
                    self.assertEqual(source.sync_status, "排队中")
                    manager.cancel(children[0].id)
                    self.assertEqual(source.sync_status, "未完成")
                    self.assertIsNone(source.sync_plot_path)
            manager = JobManager()
            source = Job("source", "j6b", "mining", [], J6B, status="成功")
            child = Job("sync", "lcc_sync", "sync", [], J6B,
                        status="失败", sync_source_job_id=source.id)
            manager._jobs = {source.id: source, child.id: child}
            manager._finish_sync_dependency(child)
            self.assertEqual(source.status, "成功")
            self.assertEqual(source.sync_status, "检查失败")
            self.assertEqual(child.sync_status, "检查失败")

    def test_discovery_skips_unrelated_datasets_and_bounds_reads(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
from pathlib import Path
import tempfile
from unittest.mock import patch
import h5py
import numpy as np
import activation_sync_check as sync

class Dataset:
    def __getitem__(self, selection):
        assert selection == slice(None, 50000), selection
        return np.array([0, 1])

assert sync.signal_value_score(Dataset(), ({0, 1},)) == 0
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "alternate_channels.h5"
    with h5py.File(path, "w") as h5:
        for i in range(50):
            group = h5.create_group(f"ch8/ETH/DBC/0x{i:X}_Other/signals")
            for j in range(20):
                group.create_dataset(f"unused_{j}", data=[0, 1])
        for message, signal in (
            ("0x194_ACC_AxlTrqReq", "ILCCC_Act"),
            ("0x3D9_Reserved_10", "Debug_uint_Reserved_11"),
        ):
            for channel, values in (("ch8", [8, 8, 8, 8]), ("ch9", [0, 1, 1, 0])):
                group = h5.create_group(f"{channel}/Network/DBC/{message}")
                group.create_dataset("timestamp", data=[0., 1., 2., 3.])
                group.create_dataset("signals/" + signal, data=values)
    with h5py.File(path, "r") as h5:
        expected = []
        h5.visititems(lambda name, obj: expected.append(name) if (
            isinstance(obj, h5py.Dataset) and
            name.endswith(("/signals/ILCCC_Act", "/signals/Debug_uint_Reserved_11"))
        ) else None)
        assert set(sync.discover_signal_paths(h5, ("ILCCC_Act", "Debug_uint_Reserved_11"))) == set(expected)
    original_getitem = h5py.Group.__getitem__
    def checked_getitem(group, name):
        assert "unused_" not in str(name), name
        return original_getitem(group, name)
    with patch.object(h5py.Group, "visititems", side_effect=AssertionError("full tree traversal")), \
         patch.object(h5py.Group, "__getitem__", checked_getitem), \
         patch.object(sync, "discover_signal_paths", wraps=sync.discover_signal_paths) as discover:
        result = sync.read_file_signals(path)
        assert discover.call_count == 1
        assert result.bus_value.tolist() == [0, 1, 1, 0]
        assert result.ethernet_raw_value.tolist() == [0, 1, 1, 0]
    with patch.object(sync, "signal_value_score", side_effect=AssertionError("redundant scoring")):
        with h5py.File(path, "r") as h5:
            assert sync.choose_preferred_signal_path(
                h5, ["ch9/Network/DBC/0x194_ACC_AxlTrqReq/signals/ILCCC_Act"], ({0, 1},),
            ).startswith("ch9/")
'''],
            cwd=J6B, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_files_thresholds_and_no_samples(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
import json
from pathlib import Path
import tempfile
import h5py
import numpy as np
from PIL import Image
from activation_sync_check import (
    BUS_SIGNAL_CANDIDATES, ETHERNET_SIGNAL_CANDIDATES, write_mining_sync_report,
    EdgeMatch, exceeds_delay_threshold,
)

for lag, expected in ((.2, False), (-.2, False), (.201, True), (-.201, True)):
    bus = 1_700_000_000.
    eth = bus + lag
    assert exceeds_delay_threshold(EdgeMatch(Path("test.h5"), None, bus, eth, 1, eth - bus)) is expected

def create(path, lag=None, constant=False, legacy=False):
    with h5py.File(path, "w") as h5:
        if lag is None:
            h5.create_group("metadata")
            return
        for signal_path, offset in (
            (BUS_SIGNAL_CANDIDATES[0], 0.),
            (ETHERNET_SIGNAL_CANDIDATES[0], lag),
        ):
            prefix = signal_path.partition("/signals/")[0]
            h5.create_dataset(prefix + "/timestamp", data=np.array([0., 1., 2., 3.]) + offset)
            values = [0, 0, 0, 0] if constant else [0, 1, 1, 0]
            if offset == lag and signal_path == ETHERNET_SIGNAL_CANDIDATES[0] and legacy:
                values = [4 if value else 5 for value in values]
            h5.create_dataset(signal_path, data=values)

with tempfile.TemporaryDirectory() as folder:
    work = Path(folder)
    selected, ignored = work / "selected.h5", work / "ignored.h5"
    create(ignored, 1.)
    for lag, expected in ((0., False), (.199, False), (.2, False),
                          (.201, True), (-.201, True), (-.2, False)):
        create(selected, lag, legacy=True)
        result = write_mining_sync_report([selected], work)
        assert result["has_large_delay"] is expected, (lag, result)
        assert result["matched_edges"] == 2, result
        assert abs(result["max_abs_lag_ms"] - abs(lag * 1000)) < 1e-7
        assert all(item["file_path"] == str(selected) for item in result["matches"])
        persisted = json.loads((work / "lcc_activation_sync.json").read_text(encoding="utf-8"))
        assert persisted == result
        with Image.open(result["plot_path"]) as image:
            assert image.size == (2400, 1500)
            image.verify()
    create(selected, 0., constant=True)
    result = write_mining_sync_report([selected], work)
    assert result["has_large_delay"] is False and result["matched_edges"] == 0
    assert "无有效样本" in result["detail"] and "边沿" in result["detail"]
    create(selected)
    result = write_mining_sync_report([selected], work)
    assert result["has_large_delay"] is False and result["max_abs_lag_ms"] is None
    assert "缺少" in result["detail"] and "跳过 1" in result["detail"]
'''],
            cwd=J6B, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fragmented_stdout_and_link(self):
        app = QApplication.instance() or QApplication([])
        manager = JobManager()
        panel = JobPanel(manager)
        with tempfile.TemporaryDirectory() as folder:
            plot = Path(folder) / "结果 图.png"
            plot.touch()
            job = Job("synthetic", "j6b", "test", [], J6B)
            manager._jobs[job.id] = job
            panel._on_job_added(job.id)

            class ProcessOutput:
                chunk = b""

                def readAllStandardOutput(self):
                    return self.chunk

            process = ProcessOutput()
            job.process = process
            payload = json.dumps({
                "has_large_delay": True, "matched_edges": 2,
                "detail": "test", "plot_path": str(plot),
            }, ensure_ascii=False)
            encoded = ("LCC_SYNC_RESULT: " + payload + "\n").encode()
            split_at = encoded.index("结".encode()) + 1
            process.chunk = encoded[:split_at]
            manager._on_output(job.id)
            self.assertEqual(job.sync_status, "")
            process.chunk = encoded[split_at:]
            manager._on_output(job.id)
            self.assertEqual(panel.table.item(0, COL_SYNC).text(), "是")
            link = panel.table.cellWidget(0, COL_SYNC_PLOT)
            self.assertIn("打开结果图", link.text())
            with patch("UnifiedApp.unified_app.ui.job_panel.QDesktopServices.openUrl", return_value=True) as open_url:
                link.linkActivated.emit("sync-plot")
                open_url.assert_called_once_with(QUrl.fromLocalFile(str(plot)))
            payload = json.dumps({
                "has_large_delay": False, "matched_edges": 0,
                "detail": "无有效样本：缺少信号", "plot_path": str(plot),
            })
            process.chunk = ("LCC_SYNC_RESULT: " + payload).encode()
            manager._on_output(job.id, final=True)
            self.assertEqual(job.sync_status, "否（无有效样本）")
            self.assertIn("缺少信号", panel.table.item(0, COL_SYNC).toolTip())
            for large, expected in ((True, "是（结果不完整）"), (False, "检查不完整")):
                payload = json.dumps({
                    "has_large_delay": large, "matched_edges": 2 if large else 0,
                    "failed_files": 1, "detail": "读取失败，结果不完整", "plot_path": str(plot),
                })
                process.chunk = ("LCC_SYNC_RESULT: " + payload + "\n").encode()
                manager._on_output(job.id)
                self.assertEqual(job.sync_status, expected)
                self.assertEqual(panel.table.item(0, COL_SYNC).text(), expected)
            process.chunk = b"LCC_SYNC_RESULT: invalid\n"
            manager._on_output(job.id)
            self.assertEqual(job.sync_status, "检查失败")
        panel.close()
        with self.assertRaises(ValueError):
            parse_line("j6b", 'LCC_SYNC_RESULT: {"has_large_delay": "false"}')
        self.assertIsNone(parse_line("mmt", "LCC_SYNC_RESULT: invalid").sync_has_large_delay)

    def test_real_mining_job_to_queue(self):
        import h5py
        import numpy as np

        app = QApplication.instance() or QApplication([])
        manager = JobManager()
        panel = JobPanel(manager)
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            source = work / "synthetic.h5"
            with h5py.File(source, "w") as h5:
                h5.create_group("metadata")
                h5.create_dataset("sample/timestamp", data=np.arange(0., 5., .1))
                h5.create_dataset("sample/signals/acc_active", data=np.ones(50))
                h5.create_dataset("sample/signals/abs_active", data=np.zeros(50))
                h5.create_dataset("sample/signals/acceleration", data=np.zeros(50))
                for prefix, lag in (
                    ("ch3/CAN_PB/SGM_C1UL_PB_CAN_0227/0x194_ACC_AxlTrqReq_0x194", 0.),
                    ("ch4/Eth_Debug_CAN5/PATAC_Acore_Debug_Msg_v0p1p6_Release_0709/0xA84_DAebMPart1Obj21", .3),
                ):
                    signal = "ILCCC_Act" if lag == 0 else "LCCC_Act"
                    h5.create_dataset(prefix + "/timestamp", data=np.array([0., 1., 2., 3.]) + lag)
                    h5.create_dataset(prefix + "/signals/" + signal, data=[0, 1, 1, 0])
            config = {
                "input_path": [str(work)], "powertrain_type": "ICE",
                "signals": {"acc_active": "sample/signals/acc_active",
                            "abs_active": "sample/signals/abs_active",
                            "acceleration": "sample/signals/acceleration"},
                "enabled_metrics": ["acc_exit_after_abs"], "output_dir": str(work / "run"),
                "lcc_sync_check": True, "lcc_sync_deferred": True,
            }
            cfg = work / "config.json"
            cfg.write_text(json.dumps(config), encoding="utf-8")
            loop = QEventLoop()
            manager.job_finished.connect(
                lambda jid: loop.quit() if manager.get_job(jid).kind == "lcc_sync" else None
            )
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(60000)
            compare_dir = work / "compare"
            compare_dir.mkdir()
            (compare_dir / "mpi_cpi_compare.py").write_text(
                "from pathlib import Path\nimport sys\n"
                "target = Path(sys.argv[sys.argv.index('--target') + 1])\n"
                "assert target.is_file()\n"
                "assert not (target.parent / 'lcc_activation_sync.png').exists()\n"
                "(target.parent / 'comparison_finished.txt').write_text('done')\n",
                encoding="utf-8",
            )
            job_id = manager._submit(
                "j6b", "real mining", [sys.executable, str(J6B / "DataMining.py"), str(cfg)],
                J6B, [], pending_compare={
                    "python_exe": sys.executable, "compare_dir": compare_dir,
                    "spec": CompareJobSpec(report_profile="J6B"),
                },
                report_archive={"archive_dir": "", "vehicle": "TEST", "date": "20261028", "version": "V1"},
                pending_sync=True,
            )
            loop.exec()
            timer.stop()
            job = manager.get_job(job_id)
            try:
                self.assertEqual(job.status, STATUS_SUCCESS, "\n".join(job.log_lines))
                self.assertEqual(job.sync_status, "是")
                self.assertTrue(job.sync_plot_path.is_file())
                self.assertTrue(job.report_path.is_file())
                self.assertEqual(job.sync_plot_path.parent, job.report_path.parent)
                self.assertEqual(panel.table.item(0, COL_SYNC).text(), "是")
                jobs = manager.jobs()
                self.assertEqual([item.kind for item in jobs], ["j6b", "compare", "lcc_sync"])
                self.assertEqual(jobs[1].status, STATUS_SUCCESS, "\n".join(jobs[1].log_lines))
                self.assertGreaterEqual(jobs[2].started_at, jobs[1].finished_at)
                self.assertTrue((job.report_path.parent / "comparison_finished.txt").is_file())
                # A disabled run still produces its normal report but no diagnostic.
                config["lcc_sync_check"] = False
                config["lcc_sync_deferred"] = False
                config["output_dir"] = str(work / "disabled")
                cfg.write_text(json.dumps(config), encoding="utf-8")
                disabled = subprocess.run(
                    [sys.executable, str(J6B / "DataMining.py"), str(cfg)], cwd=J6B,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, encoding="utf-8", timeout=30,
                )
                self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
                self.assertIn("LCC sync check disabled.", disabled.stdout)
                self.assertIn("Report written:", disabled.stdout)
                self.assertFalse(list((work / "disabled").rglob("lcc_activation_sync.*")))
            finally:
                if job.process is not None and job.process.state() != job.process.ProcessState.NotRunning:
                    job.process.kill()
                    job.process.waitForFinished(5000)
        panel.close()


if __name__ == "__main__":
    unittest.main()
