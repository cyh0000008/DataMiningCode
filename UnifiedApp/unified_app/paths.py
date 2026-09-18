"""应用级设置：定位三个既有工程目录、Python 解释器路径，并持久化到用户目录。"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


APP_SETTINGS_DIR_NAME = ".unified_mining_app"
SETTINGS_FILE_NAME = "settings.json"

J6B_DIR_NAME = "J6B_UnifiedMining"
MMT_DIR_NAME = "MMT_UnifiedMining"
COMPARE_DIR_NAME = "CompareKPI_United"


def user_data_dir() -> Path:
    """跨平台的用户级应用数据目录（Windows/Ubuntu 都适用）。"""
    home = Path.home()
    path = home / APP_SETTINGS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def presets_dir() -> Path:
    path = user_data_dir() / "presets"
    for sub in ("j6b", "mmt", "compare"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def _app_base_dir() -> Path:
    """UnifiedApp 自身所在目录（源码运行时是本文件的上两级目录，打包后是 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _looks_like_repo_root(path: Path) -> bool:
    return all((path / name).is_dir() for name in (J6B_DIR_NAME, MMT_DIR_NAME, COMPARE_DIR_NAME))


def autodetect_repo_root() -> Optional[Path]:
    """尝试在 UnifiedApp 自身目录及其上级目录中找到三个既有工程。"""
    base = _app_base_dir()
    candidates = [base, base.parent, base.parent.parent]
    for candidate in candidates:
        if _looks_like_repo_root(candidate):
            return candidate
    return None


def default_python_executable() -> str:
    return shutil.which("python3") or shutil.which("python") or sys.executable or "python3"


@dataclass
class AppSettings:
    repo_root: str = ""
    j6b_python: str = ""
    mmt_python: str = ""
    compare_python: str = ""
    max_concurrent_jobs: int = 3

    def resolved_repo_root(self) -> Path:
        if self.repo_root:
            return Path(self.repo_root)
        detected = autodetect_repo_root()
        return detected if detected is not None else _app_base_dir()

    def j6b_dir(self) -> Path:
        return self.resolved_repo_root() / J6B_DIR_NAME

    def mmt_dir(self) -> Path:
        return self.resolved_repo_root() / MMT_DIR_NAME

    def compare_dir(self) -> Path:
        return self.resolved_repo_root() / COMPARE_DIR_NAME

    def python_for(self, kind: str) -> str:
        mapping = {
            "j6b": self.j6b_python,
            "mmt": self.mmt_python,
            "compare": self.compare_python,
        }
        value = mapping.get(kind, "")
        return value if value else default_python_executable()

    def is_valid(self) -> bool:
        root = self.resolved_repo_root()
        return _looks_like_repo_root(root)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if f in data}
        return cls(**{**cls().to_dict(), **known})


def settings_path() -> Path:
    return user_data_dir() / SETTINGS_FILE_NAME


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.exists():
        settings = AppSettings()
        detected = autodetect_repo_root()
        if detected is not None:
            settings.repo_root = str(detected)
        return settings
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        return AppSettings.from_dict(data)
    except Exception:
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    with path.open("w", encoding="utf-8") as fp:
        json.dump(settings.to_dict(), fp, ensure_ascii=False, indent=2, sort_keys=True)
