import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from UnifiedApp.unified_app.job_manager import JobManager
from UnifiedApp.unified_app.job_spec import MMTJobSpec
from UnifiedApp.unified_app.ui.mmt_form import MMTForm


ROOT = Path(__file__).resolve().parents[2]


class Settings:
    def mmt_dir(self):
        return ROOT / "MMT_UnifiedMining"


class MMTSignalPlotTests(unittest.TestCase):
    def test_ui_config_and_presets(self):
        app = QApplication.instance() or QApplication([])
        manager = JobManager()
        form = MMTForm(Settings, manager)
        try:
            self.assertTrue(form.bad_case_signal_plots_check.isChecked())
            form.source_path_edit.setText(str(ROOT))
            form.vehicle_edit.setText("TEST")
            form.software_version_edit.setText("V1")
            form.query_date_edit.setText("20261109-20261109")
            for enabled in (True, False):
                form.bad_case_signal_plots_check.setChecked(enabled)
                spec = MMTJobSpec.from_dict(json.loads(json.dumps(form._collect_spec().to_dict())))
                form._apply_spec(spec)
                self.assertIs(form.bad_case_signal_plots_check.isChecked(), enabled)
                with patch.object(manager, "_try_start_next"):
                    job_id = manager.submit_mmt(
                        sys.executable, ROOT / "MMT_UnifiedMining",
                        ROOT / "MMT_UnifiedMining" / "config.json", spec,
                    )
                job = manager.get_job(job_id)
                try:
                    config = json.loads(job.temp_files[0].read_text(encoding="utf-8"))
                    self.assertIs(config["bad_case_signal_plots"], enabled)
                finally:
                    manager._cleanup_temp_files(job)
            self.assertTrue(MMTJobSpec.from_dict({}).bad_case_signal_plots)
        finally:
            form.close()

    def test_can_topic_embedded_reports_errors_and_zip(self):
        result = subprocess.run(
            [sys.executable, "-c", r'''
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
import h5py
import numpy as np
from openpyxl import load_workbook
from DataMining import (load_config, load_bad_case_extract_config, generate_bad_case_extract_report,
                        make_plot_loader, run_all)
from bad_case_signal_plots import CAN_SIGNALS, can_series, topic_series, event_spans, embed_plots
from signal_lib import FolderSession
from jerk_utils import jerk_from_acceleration

sys.path.insert(0, str(Path.cwd().parent))
from UnifiedApp.unified_app.report_archive import bundle_reports

with tempfile.TemporaryDirectory() as folder:
    work = Path(folder)
    source = work / "20261109-120000_done"
    source.mkdir()
    t = np.round(np.arange(100., 104.01, .1), 6)
    h5path = source / "_msd_can_data.h5"
    signals = {
        key: (np.sin(t * 3) if info[0] == "Acceleration" else np.ones(t.size))
        for key, info in CAN_SIGNALS.items()
    }
    with h5py.File(h5path, "w") as h5:
        channel = h5.create_group("sample").create_group("Channel1")
        for message in {key.split(".")[0] for key in signals}:
            fields = {key.split(".")[1]: values for key, values in signals.items()
                      if key.split(".")[0] == message}
            data = np.zeros(t.size, dtype=[("timestamp", "f8")] + [(key, "f8") for key in fields])
            data["timestamp"] = t
            for key, values in fields.items():
                data[key] = values
            channel.create_dataset(message, data=data)
    series, missing = can_series(FolderSession(h5path), [], 100.)
    assert not missing
    jerk = next(item for item in series if item.unit == "m/s3")
    np.testing.assert_allclose(jerk.time_s, t - 100)
    np.testing.assert_allclose(jerk.values, jerk_from_acceleration(t, next(iter(signals.values()))))
    frames = [{
        "unp_timestamp_ms": timestamp * 1000.,
        "unp_frame_data": {"NP_State": {"f_Np_on": True}, "Acc_info": {"SetSpeed": 15.},
                           "curvature_info": {"is_curv_v_limit_enable": False, "curv_velocity_limit": 30.}},
        "matched_chassis": {"timestamp_ms": timestamp * 1000., "vehicle_speed_average": 10.,
                            "accleration_on_wheel": float(acc), "throttle_override": False},
        "matched_msd_control": {"timestamp_ms": timestamp * 1000., "slope_acc": .2},
        "matched_fusion": {"timestamp_ms": timestamp * 1000., "closest_obj_x": 25.,
                           "min_ttc": 10., "closest_obj_id": 7,
                           "following_candidates": [{"id": 7, "x": 25., "ttc": 10.},
                                                    {"id": 8, "x": 30., "ttc": 12.}]},
    } for timestamp, acc in zip(t, np.sin(t * 3))]
    topic, missing = topic_series(frames + [frames[-1]], 100.)
    assert not missing, missing
    total = next(item for item in topic if item.label.startswith("Total acceleration"))
    np.testing.assert_allclose(total.values, np.sin(t * 3) + .2)
    assert len(total.time_s) == len(t)  # Repeated chassis references do not duplicate samples.
    assert any(item.label == "Candidate 2 id" and np.all(item.values == 8) for item in topic)
    assert event_spans("[Event start=1.000, end=2.000, severity=1]") == [(1., 2.)]
    assert event_spans("max_jerk_time:100000.0") == []  # Do not guess time units or events.
    config_path = work / "config.json"
    raw = {"source_path": str(source), "max_depth": 1, "output_dir": str(work / "out"),
           "report_prefix": "test", "powertrain_type": "PHEV", "bad_case_signal_plots": True,
           "pipelines": [{"name": kind, "type": kind, "func_folder_path": str(work / kind)}
                         for kind in ("can", "topic")]}
    for kind in ("can", "topic"):
        metric_dir = work / kind
        metric_dir.mkdir()
        (metric_dir / "sample_metric.py").write_text(
            'def __run_haoran_data_run__(context):\n'
            '    return True, ["[Event start=101.000, end=102.000]"], '
            '{"All Case Count": 1, "Bad Case": 1, "Severity Score": 100.0}\n', encoding="utf-8")
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load_config(config_path)
    raw.pop("bad_case_signal_plots")
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_config(config_path).bad_case_signal_plots is True
    raw["bad_case_signal_plots"] = False
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_config(config_path).bad_case_signal_plots is False
    rules_path = work / "rules.json"
    rules_path.write_text('{"enabled":true}', encoding="utf-8")
    with patch("DataMining.collect_eligible_folders", return_value=[str(source)]), \
         patch("DataMining.load_topic_frames_cached", return_value=frames), \
         patch("UnifiedApp.unified_app.feishu_outbox.enqueue_mining_results", return_value=False):
        assert run_all(cfg, bad_case_config_path=rules_path, config_path=config_path, resume_mode="never") == 0
    reports = list(cfg.output_dir.glob("bad_case_report*.xlsx"))
    assert len(reports) == 1, list(cfg.output_dir.iterdir())
    report = reports[0]
    wb = load_workbook(report)
    try:
        sheet = wb.active
        assert sheet.cell(1, 11).value == "关键信号图"
        assert len(sheet._images) == 2, [sheet.cell(row, 12).value for row in (2, 3)]
        assert all("整段概览" in sheet.cell(row, 12).value for row in (2, 3))
        assert all(sheet.row_dimensions[row].height <= 409 for row in (2, 3))
    finally:
        wb.close()
    with ZipFile(report) as xlsx:
        assert len([name for name in xlsx.namelist() if name.startswith("xl/media/")]) == 2
    bundle = bundle_reports([report], work / "zip", "TEST", "V1")
    with ZipFile(bundle) as archive:
        import io
        with ZipFile(io.BytesIO(archive.read(report.name))) as xlsx:
            assert len([name for name in xlsx.namelist() if name.startswith("xl/media/")]) == 2
    # Disabled mode preserves the original ten-column report, with no image data.
    stamp = report.stem.removeprefix("bad_case_report_")
    plain = generate_bad_case_extract_report(cfg.output_dir, stamp, load_bad_case_extract_config(rules_path))
    wb = load_workbook(plain)
    try:
        assert not wb.active._images and wb.active.max_column == 10
    finally:
        wb.close()
    # Per-row load errors are visible in both Excel and logs, not success-shaped images.
    from openpyxl import Workbook
    wb = Workbook()
    row = {"pipeline_type": "can", "output_prefix": "broken", "folder_path": str(source),
           "detail_content": ""}
    def broken(row):
        raise FileNotFoundError("H5 removed")
    embed_plots(wb.active, [row], broken, work / "failed")
    assert "绘图失败" in wb.active.cell(2, 12).value
    assert not wb.active._images
    wb.close()
    raw["bad_case_signal_plots"] = "false"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        load_config(config_path)
    except ValueError as exc:
        assert "boolean" in str(exc)
    else:
        raise AssertionError("string option should be rejected")
'''],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "UNIFIED_MINING_DISABLE_PUSH": "1"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
