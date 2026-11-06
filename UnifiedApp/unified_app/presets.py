"""任务预设的保存 / 加载 / 列举（每类任务独立目录，JSON 文件）。"""

from __future__ import annotations

import json
import re
import hashlib
import os
import tempfile
from pathlib import Path
from typing import List
from PySide6.QtCore import QLockFile

from .paths import presets_dir


_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*]+')
_ANY_REVISION = object()


def _kind_dir(kind: str) -> Path:
    if kind not in ("j6b", "mmt", "compare"):
        raise ValueError("未知预设类型")
    path = presets_dir() / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_preset_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("预设名称不能为空")
    name = _INVALID_NAME_CHARS.sub("_", name)
    if name in (".", "..") or name.endswith((".", " ")) or any(ord(c) < 32 for c in name):
        raise ValueError("预设名称不合法")
    if re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.IGNORECASE):
        raise ValueError("预设名称不能使用系统保留名称")
    return name


def list_presets(kind: str) -> List[str]:
    path = _kind_dir(kind)
    return sorted(p.stem for p in path.glob("*.json"))


def preset_revision(kind: str, name: str) -> str | None:
    path = _kind_dir(kind) / f"{sanitize_preset_name(name)}.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def save_preset(kind: str, name: str, data: dict, *, expected_revision=_ANY_REVISION) -> Path:
    name = sanitize_preset_name(name)
    path = _kind_dir(kind) / f"{name}.json"
    lock = QLockFile(str(path.with_suffix(".lock")))
    if not lock.tryLock(1000):
        raise ValueError("预设正在被其他操作修改，请重试")
    temporary = None
    try:
        if expected_revision is not _ANY_REVISION and preset_revision(kind, name) != expected_revision:
            raise ValueError("预设已被修改，请重新查看并确认")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as fp:
            temporary = Path(fp.name)
            json.dump(data, fp, ensure_ascii=False, indent=2, sort_keys=True)
            fp.flush()
            os.fsync(fp.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlock()
    return path


def load_preset(kind: str, name: str) -> dict:
    path = _kind_dir(kind) / f"{sanitize_preset_name(name)}.json"
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def delete_preset(kind: str, name: str, *, expected_revision=_ANY_REVISION) -> None:
    path = _kind_dir(kind) / f"{sanitize_preset_name(name)}.json"
    lock = QLockFile(str(path.with_suffix(".lock")))
    if not lock.tryLock(1000):
        raise ValueError("预设正在被其他操作修改，请重试")
    try:
        if expected_revision is not _ANY_REVISION and preset_revision(kind, name) != expected_revision:
            raise ValueError("预设已被修改，请重新查看并确认")
        if path.exists():
            path.unlink()
    finally:
        lock.unlock()
