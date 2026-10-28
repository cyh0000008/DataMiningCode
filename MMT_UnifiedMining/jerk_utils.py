from __future__ import annotations

import warnings
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np


JERK_SMOOTHING_WINDOW_S = 0.3
JERK_REGRESSION_WINDOW_S = 0.3
JERK_PARAMETERS = {
    "jerk_method": "centered_window_linear_regression",
    "acceleration_smoothing_window_s": JERK_SMOOTHING_WINDOW_S,
    "jerk_regression_window_s": JERK_REGRESSION_WINDOW_S,
}


def _time_window_bounds(t: np.ndarray, window_s: float) -> Tuple[np.ndarray, np.ndarray]:
    half_window = float(window_s) / 2.0
    # Include mathematical endpoints despite floating-point timestamp rounding.
    tolerance = np.maximum(1e-9, 4 * np.finfo(float).eps * np.abs(t))
    return (
        np.searchsorted(t, t - half_window - tolerance, side="left"),
        np.searchsorted(t, t + half_window + tolerance, side="right"),
    )


def time_window_moving_average(t: np.ndarray, values: np.ndarray, window_s: float) -> np.ndarray:
    values = values.astype(float)
    if window_s <= 0.0 or values.size <= 2:
        return values.copy()
    left, right = _time_window_bounds(t, window_s)
    finite = np.isfinite(values)
    safe_values = np.where(finite, values, 0.0)
    sums = np.concatenate(([0.0], np.cumsum(safe_values)))
    counts = np.concatenate(([0], np.cumsum(finite.astype(int))))
    window_sums = sums[right] - sums[left]
    window_counts = counts[right] - counts[left]
    out = np.full(values.shape, np.nan, dtype=float)
    np.divide(window_sums, window_counts, out=out, where=window_counts > 0)
    return out


def time_window_linear_slope(t: np.ndarray, values: np.ndarray, window_s: float) -> np.ndarray:
    """Fit a centered local slope; insufficient temporal support is NaN, not zero."""
    t = np.asarray(t, dtype=float)
    values = np.asarray(values, dtype=float)
    if t.ndim != 1 or values.shape != t.shape:
        raise ValueError("Slope requires matching one-dimensional time and value arrays")
    if not np.isfinite(window_s) or window_s <= 0.0:
        raise ValueError("Slope window must be finite and positive")
    if not np.all(np.isfinite(t)) or np.any(np.diff(t) <= 0.0):
        raise ValueError("Slope timestamps must be finite and strictly increasing")
    out = np.full(values.shape, np.nan, dtype=float)
    half_window = float(window_s) / 2.0
    left, right = _time_window_bounds(t, window_s)
    # Bound blocks in both samples and time, including recordings with long gaps.
    start = 0
    while start < t.size:
        end = min(start + 2048, int(np.searchsorted(t, t[start] + 30.0, side="right")))
        lo, hi = int(left[start]), int(right[end - 1])
        x = t[lo:hi] - t[start]
        y = values[lo:hi]
        finite = np.isfinite(y)
        safe_y = np.where(finite, y, 0.0)
        safe_x = np.where(finite, x, 0.0)
        a, b = left[start:end] - lo, right[start:end] - lo

        def window_sum(data: np.ndarray) -> np.ndarray:
            prefix = np.concatenate(([0.0], np.cumsum(data)))
            return prefix[b] - prefix[a]

        count = window_sum(finite.astype(float))
        sx = window_sum(safe_x)
        sy = window_sum(safe_y)
        sxx = window_sum(safe_x * safe_x)
        sxy = window_sum(safe_x * safe_y)
        denominator = count * sxx - sx * sx
        indices = np.arange(hi - lo)
        first = np.minimum.accumulate(np.where(finite, indices, hi - lo)[::-1])[::-1]
        last = np.maximum.accumulate(np.where(finite, indices, -1))
        first_in_window, last_in_window = first[a], last[b - 1]
        supported = (count >= 3) & (last_in_window >= first_in_window)
        span = np.zeros(end - start)
        span[supported] = x[last_in_window[supported]] - x[first_in_window[supported]]
        supported &= (span >= half_window - 1e-9) & (denominator > 0.0)
        np.divide(
            count * sxy - sx * sy, denominator,
            out=out[start:end], where=supported,
        )
        start = end
    return out


def jerk_from_acceleration(t: np.ndarray, acceleration: np.ndarray) -> np.ndarray:
    smoothed_acceleration = time_window_moving_average(t, acceleration, JERK_SMOOTHING_WINDOW_S)
    return time_window_linear_slope(t, smoothed_acceleration, JERK_REGRESSION_WINDOW_S)


def sustained_jerk(
    timestamps: Sequence[float],
    acceleration: Sequence[float],
    *legacy_sample_windows: int,
) -> Tuple[list[float], list[float]]:
    """Adapt list-based MPI callers without a second smoothing or a shifted axis."""
    if any(window != 5 for window in legacy_sample_windows):
        raise ValueError(
            "Sample-count smoothing overrides are no longer supported; "
            "MMT jerk uses fixed 0.3 s acceleration and regression windows"
        )
    t = np.asarray(timestamps, dtype=float)
    values = np.asarray(acceleration, dtype=float)
    jerk = jerk_from_acceleration(t, values)
    if t.size and not np.any(np.isfinite(jerk)):
        warnings.warn("Insufficient temporal support for MMT jerk; no valid fits", UserWarning, stacklevel=2)
    smoothed = time_window_moving_average(t, values, JERK_SMOOTHING_WINDOW_S)
    return smoothed.tolist(), jerk.tolist()


def jerk_extreme_with_time(
    acc_with_time: Iterable[Tuple[Optional[float], Optional[float]]],
    mode: str,
) -> Optional[Tuple[float, float]]:
    """Adapt Topic CPI input/output in milliseconds, retaining the last duplicate."""
    if mode not in {"min", "max"}:
        raise ValueError("Jerk extreme mode must be min or max")
    points = {}
    for acceleration, timestamp_ms in acc_with_time:
        if acceleration is None or timestamp_ms is None:
            continue
        if not isinstance(acceleration, (int, float)) or not isinstance(timestamp_ms, (int, float)):
            continue
        if np.isfinite(acceleration) and np.isfinite(timestamp_ms) and timestamp_ms > 0:
            points[float(timestamp_ms)] = float(acceleration)
    times_ms = np.array(sorted(points), dtype=float)
    acceleration = np.array([points[time_ms] for time_ms in times_ms], dtype=float)
    jerk = jerk_from_acceleration(times_ms / 1000.0, acceleration)
    finite = np.flatnonzero(np.isfinite(jerk))
    if not finite.size:
        warnings.warn(
            "Insufficient temporal support for MMT jerk: "
            "need 3 finite samples spanning at least 0.15 s in a 0.3 s fit window",
            UserWarning, stacklevel=2,
        )
        return None
    choose = np.argmin if mode == "min" else np.argmax
    index = int(finite[choose(jerk[finite])])
    return float(jerk[index]), float(times_ms[index])
