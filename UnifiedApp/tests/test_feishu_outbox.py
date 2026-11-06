import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from UnifiedApp.unified_app.feishu_outbox import (
    enqueue_bundle, enqueue_mining_results, claim_delivery, finish_delivery,
)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "feishu_bot.json"
        self.path.write_text(json.dumps({"enabled": True, "owner_open_id": "ou_self", "chat_id": "oc_self"}))
        self.bundle = self.root / "reports.zip"
        with ZipFile(self.bundle, "w") as archive:
            archive.writestr("report.xlsx", b"fixture")
        self.env = patch.dict(os.environ, {"UNIFIED_MINING_DISABLE_PUSH": "", "UNIFIED_MINING_MANAGED": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_independent_producer_and_consumer_deduplicate_and_complete(self):
        key = enqueue_bundle(self.bundle, "GUI task", path=self.path)
        self.assertEqual(key, enqueue_bundle(self.bundle, "CLI task", path=self.path))
        row = claim_delivery(self.path)
        self.assertEqual(row["bundle"], str(self.bundle.resolve()))
        self.assertIsNone(claim_delivery(self.path))
        finish_delivery(self.path, key, True)
        self.assertIsNone(claim_delivery(self.path))
        enqueue_bundle(self.bundle, "same artifact", path=self.path)
        self.assertIsNone(claim_delivery(self.path))

    def test_failed_send_retained_and_later_retried(self):
        key = enqueue_bundle(self.bundle, path=self.path)
        claim_delivery(self.path)
        finish_delivery(self.path, key, False)
        self.assertIsNone(claim_delivery(self.path))
        import time
        with patch("UnifiedApp.unified_app.feishu_outbox.time.time", return_value=time.time() + 3601):
            self.assertEqual(claim_delivery(self.path)["outbox_id"], key)

    def test_explicit_resend_is_durable_and_not_deduplicated_as_original(self):
        original = enqueue_bundle(self.bundle, path=self.path)
        claim_delivery(self.path)
        finish_delivery(self.path, original, True)
        resend = enqueue_bundle(self.bundle, path=self.path, resend=True)
        self.assertNotEqual(original, resend)
        self.assertEqual(claim_delivery(self.path)["outbox_id"], resend)

    def test_crashed_sender_lease_recovers(self):
        key = enqueue_bundle(self.bundle, path=self.path)
        claim_delivery(self.path)
        import time
        with patch("UnifiedApp.unified_app.feishu_outbox.time.time", return_value=time.time() + 601):
            self.assertEqual(claim_delivery(self.path)["outbox_id"], key)

    def test_rebinding_never_sends_old_result_to_new_account(self):
        enqueue_bundle(self.bundle, path=self.path)
        self.path.write_text(json.dumps({"enabled": True, "owner_open_id": "ou_other", "chat_id": "oc_other"}))
        self.assertIsNone(claim_delivery(self.path))

    def test_unbound_or_disabled_has_no_upload(self):
        self.assertIsNone(enqueue_bundle(self.bundle, path=self.root / "missing.json"))
        with patch.dict(os.environ, {"UNIFIED_MINING_DISABLE_PUSH": "1"}):
            self.assertIsNone(enqueue_bundle(self.bundle, path=self.path))

    def test_standalone_j6b_and_mmt_package_only_current_reports(self):
        report = self.root / "summary.xlsx"
        bad = self.root / "bad.xlsx"
        report.write_bytes(b"summary")
        bad.write_bytes(b"bad")
        (self.root / "old.xlsx").write_bytes(b"old")
        with patch("UnifiedApp.unified_app.feishu_outbox.user_data_dir", return_value=self.root):
            for kind in ("j6b", "mmt"):
                key = enqueue_mining_results(kind, report, bad)
                self.assertTrue(key)
                row = claim_delivery(self.path)
                with ZipFile(row["bundle"]) as archive:
                    self.assertEqual(set(archive.namelist()), {"summary.xlsx", "bad.xlsx"})
                finish_delivery(self.path, key, True)
            with patch.dict(os.environ, {"UNIFIED_MINING_MANAGED": "1"}):
                self.assertIsNone(enqueue_mining_results("j6b", report, bad))


if __name__ == "__main__":
    unittest.main()
