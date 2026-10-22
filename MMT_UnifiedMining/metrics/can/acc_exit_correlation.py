from __future__ import annotations

import math
from typing import Iterable


CORRELATION_WINDOW_SECONDS = 1.0


def find_correlated_acc_exits(
    timestamps: Iterable[float],
    acc_values: Iterable[object],
    trigger_values: Iterable[object],
) -> list[tuple[float, float]]:
    events: list[tuple[float, float]] = []
    pending_triggers: list[float] = []
    previous_acc: bool | None = None
    previous_trigger: bool | None = None

    for timestamp, acc_value, trigger_value in zip(timestamps, acc_values, trigger_values):
        acc_active = _binary_state(acc_value, allow_nonzero=True)
        trigger_active = _binary_state(trigger_value)
        if acc_active is None or trigger_active is None:
            pending_triggers.clear()
            previous_acc = None
            previous_trigger = None
            continue

        timestamp = float(timestamp)
        if previous_acc is True and not acc_active:
            matching_triggers = [
                trigger_time
                for trigger_time in pending_triggers
                if 0.0 <= timestamp - trigger_time <= CORRELATION_WINDOW_SECONDS
            ]
            if matching_triggers:
                events.append((max(matching_triggers), timestamp))
            pending_triggers.clear()
        elif not acc_active:
            pending_triggers.clear()
        else:
            pending_triggers = [
                trigger_time
                for trigger_time in pending_triggers
                if timestamp - trigger_time <= CORRELATION_WINDOW_SECONDS
            ]
            if previous_trigger is False and trigger_active:
                pending_triggers.append(timestamp)

        previous_acc = acc_active
        previous_trigger = trigger_active

    return events


def _binary_state(value: object, allow_nonzero: bool = False) -> bool | None:
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "1.0", "true", "active"}:
            return True
        if normalized in {"0", "0.0", "false", "inactive"}:
            return False
        if allow_nonzero:
            try:
                numeric = float(normalized)
            except ValueError:
                return None
            if math.isfinite(numeric):
                return numeric != 0.0
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            if allow_nonzero:
                return numeric != 0.0
            if numeric in (0.0, 1.0):
                return numeric == 1.0
    return None
