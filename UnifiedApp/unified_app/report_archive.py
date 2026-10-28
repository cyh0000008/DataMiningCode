"""Archive mining XLSX reports and bundle current-run deliverables as ZIP."""

from __future__ import annotations

import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def build_archive_stem(vehicle: str, date_text: str, version: str) -> str:
    date_text = date_text.strip()
    match = re.fullmatch(r"(20\d{6})(?:[-~](20\d{6}))?", date_text)
    if not match:
        raise ValueError("日期范围请使用 YYYYMMDD-YYYYMMDD，单日可填写 YYYYMMDD")
    start, end = match[1], match[2] or match[1]
    datetime.strptime(start, "%Y%m%d")
    datetime.strptime(end, "%Y%m%d")
    if start > end:
        raise ValueError("日期范围的开始日期不能晚于结束日期")
    date_text = f"{start}-{end}"
    parts = [_sanitize_filename_part(value) for value in (vehicle, date_text, version)]
    if any(not part for part in parts):
        raise ValueError("报告命名需要填写当前报告车型、日期范围和软件版本")
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


def bundle_reports(
    reports: list[Path], output_dir: Path, vehicle: str, version: str,
) -> Path:
    if not reports:
        raise ValueError("没有可打包的对比报告或 Bad Case Excel")
    parts = [_sanitize_filename_part(value) for value in (vehicle, version)]
    if any(not part for part in parts):
        raise ValueError("报告打包需要填写当前报告车型和软件版本")
    for report in reports:
        if not report.is_file():
            raise FileNotFoundError(f"待打包报告不存在：{report}")
        if report.suffix.lower() != ".xlsx":
            raise ValueError(f"待打包报告不是 XLSX 文件：{report}")
    if len({report.name.casefold() for report in reports}) != len(reports):
        raise ValueError("待打包报告文件名重复，无法放入同一 ZIP")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "_".join([*parts, datetime.now().strftime("%Y%m%d_%H%M%S")])
    destination = _next_available_path(output_dir, stem, ".zip")
    with destination.open("xb") as stream:
        try:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for report in reports:
                    archive.write(report, arcname=report.name)
        except (OSError, ValueError, RuntimeError, zipfile.LargeZipFile):
            stream.close()
            destination.unlink()
            raise
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
