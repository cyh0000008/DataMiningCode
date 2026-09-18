"""从子进程 stdout 文本行里解析进度，不依赖也不修改既有脚本的代码。

J6B DataMining.py 每处理一个 H5 文件会打印形如::

    [3/42] preprocessing xxx.h5

处理结束后会打印生成的汇总报告路径::

    Report written: /abs/path/to/Longitudinal_CPI_Report.xlsx

MMT DataMining.py 每进入一个 _done 目录会打印形如::

    ========== [3/42] /path/to/xxx_done ==========

处理结束后会打印生成的汇总报告路径::

    统计报告已生成：/abs/path/to/Unified_Longitudinal_Report_xxx.xlsx

两者都可以用同一个 ``[数字/数字]`` 正则提取当前/总数。CompareKPI 的
mpi_cpi_compare.py 是一次性脚本，没有逐条进度，只能在进程退出时给到 100%。

这里额外解析出的“报告已生成”路径，会被 job_manager 用作“挖掘完成后自动生成
对比报告”功能里 target 报告的来源，不需要用户再手动去输出目录里找文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_PROGRESS_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]")

_J6B_DONE_MARKERS = ("Report written:",)
_MMT_DONE_MARKERS = ("========== 全部处理完成 ==========", "统计报告已生成")
_COMPARE_DONE_MARKERS = ("已生成：",)

_ERROR_MARKERS = ("Traceback (most recent call last)", "执行失败：")

_J6B_REPORT_PATH_RE = re.compile(r"Report written:\s*(.+)\s*$")
_MMT_REPORT_PATH_RE = re.compile(r"统计报告已生成[：:]\s*(.+)\s*$")


@dataclass
class ProgressUpdate:
    current: Optional[int] = None
    total: Optional[int] = None
    percent: Optional[int] = None
    done_marker: bool = False
    error_marker: bool = False
    report_path: Optional[str] = None


def parse_line(kind: str, line: str) -> ProgressUpdate:
    update = ProgressUpdate()

    match = _PROGRESS_RE.search(line)
    if match:
        current, total = int(match.group(1)), int(match.group(2))
        if total > 0:
            update.current = current
            update.total = total
            update.percent = max(0, min(100, round(current * 100 / total)))

    done_markers = {
        "j6b": _J6B_DONE_MARKERS,
        "mmt": _MMT_DONE_MARKERS,
        "compare": _COMPARE_DONE_MARKERS,
    }.get(kind, ())
    if any(marker in line for marker in done_markers):
        update.done_marker = True
        update.percent = 100

    if kind == "j6b":
        report_match = _J6B_REPORT_PATH_RE.search(line)
        if report_match:
            update.report_path = report_match.group(1).strip()
    elif kind == "mmt":
        report_match = _MMT_REPORT_PATH_RE.search(line)
        if report_match:
            update.report_path = report_match.group(1).strip()

    if any(marker in line for marker in _ERROR_MARKERS):
        update.error_marker = True

    return update
