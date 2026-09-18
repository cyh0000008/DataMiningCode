"""Copy a mining XLSX report to an optional user-managed archive folder."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def build_archive_stem(vehicle: str, date_text: str, version: str) -> str:
    parts = [_sanitize_filename_part(value) for value in (vehicle, date_text, version)]
    if any(not part for part in parts):
        raise ValueError("归档报告需要填写 Target 车型、日期和软件版本")
    return "-".join(parts)


def archive_report(
    source_report: str | Path,
    archive_dir: str | Path,
    vehicle: str,
    date_text: str,
    version: str,
) -> Path:
    source = Path(source_report)
    if not source.is_file():
        raise FileNotFoundError(f"原始报告不存在：{source}")
    if source.suffix.lower() != ".xlsx":
        raise ValueError(f"原始报告不是 XLSX 文件：{source}")

    destination_dir = Path(archive_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = build_archive_stem(vehicle, date_text, version)
    destination = _next_available_path(destination_dir, stem, source.suffix)
    shutil.copy2(source, destination)
    return destination


def _sanitize_filename_part(value: str) -> str:
    text = _INVALID_FILENAME_CHARS.sub("_", str(value).strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


def _next_available_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({index}){suffix}"
        index += 1
    return candidate
