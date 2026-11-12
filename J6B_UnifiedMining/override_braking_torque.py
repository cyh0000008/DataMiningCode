from __future__ import annotations

from typing import Callable

import numpy as np


def find_torque_braking_events(
    timestamps: np.ndarray,
    active: np.ndarray,
    override: np.ndarray,
    drive: np.ndarray,
    brake: np.ndarray,
    detect_braking: Callable[[int, int, int], dict | None],
    primary_end: float = float("inf"),
) -> list[dict]:
    t = np.asarray(timestamps, dtype=float)
    active = np.asarray(active)
    override = np.asarray(override)
    drive = np.asarray(drive, dtype=float)
    brake = np.asarray(brake, dtype=float)
    if any(values.shape != t.shape for values in (active, override, drive, brake)):
        raise ValueError("Override braking signals must share the same time axis")
    if not np.all(np.isfinite(t)) or np.any(np.diff(t) <= 0):
        raise ValueError("Override braking timestamps must be finite and strictly increasing")
    if not np.any(np.isfinite(drive)) or not np.any(np.isfinite(brake)):
        raise ValueError("实车驱动或制动扭矩无有效数据，Override后重制动未统计")

    def rates(values: np.ndarray) -> np.ndarray:
        # A trailing window prevents a future torque change from appearing before the 200 ms deadline.
        finite = np.isfinite(values)
        left = np.searchsorted(t, t - 0.08 - 1e-9, side="left")
        right = np.arange(len(t)) + 1
        sums = np.concatenate(([0.0], np.cumsum(np.where(finite, values, 0.0))))
        counts = np.concatenate(([0], np.cumsum(finite.astype(int))))
        smooth = np.full(t.shape, np.nan)
        count = counts[right] - counts[left]
        np.divide(sums[right] - sums[left], count, out=smooth,
                  where=(count == right - left) & (count > 0))
        result = np.full(t.shape, np.nan)
        if len(t) > 1:
            result[1:] = np.diff(smooth) / np.diff(t)
        return result

    drive_rate, brake_rate = rates(drive), rates(brake)
    valid = (np.isfinite(active.astype(float)) & (active.astype(float) != 0)
             & np.isfinite(override.astype(float)) & (override.astype(float) == 0))
    torque_valid = np.isfinite(drive) & np.isfinite(brake)
    events = []
    for release in range(1, len(t)):
        if not (override[release - 1] == 1 and override[release] == 0
                and valid[release] and t[release] <= primary_end):
            continue
        deadline = t[release] + 0.2
        for onset in range(release, len(t)):
            if t[onset] > deadline + 1e-9 or not valid[onset] or not torque_valid[onset]:
                break
            if onset == 0 or not (torque_valid[onset - 1] and torque_valid[onset]):
                continue
            brake_start = (brake_rate[onset] >= 2500.0
                           and (not np.isfinite(brake_rate[onset - 1]) or brake_rate[onset - 1] < 2500.0))
            drive_start = (drive_rate[onset] <= -1500.0
                           and (not np.isfinite(drive_rate[onset - 1]) or drive_rate[onset - 1] > -1500.0))
            if not (brake_start or drive_start):
                continue
            end = onset
            while (end + 1 < len(t) and t[end + 1] <= t[onset] + 1.0 + 1e-9
                   and valid[end + 1] and torque_valid[end + 1]):
                end += 1
            braking = detect_braking(release, onset, end)
            if braking is None:
                continue
            for kind, values, rate, threshold, direction, enabled in (
                ("brake", brake, brake_rate, 120.0, 1, brake_start),
                ("drive", drive, drive_rate, 80.0, -1, drive_start),
            ):
                if not enabled:
                    continue
                changes = direction * (values[onset:end + 1] - values[onset - 1])
                reaches = np.flatnonzero(changes >= threshold)
                if reaches.size:
                    event = dict(braking)
                    event.update(release_index=release, torque_onset_index=onset,
                                 torque_confirm_index=onset + int(reaches[0]),
                                 torque_type=kind, torque_rate=float(rate[onset]),
                                 torque_change=float(np.max(changes)))
                    events.append(event)
                    break
            else:
                continue
            break
    return events
