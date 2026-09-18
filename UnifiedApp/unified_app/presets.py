"""任务预设的保存 / 加载 / 列举（每类任务独立目录，JSON 文件）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

from .paths import presets_dir


_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*]+')


def _kind_dir(kind: str) -> Path:
    path = presets_dir() / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_preset_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("预设名称不能为空")
    name = _INVALID_NAME_CHARS.sub("_", name)
    return name


def list_presets(kind: str) -> List[str]:
    path = _kind_dir(kind)
    return sorted(p.stem for p in path.glob("*.json"))


def save_preset(kind: str, name: str, data: dict) -> Path:
    name = sanitize_preset_name(name)
    path = _kind_dir(kind) / f"{name}.json"
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def load_preset(kind: str, name: str) -> dict:
    path = _kind_dir(kind) / f"{name}.json"
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def delete_preset(kind: str, name: str) -> None:
    path = _kind_dir(kind) / f"{name}.json"
    if path.exists():
        path.unlink()
