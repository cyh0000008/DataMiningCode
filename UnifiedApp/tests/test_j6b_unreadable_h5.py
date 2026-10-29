import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

J6B = Path(__file__).resolve().parents[2] / "J6B_UnifiedMining"


class UnreadableH5Test(unittest.TestCase):
    def test_bad_h5_is_skipped_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            names = [
                "2026-10-27-18-48-50_can_parsed.h5",
                "2026-10-27-18-48-55_can_parsed.h5",
                "2026-10-27-18-49-00_can_parsed.h5",
                "2026-10-27-18-49-05_can_parsed.h5",
            ]
            bad_name = names[2]
            for name in (names[0], names[1], names[3]):
                with h5py.File(work / name, "w") as h5:
                    h5.create_group("metadata")
                    h5.create_dataset("sample/timestamp", data=np.arange(0., 5., .1))
                    h5.create_dataset("sample/signals/acc_active", data=np.ones(50))
                    h5.create_dataset("sample/signals/abs_active", data=np.zeros(50))
                    h5.create_dataset("sample/signals/acceleration", data=np.zeros(50))
            (work / bad_name).write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 64)
            config = {
                "input_path": [str(work)], "powertrain_type": "ICE",
                "signals": {"acc_active": "sample/signals/acc_active",
                            "abs_active": "sample/signals/abs_active",
                            "acceleration": "sample/signals/acceleration"},
                "defaults": {"h5_read_retries": 1, "h5_read_retry_delay_s": 0},
                "enabled_metrics": ["acc_exit_after_abs", "curvature_min_speed"], "output_dir": str(work / "run"),
                "lcc_sync_check": False,
            }
            cfg = work / "config.json"
            cfg.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(J6B / "DataMining.py"), str(cfg)], cwd=J6B,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                capture_output=True, encoding="utf-8", timeout=60,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("retrying (1/1)", output)
            self.assertIn("[4/4] preprocessing", output)
            stitched = [line for line in output.splitlines() if "stitching next H5" in line]
            self.assertEqual(len(stitched), 1, output)
            self.assertIn(names[1], stitched[0])
            self.assertEqual(output.count("retrying (1/1)"), 1, output)
            self.assertIn("1 unreadable H5 file(s) were skipped", output)
            manifest = json.loads(next((work / "run").rglob("run_manifest.json")).read_text(encoding="utf-8"))
            self.assertFalse(manifest["complete"])
            self.assertEqual([Path(item["file"]).name for item in manifest["failed_h5_files"]], [bad_name])
            self.assertEqual(manifest["failed_h5_files"][0]["error_type"], "OSError")


if __name__ == "__main__":
    unittest.main()
