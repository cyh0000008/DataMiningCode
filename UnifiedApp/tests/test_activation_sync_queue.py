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
from UnifiedApp.unified_app.progress_parser import parse_line
from UnifiedApp.unified_app.ui.job_panel import JobPanel, COL_SYNC, COL_SYNC_PLOT


ROOT = Path(__file__).resolve().parents[2]
J6B = ROOT / "J6B_UnifiedMining"


class ActivationSyncTests(unittest.TestCase):
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
            }
            cfg = work / "config.json"
            cfg.write_text(json.dumps(config), encoding="utf-8")
            loop = QEventLoop()
            manager.job_finished.connect(loop.quit)
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(60000)
            job_id = manager._submit(
                "j6b", "real mining", [sys.executable, str(J6B / "DataMining.py"), str(cfg)],
                J6B, [],
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
            finally:
                if job.process is not None and job.process.state() != job.process.ProcessState.NotRunning:
                    job.process.kill()
                    job.process.waitForFinished(5000)
        panel.close()


if __name__ == "__main__":
    unittest.main()
