import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from UnifiedApp.unified_app.job_manager import Job, JobManager, STATUS_SUCCESS
from UnifiedApp.unified_app.job_spec import J6BJobSpec, MMTJobSpec
from UnifiedApp.unified_app.progress_parser import parse_line
from UnifiedApp.unified_app.report_archive import bundle_reports
from UnifiedApp.unified_app.ui.job_panel import JobPanel, COL_STATUS


MINING_SCRIPT = '''
import json
from pathlib import Path
import sys
from openpyxl import Workbook
config = json.loads(Path(sys.argv[-1] if "--resume" not in sys.argv else sys.argv[2]).read_text(encoding="utf-8"))
output = Path(config["output_dir"]) / "current_run"
output.mkdir(parents=True, exist_ok=True)
report = output / "summary.xlsx"
bad = output / "bad_case_report.xlsx"
for path in (report, bad):
    wb = Workbook()
    wb.active.append([path.stem])
    wb.save(path)
(output / "case.png").write_bytes(b"not included")
if "source_path" in config:
    print("Bad Case 提取报告：" + str(bad), flush=True)
    print("统计报告已生成：" + str(report), flush=True)
else:
    print("Bad case report written: " + str(bad), flush=True)
    print("Report written: " + str(report), flush=True)
'''

COMPARE_SCRIPT = '''
from pathlib import Path
import sys
from openpyxl import Workbook
target = Path(sys.argv[sys.argv.index("--target") + 1])
assert not list((target.parent.parent / "selected output").glob("*.zip"))
output = target.parent / "comparison.xlsx"
wb = Workbook()
wb.active.append(["comparison"])
wb.save(output)
print("已生成：" + str(output), flush=True)
'''


class ReportBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_j6b_and_mmt_processes_exact_current_reports_and_output(self):
        for kind in ("j6b", "mmt"):
            for auto_compare in (True, False):
                with self.subTest(kind=kind, auto_compare=auto_compare), tempfile.TemporaryDirectory() as folder:
                    work = Path(folder)
                    engine = work / "engine"
                    engine.mkdir()
                    (engine / "DataMining.py").write_text(MINING_SCRIPT, encoding="utf-8")
                    (engine / "mpi_cpi_compare.py").write_text(COMPARE_SCRIPT, encoding="utf-8")
                    output = work / "selected output"
                    output.mkdir()
                    (output / "stale_bad_case_report.xlsx").write_bytes(b"old")
                    archive = work / "extra archive"
                    template = engine / "config.json"
                    template.write_text(json.dumps({
                        "input_path": [str(work)], "source_path": str(work),
                        "output_dir": str(output), "pipelines": [{"name": "can", "enabled": True}],
                    } if kind == "mmt" else {
                        "input_path": [str(work)], "output_dir": str(output),
                    }), encoding="utf-8")
                    common = dict(
                        vehicle="测试车型", software_version="V2.3", auto_compare=auto_compare,
                        raw_report_archive_dir=str(archive),
                    )
                    manager = JobManager(max_concurrent=1)
                    if kind == "j6b":
                        spec = J6BJobSpec(
                            **common, input_paths=[str(work)], date_range="20261028",
                            output_dir=str(output), lcc_sync_check=False,
                        )
                        job_id = manager.submit_j6b(
                            sys.executable, engine, template, spec, sys.executable, engine,
                        )
                    else:
                        # Empty selection resolves the template's output directory.
                        spec = MMTJobSpec(**common, source_path=str(work), query_date="20261028")
                        job_id = manager.submit_mmt(
                            sys.executable, engine, template, spec, sys.executable, engine,
                        )
                    loop = QEventLoop()
                    timer = QTimer()
                    timer.timeout.connect(
                        lambda: loop.quit() if manager.get_job(job_id).bundle_path
                        or manager.get_job(job_id).bundle_error else None
                    )
                    timer.start(10)
                    QTimer.singleShot(20000, loop.quit)
                    loop.exec()
                    timer.stop()
                    source = manager.get_job(job_id)
                    self.assertIsNotNone(source.bundle_path, "\n".join(
                        line for job in manager.jobs() for line in job.log_lines
                    ))
                    self.assertEqual(source.status, STATUS_SUCCESS)
                    self.assertEqual(source.bundle_path.parent, output)
                    self.assertRegex(source.bundle_path.name, r"^测试车型_V2\.3_\d{8}_\d{6}\.zip$")
                    expected = {"bad_case_report.xlsx"}
                    if auto_compare:
                        expected.add("comparison.xlsx")
                    with ZipFile(source.bundle_path) as bundle:
                        self.assertEqual(set(bundle.namelist()), expected)
                        self.assertIsNone(bundle.testzip())
                        for name in expected:
                            self.assertEqual(bundle.read(name), (
                                archive / name if name == "comparison.xlsx"
                                else output / "current_run" / name
                            ).read_bytes())
                    self.assertEqual(len(list(output.glob("*.zip"))), 1)
                    self.assertEqual(list(archive.glob("*.zip")), [])
                    panel = JobPanel(manager)
                    panel._on_job_added(job_id)
                    panel._on_job_updated(job_id)
                    row = panel._row_by_job[job_id]
                    self.assertEqual(panel.table.item(row, COL_STATUS).toolTip(), str(source.bundle_path))
                    panel.deleteLater()

    def test_collision_sanitization_and_failed_zip_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            report = output / "bad.xlsx"
            report.write_bytes(b"report")
            first = bundle_reports([report], output, "car:/", "V1? ")
            with patch("UnifiedApp.unified_app.report_archive.datetime") as clock:
                clock.now.return_value.strftime.return_value = first.stem.split("_")[-2] + "_" + first.stem.split("_")[-1]
                second = bundle_reports([report], output, "car:/", "V1? ")
            self.assertNotEqual(first, second)
            self.assertTrue(second.stem.endswith("(2)"))
            self.assertNotIn("?", first.name)
            before = set(output.glob("*.zip"))
            with patch.object(ZipFile, "write", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    bundle_reports([report], output, "car", "V1")
            self.assertEqual(set(output.glob("*.zip")), before)
            with self.assertRaises(FileNotFoundError):
                bundle_reports([output / "missing.xlsx"], output, "car", "V1")
            with self.assertRaises(ValueError):
                bundle_reports([], output, "car", "V1")

    def test_failure_cancel_missing_reports_and_pack_before_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            bad = work / "bad.xlsx"
            comparison_path = work / "comparison.xlsx"
            bad.write_bytes(b"bad")
            comparison_path.write_bytes(b"comparison")
            for status in ("失败", "已取消", STATUS_SUCCESS):
                manager = JobManager()
                source = Job(
                    "source", "mmt", "source", [], work,
                    bad_case_report_path=bad,
                    bundle_options={"output_dir": str(work), "vehicle": "car", "version": "V1"},
                )
                manager._jobs[source.id] = source
                comparison = Job(
                    "comparison", "compare", "comparison", [], work, status=status,
                    sync_source_job_id=source.id,
                )
                manager._finish_sync_dependency(comparison)
                self.assertIsNone(source.bundle_path)
                self.assertTrue(source.bundle_error)
                panel = JobPanel(manager)
                panel._on_job_added(source.id)
                panel._on_job_updated(source.id)
                row = panel._row_by_job[source.id]
                self.assertIn("打包失败", panel.table.item(row, COL_STATUS).text())
                panel.deleteLater()
            comparison.status = STATUS_SUCCESS
            comparison.report_path = comparison_path
            source.pending_sync = True
            with patch.object(manager, "_submit_sync") as sync:
                sync.side_effect = lambda job: self.assertTrue(job.bundle_path.is_file())
                manager._finish_sync_dependency(comparison)
                sync.assert_called_once_with(source)
            source.bundle_path = None
            source.bad_case_report_path = work / "missing.xlsx"
            manager._bundle_reports(source, comparison)
            self.assertIsNone(source.bundle_path)
            self.assertIn("不存在", source.bundle_error)
            source.bad_case_report_path = None
            manager._bundle_reports(source, comparison)
            with ZipFile(source.bundle_path) as bundle:
                self.assertEqual(bundle.namelist(), ["comparison.xlsx"])

    def test_report_stdout_protocols(self):
        self.assertEqual(
            parse_line("compare", "已生成：C:\\输出\\compare.xlsx").report_path,
            "C:\\输出\\compare.xlsx",
        )
        for kind, prefix in (("j6b", "Bad case report written: "), ("mmt", "Bad Case 提取报告：")):
            update = parse_line(kind, prefix + "C:\\输出\\bad_case_report.xlsx")
            self.assertEqual(update.bad_case_report_path, "C:\\输出\\bad_case_report.xlsx")
            self.assertIsNone(update.report_path)

    def test_dry_run_and_failed_compare_submission_do_not_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            template = work / "config.json"
            template.write_text(json.dumps({
                "source_path": str(work), "output_dir": str(work),
                "pipelines": [{"name": "can", "enabled": True}],
            }), encoding="utf-8")
            manager = JobManager()
            with patch.object(manager, "_try_start_next"):
                job_id = manager.submit_mmt(
                    sys.executable, work, template,
                    MMTJobSpec(dry_run=True, auto_compare=True), sys.executable, work,
                )
            job = manager.get_job(job_id)
            self.assertIsNone(job.bundle_options)
            self.assertIsNone(job.pending_compare)
            manager._cleanup_temp_files(job)
            job.pending_compare = {"spec": object()}
            job.bundle_options = {"output_dir": str(work), "vehicle": "car", "version": "V1"}
            with patch.object(manager, "_trigger_auto_compare", return_value=None), \
                 patch.object(manager, "_try_start_next"):
                manager._on_finished(job_id, 0, None)
            self.assertIsNone(job.bundle_path)
            self.assertIn("未成功提交", job.bundle_error)
            self.assertEqual(list(work.glob("*.zip")), [])


if __name__ == "__main__":
    unittest.main()
