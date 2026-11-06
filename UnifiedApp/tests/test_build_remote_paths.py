import json
from pathlib import Path
import tempfile
import unittest

from UnifiedApp.unified_app.build_remote_paths import initialize_remote_paths


class BuildRemotePathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config" / "feishu_bot.json"

    def test_new_config_authorizes_root_for_each_access(self):
        self.assertTrue(initialize_remote_paths(self.path))
        config = json.loads(self.path.read_text(encoding="utf-8"))
        aliases = config["remote"]["path_aliases"]
        self.assertEqual({value["access"] for value in aliases.values()}, {"input", "output", "report"})
        self.assertTrue(all(value["path"] == "/" for value in aliases.values()))
        self.assertFalse(initialize_remote_paths(self.path))

    def test_existing_empty_config_keeps_binding_and_other_settings(self):
        self.path.parent.mkdir()
        original = {"enabled": True, "owner_open_id": "ou_test", "remote": {
            "machine_name": "mine", "path_aliases": {}, "other": 1}}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        self.assertTrue(initialize_remote_paths(self.path))
        config = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(config["enabled"])
        self.assertEqual(config["owner_open_id"], "ou_test")
        self.assertEqual(config["remote"]["machine_name"], "mine")
        self.assertEqual(config["remote"]["other"], 1)

    def test_existing_partial_authorization_is_not_changed(self):
        self.path.parent.mkdir()
        original = '{"remote":{"path_aliases":{"data":{"path":"/data","access":"input"}}}}'
        self.path.write_text(original, encoding="utf-8")
        self.assertFalse(initialize_remote_paths(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_invalid_configuration_fails_without_replacing_file(self):
        self.path.parent.mkdir()
        for original in ("{", "[]", '{"remote": null}', '{"remote":{"path_aliases":[]}}'):
            with self.subTest(original=original):
                self.path.write_text(original, encoding="utf-8")
                with self.assertRaises(ValueError):
                    initialize_remote_paths(self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), original)
