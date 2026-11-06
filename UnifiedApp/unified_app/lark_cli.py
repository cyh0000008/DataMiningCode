"""Resolve a CLI version that supports card and stdin-based message delivery."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


MIN_VERSION = (1, 0, 99)
MANIFEST_NAME = "mining-lark-cli-path.txt"


def _validate(path: str, *, unwrap_npm: bool = True) -> str:
    executable = shutil.which(path)
    if not executable:
        raise ValueError(f"飞书 CLI 不存在或不可执行：{path}")
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"无法检查飞书 CLI：{path}：{exc}") from exc
    match = re.search(r"\blark-cli version (\d+)\.(\d+)\.(\d+)\b", result.stdout)
    if result.returncode or match is None:
        raise ValueError(f"无法识别飞书 CLI 版本：{path}")
    if tuple(map(int, match.groups())) < MIN_VERSION:
        raise ValueError(f"飞书 CLI 版本过旧：{path}；请升级至 1.0.99 或更新版本")
    if unwrap_npm:
        target = Path(executable).resolve()
        package = target.parent.parent
        if (target.name == "run.js" and target.parent.name == "scripts"
                and package.name == "cli" and package.parent.name == "@larksuite"):
            native = package / "bin" / "lark-cli"
            if native.is_file() and os.access(native, os.X_OK):
                return _validate(str(native), unwrap_npm=False)
    return os.path.abspath(executable)


def resolve_linux_cli() -> str:
    override = os.environ.get("MINING_LARK_CLI")
    if override:
        return _validate(override)
    bundle = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle:
        manifest = Path(bundle) / MANIFEST_NAME
        if manifest.is_file():
            path = manifest.read_text(encoding="utf-8").strip()
            if not path or not os.path.isabs(path):
                raise ValueError("打包记录的飞书 CLI 路径无效，请重新打包")
            return _validate(path)
    candidates = [str(Path.home() / ".npm-global" / "bin" / "lark-cli"),
                  str(Path.home() / ".local" / "bin" / "lark-cli"),
                  shutil.which("lark-cli")]
    failures = []
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return _validate(candidate)
        except ValueError as exc:
            failures.append(str(exc))
    detail = "；".join(failures) if failures else "未找到 lark-cli"
    raise ValueError(f"{detail}；请安装新版 CLI 后重新打包，或设置 MINING_LARK_CLI")


if __name__ == "__main__":
    try:
        print(resolve_linux_cli())
    except (OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
