"""任务队列 / 并发调度：所有任务共用一个全局队列，最多同时运行 N 个子进程。

除了普通任务外，还支持“挖掘完成后自动生成对比报告”：J6B / MMT 挖掘任务成功
结束后，会从其 stdout 中捕获到的“报告已生成”路径作为 target，自动提交一个
对比报告任务，不需要用户再手动去输出目录里找文件、手动点击对比报告页签。
"""

from __future__ import annotations

import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import config_writer
from .job_spec import (
    JOB_KIND_COMPARE,
    JOB_KIND_J6B,
    JOB_KIND_MMT,
    JOB_KIND_LABELS,
    CompareJobSpec,
    J6BJobSpec,
    MMTJobSpec,
)
from .paths import AppSettings
from .progress_parser import parse_line
from .report_archive import archive_report


STATUS_QUEUED = "排队中"
STATUS_RUNNING = "运行中"
STATUS_SUCCESS = "成功"
STATUS_FAILED = "失败"
STATUS_CANCELED = "已取消"

TERMINAL_STATUSES = {STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED}

_COMPARE_PROFILE_BY_KIND = {JOB_KIND_J6B: "J6B", JOB_KIND_MMT: "MMT"}


@dataclass
class Job:
    id: str
    kind: str
    title: str
    command: List[str]
    cwd: Path
    temp_files: List[Path] = field(default_factory=list)
    status: str = STATUS_QUEUED
    percent: int = 0
    progress_text: str = ""
    log_lines: List[str] = field(default_factory=list)
    process: Optional[QProcess] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None
    report_path: Optional[Path] = None
    pending_compare: Optional[Dict[str, Any]] = None
    report_archive: Optional[Dict[str, str]] = None
    archived_report_path: Optional[Path] = None

    @property
    def kind_label(self) -> str:
        return JOB_KIND_LABELS.get(self.kind, self.kind)

    @property
    def command_text(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)


class JobManager(QObject):
    job_added = Signal(str)
    job_updated = Signal(str)
    job_log = Signal(str, str)
    job_finished = Signal(str)

    def __init__(self, max_concurrent: int = 3, parent=None) -> None:
        super().__init__(parent)
        self.max_concurrent = max(1, min(3, max_concurrent))
        self._jobs: Dict[str, Job] = {}
        self._queue: List[str] = []
        self._running: Dict[str, Job] = {}

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------
    def set_max_concurrent(self, value: int) -> None:
        self.max_concurrent = max(1, min(3, value))
        self._try_start_next()

    def jobs(self) -> List[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at)

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def running_count(self) -> int:
        return len(self._running)

    def submit_j6b(
        self,
        python_exe: str,
        j6b_dir: Path,
        template_path: Path,
        spec: J6BJobSpec,
        compare_python: str = "",
        compare_dir: Optional[Path] = None,
    ) -> str:
        temp_config = config_writer.prepare_j6b_config(template_path, spec)
        title = spec.task_name.strip() or f"J6B 挖掘 {time.strftime('%H:%M:%S')}"
        command = [python_exe, str(j6b_dir / "DataMining.py"), str(temp_config)]
        pending_compare = self._build_pending_compare(
            JOB_KIND_J6B, title, spec.auto_compare, spec.compare_base_path,
            spec.compare_details_sheet, compare_python, compare_dir,
            base_vehicle=spec.compare_base_vehicle,
            target_vehicle=spec.compare_target_vehicle,
            base_date=spec.compare_base_date,
            target_date=spec.compare_target_date,
            base_version=spec.compare_base_version,
            target_version=spec.compare_target_version,
        )
        report_archive = self._build_report_archive(
            spec.raw_report_archive_dir,
            spec.compare_target_vehicle,
            spec.compare_target_date,
            spec.compare_target_version,
        )
        return self._submit(
            JOB_KIND_J6B, title, command, j6b_dir, [temp_config], pending_compare, report_archive
        )

    def submit_mmt(
        self,
        python_exe: str,
        mmt_dir: Path,
        template_path: Path,
        spec: MMTJobSpec,
        compare_python: str = "",
        compare_dir: Optional[Path] = None,
    ) -> str:
        temp_config = config_writer.prepare_mmt_config(template_path, spec)
        title = spec.task_name.strip() or f"MMT 挖掘 {time.strftime('%H:%M:%S')}"
        command = [
            python_exe,
            str(mmt_dir / "DataMining.py"),
            "--config",
            str(temp_config),
            "--resume",
            spec.resume_mode if spec.resume_mode in ("auto", "never") else "auto",
        ]
        if spec.dry_run:
            command.append("--dry-run")
        # dry-run 不会生成报告文件，即使勾选了自动对比也没有 target 可用，直接跳过
        auto_compare = spec.auto_compare and not spec.dry_run
        pending_compare = self._build_pending_compare(
            JOB_KIND_MMT, title, auto_compare, spec.compare_base_path,
            spec.compare_details_sheet, compare_python, compare_dir,
            base_vehicle=spec.compare_base_vehicle,
            target_vehicle=spec.compare_target_vehicle,
            base_date=spec.compare_base_date,
            target_date=spec.compare_target_date,
            base_version=spec.compare_base_version,
            target_version=spec.compare_target_version,
        )
        report_archive = self._build_report_archive(
            spec.raw_report_archive_dir,
            spec.compare_target_vehicle,
            spec.compare_target_date,
            spec.compare_target_version,
        )
        return self._submit(
            JOB_KIND_MMT, title, command, mmt_dir, [temp_config], pending_compare, report_archive
        )

    def submit_compare(self, python_exe: str, compare_dir: Path, spec: CompareJobSpec) -> str:
        title = spec.task_name.strip() or f"对比报告 {time.strftime('%H:%M:%S')}"
        command = [
            python_exe,
            str(compare_dir / "mpi_cpi_compare.py"),
            "--root",
            str(compare_dir),
            "--report-profile",
            spec.report_profile,
        ]
        optional_flag_map = {
            "--base": spec.base_path,
            "--target": spec.target_path,
            "--template": spec.template_path,
            "--output": spec.output_path,
            "--base-vehicle": spec.base_vehicle,
            "--target-vehicle": spec.target_vehicle,
            "--base-date": spec.base_date,
            "--target-date": spec.target_date,
            "--base-version": spec.base_version,
            "--target-version": spec.target_version,
            "--base-source-date": spec.base_source_date,
            "--target-source-date": spec.target_source_date,
        }
        for flag, value in optional_flag_map.items():
            if value and value.strip():
                command.extend([flag, value.strip()])
        if spec.details_sheet:
            command.append("--details-sheet")
        return self._submit(JOB_KIND_COMPARE, title, command, compare_dir, [])

    def cancel(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if job.status == STATUS_QUEUED:
            if job_id in self._queue:
                self._queue.remove(job_id)
            job.status = STATUS_CANCELED
            job.finished_at = time.time()
            self._cleanup_temp_files(job)
            self.job_updated.emit(job_id)
            self.job_finished.emit(job_id)
        elif job.status == STATUS_RUNNING and job.process is not None:
            job.status = STATUS_CANCELED
            self.job_updated.emit(job_id)
            job.process.kill()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    @staticmethod
    def _build_pending_compare(
        kind: str,
        source_title: str,
        auto_compare: bool,
        compare_base_path: str,
        compare_details_sheet: bool,
        compare_python: str,
        compare_dir: Optional[Path],
        base_vehicle: str = "",
        target_vehicle: str = "",
        base_date: str = "",
        target_date: str = "",
        base_version: str = "",
        target_version: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not auto_compare or compare_dir is None:
            return None
        compare_spec = CompareJobSpec(
            task_name=f"对比报告（自动）- {source_title}",
            report_profile=_COMPARE_PROFILE_BY_KIND.get(kind, "J6B"),
            base_path=compare_base_path,
            target_path="",  # 挖掘任务成功后从其输出中捕获报告路径再填入
            details_sheet=compare_details_sheet,
            base_vehicle=base_vehicle,
            target_vehicle=target_vehicle,
            base_date=base_date,
            target_date=target_date,
            base_version=base_version,
            target_version=target_version,
        )
        return {"python_exe": compare_python, "compare_dir": compare_dir, "spec": compare_spec}

    @staticmethod
    def _build_report_archive(
        archive_dir: str,
        target_vehicle: str,
        target_date: str,
        target_version: str,
    ) -> Optional[Dict[str, str]]:
        if not archive_dir.strip():
            return None
        if not all((target_vehicle.strip(), target_date.strip(), target_version.strip())):
            raise ValueError("启用原始报告额外保存时，请填写 Target 的车型、日期和软件版本")
        return {
            "archive_dir": archive_dir.strip(),
            "vehicle": target_vehicle.strip(),
            "date": target_date.strip(),
            "version": target_version.strip(),
        }

    def _submit(
        self,
        kind: str,
        title: str,
        command: List[str],
        cwd: Path,
        temp_files: List[Path],
        pending_compare: Optional[Dict[str, Any]] = None,
        report_archive: Optional[Dict[str, str]] = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        job = Job(
            id=job_id,
            kind=kind,
            title=title,
            command=command,
            cwd=cwd,
            temp_files=temp_files,
            pending_compare=pending_compare,
            report_archive=report_archive,
        )
        self._jobs[job_id] = job
        self._queue.append(job_id)
        self.job_added.emit(job_id)
        self._try_start_next()
        return job_id

    def _try_start_next(self) -> None:
        while self._queue and len(self._running) < self.max_concurrent:
            job_id = self._queue.pop(0)
            job = self._jobs.get(job_id)
            if job is None or job.status != STATUS_QUEUED:
                continue
            self._start(job)

    def _start(self, job: Job) -> None:
        process = QProcess(self)
        process.setProgram(job.command[0])
        process.setArguments(job.command[1:])
        process.setWorkingDirectory(str(job.cwd))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        job.process = process
        job.status = STATUS_RUNNING
        job.started_at = time.time()
        self._running[job.id] = job

        process.readyReadStandardOutput.connect(lambda job_id=job.id: self._on_output(job_id))
        process.finished.connect(lambda code, status, job_id=job.id: self._on_finished(job_id, code, status))
        process.errorOccurred.connect(lambda error, job_id=job.id: self._on_error(job_id, error))

        self.job_updated.emit(job.id)
        process.start()

    def _on_output(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.process is None:
            return
        raw = bytes(job.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not raw:
            return
        for line in raw.splitlines():
            line = line.rstrip()
            if not line:
                continue
            job.log_lines.append(line)
            update = parse_line(job.kind, line)
            if update.percent is not None:
                job.percent = max(job.percent, update.percent)
            if update.current is not None and update.total is not None:
                job.progress_text = f"{update.current}/{update.total}"
            if update.report_path:
                captured_path = Path(update.report_path)
                job.report_path = (
                    captured_path
                    if captured_path.is_absolute()
                    else (job.cwd / captured_path).resolve()
                )
            self.job_log.emit(job_id, line)
        self.job_updated.emit(job_id)

    def _on_error(self, job_id: str, error) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        message = f"[进程错误] {error}"
        job.log_lines.append(message)
        self.job_log.emit(job_id, message)

    def _on_finished(self, job_id: str, exit_code: int, exit_status) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        self._running.pop(job_id, None)
        job.exit_code = exit_code
        job.finished_at = time.time()
        if job.status == STATUS_CANCELED:
            pass
        elif exit_code == 0:
            job.status = STATUS_SUCCESS
            job.percent = 100
        else:
            job.status = STATUS_FAILED
        self._cleanup_temp_files(job)
        if job.status == STATUS_SUCCESS and job.report_archive is not None:
            self._archive_report(job)
        self.job_updated.emit(job_id)
        self.job_finished.emit(job_id)
        if job.status == STATUS_SUCCESS and job.pending_compare is not None:
            self._trigger_auto_compare(job)
        self._try_start_next()

    def _archive_report(self, job: Job) -> None:
        options = job.report_archive
        if options is None:
            return
        if job.report_path is None:
            message = "[原始报告归档] 未能从输出中捕获原始 XLSX 路径，跳过额外保存。"
            job.log_lines.append(message)
            self.job_log.emit(job.id, message)
            return
        try:
            archived_path = archive_report(
                job.report_path,
                options["archive_dir"],
                options["vehicle"],
                options["date"],
                options["version"],
            )
        except (OSError, ValueError) as exc:
            message = f"[原始报告归档] 保存失败：{exc}"
            job.log_lines.append(message)
            self.job_log.emit(job.id, message)
            return
        job.archived_report_path = archived_path
        message = f"[原始报告归档] 已额外保存：{archived_path}"
        job.log_lines.append(message)
        self.job_log.emit(job.id, message)

    def _trigger_auto_compare(self, job: Job) -> None:
        pending = job.pending_compare
        if pending is None:
            return
        if job.report_path is None:
            message = (
                "[自动对比] 未能从输出中捕获生成的报告路径，跳过自动生成对比报告，"
                "请在“对比报告”页签手动指定 target 路径。"
            )
            job.log_lines.append(message)
            self.job_log.emit(job.id, message)
            self.job_updated.emit(job.id)
            return
        spec: CompareJobSpec = pending["spec"]
        spec.target_path = str(job.report_path)
        message = f"[自动对比] 挖掘任务已完成，检测到报告：{job.report_path}，正在自动提交对比报告任务…"
        job.log_lines.append(message)
        self.job_log.emit(job.id, message)
        self.job_updated.emit(job.id)
        try:
            self.submit_compare(pending["python_exe"], pending["compare_dir"], spec)
        except Exception as exc:  # noqa: BLE001 - surface failure into the source job's log
            error_message = f"[自动对比] 提交对比报告任务失败：{exc}"
            job.log_lines.append(error_message)
            self.job_log.emit(job.id, error_message)
            self.job_updated.emit(job.id)

    @staticmethod
    def _cleanup_temp_files(job: Job) -> None:
        for path in job.temp_files:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
