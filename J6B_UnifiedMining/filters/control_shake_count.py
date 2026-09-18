from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from metric_utils import BaseMetric, MetricEvent, MetricResult, result_from_events
from signal_preprocessor import SignalFrame


@dataclass
class ShakeWindow:
    start_s: float
    end_s: float
    kind: str
    score: float
    acc_amp: float
    acc_zero_crossings: int
    acc_turns: int
    drive_request_amp_nm: float
    drive_actual_amp_nm: float
    brake_request_amp_nm: float
    brake_response_amp_nm: float
    brake_request_pulses: int
    brake_response_pulses: int
    brake_active_ratio: float


class ControlShakeCountMetric(BaseMetric):
    name = "control_shake_count"
    category = "MPI"
    result_mode = "event_count"
    required_fields = (
        "acceleration",
        "ego_speed",
        "acc_active_flag",
        "control_drive_request_torque",
        "actual_drive_torque",
        "control_brake_request_torque",
        "actual_brake_torque",
    )

    sample_period_s = 0.05
    window_s = 8.0
    stride_s = 0.5
    min_speed_kph = 5.0
    drive_acc_amp_min = 0.8
    drive_zc_min = 6
    drive_request_amp_min_nm = 280.0
    drive_actual_amp_min_nm = 250.0
    drive_brake_quiet_amp_max_nm = 200.0
    drive_brake_active_ratio_max = 0.10
    brake_acc_amp_min = 1.45
    brake_zc_min = 5
    brake_request_or_response_amp_min_nm = 500.0
    brake_pulse_prominence_nm = 500.0
    max_selected_windows_per_file = 3
    min_window_separation_s = 4.0

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        grid, arrays = self._resample(frame)
        windows = self._detect_windows(grid, arrays)
        events = [
            MetricEvent(
                start_s=window.start_s,
                end_s=window.end_s,
                severity=round(window.score, 3),
                message="control shake detected: %s" % window.kind,
                values={
                    "kind": window.kind,
                    "acc_amp": round(window.acc_amp, 3),
                    "acc_zero_crossings": window.acc_zero_crossings,
                    "acc_turns": window.acc_turns,
                    "drive_request_amp_nm": round(window.drive_request_amp_nm, 3),
                    "drive_actual_amp_nm": round(window.drive_actual_amp_nm, 3),
                    "brake_request_amp_nm": round(window.brake_request_amp_nm, 3),
                    "brake_response_amp_nm": round(window.brake_response_amp_nm, 3),
                    "brake_request_pulses": window.brake_request_pulses,
                    "brake_response_pulses": window.brake_response_pulses,
                    "brake_active_ratio": round(window.brake_active_ratio, 4),
                },
            )
            for window in windows
        ]
        return result_from_events(self.name, self.category, frame, len(events), events, self.result_mode)

    def _resample(self, frame: SignalFrame) -> Tuple[np.ndarray, dict]:
        source_t = frame.time_s.astype(float)
        if source_t.size < 2:
            return source_t, {}
        grid = np.arange(float(source_t[0]), float(source_t[-1]), self.sample_period_s)
        arrays = {
            "acceleration": interpolate_continuous(source_t, frame.col("acceleration").astype(float), grid),
            "ego_speed": interpolate_continuous(source_t, frame.col("ego_speed").astype(float), grid),
            "acc_active": sample_nearest(source_t, frame.col("acc_active_flag").astype(float), grid),
            "drive_request": interpolate_continuous(
                source_t,
                frame.col("control_drive_request_torque").astype(float),
                grid,
            ),
            "drive_actual": interpolate_continuous(source_t, frame.col("actual_drive_torque").astype(float), grid),
            "brake_request": interpolate_continuous(
                source_t,
                frame.col("control_brake_request_torque").astype(float),
                grid,
            ),
            "brake_response": interpolate_continuous(source_t, frame.col("actual_brake_torque").astype(float), grid),
        }
        return grid, arrays

    def _detect_windows(self, grid: np.ndarray, arrays: dict) -> List[ShakeWindow]:
        if grid.size < 2 or not arrays:
            return []

        active = np.nan_to_num(arrays["acc_active"]) >= 0.5
        speed_ok = np.nan_to_num(arrays["ego_speed"]) > self.min_speed_kph
        valid = active & speed_ok & np.isfinite(arrays["acceleration"])

        window_samples = max(2, int(round(self.window_s / self.sample_period_s)))
        stride_samples = max(1, int(round(self.stride_s / self.sample_period_s)))

        candidates: List[ShakeWindow] = []
        for start in range(0, len(grid) - window_samples, stride_samples):
            end = start + window_samples
            if not bool(np.all(valid[start:end])):
                continue

            acc = detrend(arrays["acceleration"][start:end])
            acc_amp = p95_p5(acc)
            zc = zero_crossings(acc)
            acc_turns = turn_count(acc, 0.14)

            drive_request = arrays["drive_request"][start:end]
            drive_actual = arrays["drive_actual"][start:end]
            brake_request = arrays["brake_request"][start:end]
            brake_response = arrays["brake_response"][start:end]

            drive_request_amp = p95_p5(drive_request)
            drive_actual_amp = p95_p5(drive_actual)
            brake_request_amp = p95_p5(brake_request)
            brake_response_amp = p95_p5(brake_response)
            brake_request_pulses = pulse_count(brake_request, self.brake_pulse_prominence_nm)
            brake_response_pulses = pulse_count(brake_response, self.brake_pulse_prominence_nm)
            brake_active_ratio = float(
                (
                    (np.nan_to_num(brake_request) > self.drive_brake_quiet_amp_max_nm)
                    | (np.nan_to_num(brake_response) > self.drive_brake_quiet_amp_max_nm)
                ).mean()
            )

            drive_like = (
                acc_amp >= self.drive_acc_amp_min
                and zc >= self.drive_zc_min
                and drive_request_amp >= self.drive_request_amp_min_nm
                and drive_actual_amp >= self.drive_actual_amp_min_nm
                and brake_request_amp < self.drive_brake_quiet_amp_max_nm
                and brake_response_amp < self.drive_brake_quiet_amp_max_nm
                and brake_active_ratio <= self.drive_brake_active_ratio_max
            )

            brake_like = (
                acc_amp >= self.brake_acc_amp_min
                and zc >= self.brake_zc_min
                and (
                    brake_request_amp >= self.brake_request_or_response_amp_min_nm
                    or brake_response_amp >= self.brake_request_or_response_amp_min_nm
                    or brake_request_pulses >= 1
                    or brake_response_pulses >= 1
                )
                and (brake_request_pulses + brake_response_pulses >= 1 or acc_turns >= 4)
            )

            if not (drive_like or brake_like):
                continue

            kind = "drive torque oscillation" if drive_like else "brake request/response oscillation"
            score = acc_amp + 0.08 * min(acc_turns, 8) + 0.10 * min(brake_request_pulses + brake_response_pulses, 4)
            candidates.append(
                ShakeWindow(
                    start_s=float(grid[start]),
                    end_s=float(grid[end - 1]),
                    kind=kind,
                    score=float(score),
                    acc_amp=float(acc_amp),
                    acc_zero_crossings=int(zc),
                    acc_turns=int(acc_turns),
                    drive_request_amp_nm=float(drive_request_amp),
                    drive_actual_amp_nm=float(drive_actual_amp),
                    brake_request_amp_nm=float(brake_request_amp),
                    brake_response_amp_nm=float(brake_response_amp),
                    brake_request_pulses=int(brake_request_pulses),
                    brake_response_pulses=int(brake_response_pulses),
                    brake_active_ratio=float(brake_active_ratio),
                )
            )

        candidates.sort(key=lambda item: item.score, reverse=True)
        selected: List[ShakeWindow] = []
        for item in candidates:
            overlaps = any(
                not (
                    item.end_s < previous.start_s - self.min_window_separation_s
                    or item.start_s > previous.end_s + self.min_window_separation_s
                )
                for previous in selected
            )
            if overlaps:
                continue
            selected.append(item)
            if len(selected) >= self.max_selected_windows_per_file:
                break
        return sorted(selected, key=lambda item: item.start_s)


def interpolate_continuous(source_t: np.ndarray, source_v: np.ndarray, target_t: np.ndarray) -> np.ndarray:
    finite = np.isfinite(source_t) & np.isfinite(source_v)
    if np.sum(finite) < 2:
        return np.full(target_t.shape, np.nan)
    return np.interp(target_t, source_t[finite], source_v[finite], left=np.nan, right=np.nan)


def sample_nearest(source_t: np.ndarray, source_v: np.ndarray, target_t: np.ndarray) -> np.ndarray:
    if source_t.size == 0:
        return np.full(target_t.shape, np.nan)
    right = np.searchsorted(source_t, target_t, side="left")
    right = np.clip(right, 0, source_t.size - 1)
    left = np.clip(right - 1, 0, source_t.size - 1)
    choose_right = np.abs(source_t[right] - target_t) < np.abs(target_t - source_t[left])
    index = np.where(choose_right, right, left)
    return source_v[index]


def p95_p5(values: np.ndarray) -> float:
    if values is None or not np.isfinite(values).any():
        return float("nan")
    return float(np.nanpercentile(values, 95) - np.nanpercentile(values, 5))


def smooth(values: np.ndarray, width: int = 7) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) < width:
        return values.copy()
    pad = width // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    return np.convolve(padded, np.ones(width) / width, mode="valid")[: len(values)]


def detrend(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if finite.sum() < 3:
        return values - np.nanmean(values)
    x = np.arange(len(values))
    slope, offset = np.polyfit(x[finite], values[finite], 1)
    return values - (slope * x + offset)


def zero_crossings(values: np.ndarray, epsilon: float = 0.04) -> int:
    signs = np.zeros_like(values, dtype=int)
    signs[values > epsilon] = 1
    signs[values < -epsilon] = -1
    non_zero = signs[signs != 0]
    if len(non_zero) < 2:
        return 0
    return int(np.sum(non_zero[1:] != non_zero[:-1]))


def turn_count(values: np.ndarray, prominence: float) -> int:
    y = smooth(np.asarray(values, dtype=float), 7)
    finite = np.isfinite(y)
    if finite.sum() < 8:
        return 0
    y = np.where(finite, y, np.nanmedian(y[finite]))
    dy = np.diff(y)
    deadband = max(prominence / 12.0, 1e-6)
    signs = np.zeros_like(dy, dtype=int)
    signs[dy > deadband] = 1
    signs[dy < -deadband] = -1

    last = 0
    for index, value in enumerate(signs):
        if value == 0:
            signs[index] = last
        else:
            last = value

    extrema: List[int] = []
    for index in range(1, len(signs)):
        if signs[index] and signs[index - 1] and signs[index] != signs[index - 1]:
            extrema.append(index)
    if not extrema:
        return 0

    filtered = [extrema[0]]
    center = float(np.nanmedian(y))
    for index in extrema[1:]:
        if abs(y[index] - y[filtered[-1]]) >= prominence:
            filtered.append(index)
        elif abs(y[index] - center) > abs(y[filtered[-1]] - center):
            filtered[-1] = index
    return len(filtered)


def pulse_count(values: np.ndarray, prominence_nm: float) -> int:
    y = np.asarray(values, dtype=float)
    if not np.isfinite(y).any():
        return 0
    baseline = float(np.nanpercentile(y, 10))
    high = float(np.nanpercentile(y, 90))
    threshold = baseline + max((high - baseline) * 0.35, prominence_nm * 0.25)
    above = y > threshold

    for index in range(1, len(above) - 1):
        if not above[index] and above[index - 1] and above[index + 1]:
            above[index] = True

    count = 0
    index = 0
    while index < len(above):
        if not above[index]:
            index += 1
            continue
        end = index
        while end < len(above) and above[end]:
            end += 1
        if float(np.nanmax(y[index:end]) - baseline) >= prominence_nm:
            count += 1
        index = end
    return count


def create_metric() -> BaseMetric:
    return ControlShakeCountMetric()
