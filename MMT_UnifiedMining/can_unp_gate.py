"""Time-aligned Topic UNP eligibility for ACC-controlled CAN metrics."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from metrics.can.code.acc_unreasonable_low_speed_count import (
    _find_topic_file, _nearest_frame, _parse_unp_planning_file,
)


ACC_ACTIVE_SIGNALS = {
    "Adaptive_Cruise_Disp_Stat.IACCAct376",
    "PPEI_Adaptive_Cruise_Axl_Trq_Req_194_M.IACCATC_ACCAct",
}
MATCH_TOLERANCE_MS = 300.0


class UnpAlignmentError(ValueError):
    pass


class UnpActiveGate:
    def __init__(self, source_folders: list[str]) -> None:
        frames = []
        for folder in source_folders:
            path = _find_topic_file(
                Path(folder), ["_unp_planning.txt"], ["unp", "planning"],
            )
            if path is None:
                raise UnpAlignmentError(f"缺少 Topic UNP planning 文件：{folder}")
            parsed = _parse_unp_planning_file(path)
            if not parsed:
                raise UnpAlignmentError(f"Topic UNP planning 没有可解析帧：{path}")
            frames.extend(parsed)
        by_time = {}
        for frame in frames:
            timestamp = float(frame["timestamp_ms"])
            if not math.isfinite(timestamp):
                raise UnpAlignmentError("Topic UNP 时间戳无效")
            previous = by_time.get(timestamp)
            if previous is not None and previous.get("np_on") != frame.get("np_on"):
                raise UnpAlignmentError("Topic UNP 同一时间戳存在冲突状态")
            by_time[timestamp] = frame
        self.timestamps = sorted(by_time)
        self.frames = [by_time[timestamp] for timestamp in self.timestamps]

    def states(self, timestamps: list[float]) -> list[bool]:
        result = []
        for timestamp in timestamps:
            if not math.isfinite(float(timestamp)):
                raise UnpAlignmentError("CAN 时间戳无效，无法对齐 UNP")
            frame, gap = _nearest_frame(
                self.frames, self.timestamps, float(timestamp) * 1000.0, MATCH_TOLERANCE_MS,
            )
            if frame is None:
                raise UnpAlignmentError(
                    f"CAN {timestamp:.6f}s 无对应 UNP 帧（允许偏差 {MATCH_TOLERANCE_MS:.0f}ms，实际 {gap}ms）")
            state = frame.get("np_on")
            if type(state) is not bool:
                raise UnpAlignmentError(f"CAN {timestamp:.6f}s 对应 Topic 的 FrameInfo.function 缺失或无效")
            result.append(state)
        return result

    def apply(self, timestamps: list[float], values: list[object]) -> list[object]:
        if len(timestamps) != len(values):
            raise ValueError("CAN 开启信号与时间轴长度不一致")
        return [value if enabled else 0 for value, enabled in zip(values, self.states(timestamps))]


def requires_unp(module: object) -> bool:
    return bool(ACC_ACTIVE_SIGNALS.intersection(getattr(module, "REQUIRED_SIGNALS", [])))


def validate_coverage(gate: UnpActiveGate, session: Any, modules: list[Any]) -> None:
    checked = set()
    for module in modules:
        if not requires_unp(module):
            continue
        required = list(module.REQUIRED_SIGNALS)
        if session.missing_signals(required):
            continue
        start, end = session.common_time_range(required)
        for signal in required:
            key = (signal, start, end)
            if key in checked:
                continue
            timestamps, _values = session.get_signal_slice(signal, start, end)
            gate.states(timestamps)
            checked.add(key)
