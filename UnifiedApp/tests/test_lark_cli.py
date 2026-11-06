import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

from UnifiedApp.unified_app import lark_cli
from UnifiedApp.unified_app.feishu_bot import cli_command


class LarkCliSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def version(self, version="1.0.99", code=0):
        class Result:
            returncode = code
            stdout = "lark-cli version " + version
        return Result()

    def test_build_path_is_used_despite_different_desktop_path(self):
        manifest = self.root / lark_cli.MANIFEST_NAME
        manifest.write_text("/home/user/.npm-global/bin/lark-cli\n", encoding="utf-8")
        with patch.object(lark_cli.sys, "frozen", True, create=True), \
                patch.object(lark_cli.sys, "_MEIPASS", str(self.root), create=True), \
                patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
                patch.object(lark_cli.shutil, "which", side_effect=lambda path: path), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version()) as run:
            self.assertEqual(lark_cli.resolve_linux_cli(), os.path.abspath("/home/user/.npm-global/bin/lark-cli"))
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["/home/user/.npm-global/bin/lark-cli", "--version"])

    def test_explicit_override_wins_over_bundled_cli_and_is_validated(self):
        (self.root / lark_cli.MANIFEST_NAME).write_text("/usr/bin/lark-cli\n", encoding="utf-8")
        with patch.object(lark_cli.sys, "frozen", True, create=True), \
                patch.object(lark_cli.sys, "_MEIPASS", str(self.root), create=True), \
                patch.dict(os.environ, {"MINING_LARK_CLI": "/custom/lark-cli"}, clear=True), \
                patch.object(lark_cli.shutil, "which", side_effect=lambda path: path), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version()):
            self.assertEqual(lark_cli.resolve_linux_cli(), os.path.abspath("/custom/lark-cli"))

    def test_old_path_candidate_falls_back_to_new_user_cli(self):
        home_cli = str(self.root / ".npm-global" / "bin" / "lark-cli")

        def which(path):
            return {"lark-cli": "/usr/bin/lark-cli", "/usr/bin/lark-cli": "/usr/bin/lark-cli",
                    home_cli: home_cli}.get(path)

        def run(args, **kwargs):
            return self.version("1.0.78" if args[0] == "/usr/bin/lark-cli" else "1.0.99")

        with patch.dict(os.environ, {}, clear=True), patch.object(lark_cli.Path, "home", return_value=self.root), \
                patch.object(lark_cli.shutil, "which", side_effect=which), \
                patch.object(lark_cli.subprocess, "run", side_effect=run), \
                patch.object(lark_cli.sys, "frozen", False, create=True):
            self.assertEqual(lark_cli.resolve_linux_cli(), home_cli)

    def test_build_without_override_prefers_compatible_npm_cli_over_system_path(self):
        home_cli = str(self.root / ".npm-global" / "bin" / "lark-cli")

        def which(path):
            return {"lark-cli": "/usr/bin/lark-cli", "/usr/bin/lark-cli": "/usr/bin/lark-cli",
                    home_cli: home_cli}.get(path)

        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
                patch.object(lark_cli.Path, "home", return_value=self.root), \
                patch.object(lark_cli.shutil, "which", side_effect=which), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version()) as run, \
                patch.object(lark_cli.sys, "frozen", False, create=True):
            self.assertEqual(lark_cli.resolve_linux_cli(), home_cli)
            self.assertEqual(run.call_args.args[0], [home_cli, "--version"])

    def test_build_without_override_uses_path_when_npm_cli_is_old(self):
        home_cli = str(self.root / ".npm-global" / "bin" / "lark-cli")

        def which(path):
            return {"lark-cli": "/usr/bin/lark-cli", "/usr/bin/lark-cli": "/usr/bin/lark-cli",
                    home_cli: home_cli}.get(path)

        def run(args, **kwargs):
            return self.version("1.0.78" if args[0] == home_cli else "1.0.99")

        with patch.dict(os.environ, {}, clear=True), patch.object(lark_cli.Path, "home", return_value=self.root), \
                patch.object(lark_cli.shutil, "which", side_effect=which), \
                patch.object(lark_cli.subprocess, "run", side_effect=run), \
                patch.object(lark_cli.sys, "frozen", False, create=True):
            self.assertEqual(lark_cli.resolve_linux_cli(), os.path.abspath("/usr/bin/lark-cli"))

    def test_bundled_or_explicit_old_or_missing_path_fails_without_silent_fallback(self):
        manifest = self.root / lark_cli.MANIFEST_NAME
        manifest.write_text("/usr/bin/lark-cli", encoding="utf-8")
        with patch.object(lark_cli.sys, "frozen", True, create=True), \
                patch.object(lark_cli.sys, "_MEIPASS", str(self.root), create=True), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(lark_cli.shutil, "which", side_effect=lambda path: path), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version("1.0.78")):
            with self.assertRaisesRegex(ValueError, "版本过旧"):
                lark_cli.resolve_linux_cli()
        with patch.dict(os.environ, {"MINING_LARK_CLI": "/missing/cli"}, clear=True), \
                patch.object(lark_cli.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "不存在"):
                lark_cli.resolve_linux_cli()

    def test_bad_version_and_nonabsolute_manifest_are_rejected(self):
        (self.root / lark_cli.MANIFEST_NAME).write_text("relative/path", encoding="utf-8")
        with patch.object(lark_cli.sys, "frozen", True, create=True), \
                patch.object(lark_cli.sys, "_MEIPASS", str(self.root), create=True), \
                patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "路径无效"):
                lark_cli.resolve_linux_cli()
        with patch.object(lark_cli.shutil, "which", return_value="/usr/bin/lark-cli"), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version("bad")):
            with self.assertRaisesRegex(ValueError, "无法识别"):
                lark_cli._validate("/usr/bin/lark-cli")

    def test_linux_bridge_uses_resolver(self):
        with patch("UnifiedApp.unified_app.feishu_bot.sys.platform", "linux"), \
                patch("UnifiedApp.unified_app.feishu_bot.resolve_linux_cli", return_value="/custom/lark-cli"):
            self.assertEqual(cli_command(), ["/custom/lark-cli"])

    def test_npm_wrapper_uses_same_package_native_binary_for_clean_shutdown(self):
        package = self.root / "node_modules" / "@larksuite" / "cli"
        script = package / "scripts" / "run.js"
        native = package / "bin" / "lark-cli"
        script.parent.mkdir(parents=True)
        native.parent.mkdir(parents=True)
        script.write_text("# fake wrapper", encoding="utf-8")
        native.write_text("# fake binary", encoding="utf-8")
        with patch.object(lark_cli.shutil, "which", side_effect=lambda path: path), \
                patch.object(lark_cli.os, "access", return_value=True), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version()) as run:
            self.assertEqual(lark_cli._validate(str(script)), str(native))
            self.assertEqual([call.args[0][0] for call in run.call_args_list], [str(script), str(native)])

    def test_unrelated_node_script_is_not_rewritten(self):
        script = self.root / "scripts" / "run.js"
        script.parent.mkdir()
        script.write_text("# fake wrapper", encoding="utf-8")
        with patch.object(lark_cli.shutil, "which", side_effect=lambda path: path), \
                patch.object(lark_cli.subprocess, "run", return_value=self.version()):
            self.assertEqual(lark_cli._validate(str(script)), str(script))

    def test_pyinstaller_spec_embeds_only_the_requested_manifest(self):
        manifest = self.root / lark_cli.MANIFEST_NAME
        manifest.write_text("/opt/lark-cli\n", encoding="utf-8")
        spec = Path(__file__).resolve().parents[1] / "packaging" / "UnifiedApp.spec"
        analysis = Mock()
        analysis.return_value = Mock(pure=[], scripts=[], binaries=[], datas=[])
        with patch.dict(os.environ, {"UNIFIED_LARK_CLI_MANIFEST": str(manifest)}, clear=True):
            runpy.run_path(str(spec), init_globals={
                "SPECPATH": str(spec.parent), "Analysis": analysis,
                "PYZ": Mock(), "EXE": Mock(),
            })
        self.assertEqual(analysis.call_args.kwargs["datas"], [(str(manifest), ".")])
        with patch.dict(os.environ, {}, clear=True):
            runpy.run_path(str(spec), init_globals={
                "SPECPATH": str(spec.parent), "Analysis": analysis,
                "PYZ": Mock(), "EXE": Mock(),
            })
        self.assertEqual(analysis.call_args.kwargs["datas"], [])


if __name__ == "__main__":
    unittest.main()
