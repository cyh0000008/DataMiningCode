"""Initialize Linux build user's remote path permissions without overwriting existing aliases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .paths import user_data_dir


def initialize_remote_paths(path: Path) -> bool:
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("飞书配置必须是 JSON 对象")
    else:
        config = {}
    remote = config.get("remote", {})
    if not isinstance(remote, dict):
        raise ValueError("飞书远程配置必须是 JSON 对象")
    aliases = remote.get("path_aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("飞书路径授权必须是 JSON 对象")
    if aliases:
        return False
    remote["path_aliases"] = {
        "全部路径-输入": {"path": "/", "access": "input"},
        "全部路径-输出": {"path": "/", "access": "output"},
        "全部路径-基线": {"path": "/", "access": "report"},
    }
    config["remote"] = remote
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".feishu_bot.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main() -> None:
    path = user_data_dir() / "feishu_bot.json"
    if initialize_remote_paths(path):
        print(f"已为当前用户配置全部路径授权（input/output/report: /）：{path}")
        print("注意：飞书可浏览当前应用账号有权访问的文件系统；系统权限不会提升。")
    else:
        print(f"检测到已有飞书路径授权，保持原配置不变：{path}")


if __name__ == "__main__":
    main()
