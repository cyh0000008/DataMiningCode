from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from signal_preprocessor import SignalFrame


JERK_SMOOTHING_WINDOW_S = 0.3
JERK_REGRESSION_WINDOW_S = 0.3
KPH_PER_MPS = 3.6
FOLLOW_STOP_FRONT_STOP_SPEED_MPS = 0.2
STOP2GO_START_TARGET_SPEED_MPS = 3.33
STOP2GO_ACCEL_BUILD_START_THRESHOLD_MPS2 = 0.2


@dataclass(frozen=True)
class Segment:
    start: int
    end: int

    def duration(self, t: np.ndarray) -> float:
        return float(t[self.end] - t[self.start]) if self.end >= self.start else 0.0

    def sample_count(self) -> int:
        return int(self.end - self.start + 1)


@dataclass
class MetricEvent:
    start_s: float
    end_s: float
    severity: float
    message: str
    values: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass
class MetricResult:
    metric_name: str
    category: str
    result_mode: str
    source_path: str
    distance_km: Optional[float]
    distance_status: str
    total_cases: int
    bad_cases: int
    severity_score: float
    events: List[MetricEvent]
    details: List[str]
    runtime_s: float = 0.0
    error: Optional[str] = None
    status: Optional[str] = None
    missing_fields: List[str] = field(default_factory=list)
    missing_signals: Dict[str, str] = field(default_factory=dict)


class BaseMetric:
    name = "base_metric"
    category = "CPI"
    result_mode = "case_rate"
    required_fields: Sequence[str] = ()
    powertrain_type = "PHEV"

    def run(self, frame: SignalFrame) -> MetricResult:
        start = time.time()
        required = metric_required_fields(self)
        missing = [name for name in required if not frame.has(name)]
        if missing:
            missing_signal_details = frame.missing_signal_details(missing)
            missing_text = format_missing_signal_mapping(missing_signal_details)
            return MetricResult(
                metric_name=self.name,
                category=self.category,
                result_mode=self.result_mode,
                source_path=frame.source_path,
                distance_km=frame.distance_km,
                distance_status=frame.distance_status,
                total_cases=0,
                bad_cases=0,
                severity_score=0.0,
                events=[],
                details=[
                    "skipped: missing fields: %s" % ", ".join(missing),
                    "missing signals: %s" % missing_text,
                ],
                runtime_s=time.time() - start,
                error="SKIPPED_MISSING_SIGNALS",
                status="SKIPPED",
                missing_fields=missing,
                missing_signals=missing_signal_details,
            )
        try:
            result = self.evaluate(frame)
            result.result_mode = self.result_mode
            result.distance_km = frame.distance_km
            result.distance_status = frame.distance_status
            result.runtime_s = time.time() - start
            return result
        except Exception as exc:
            return MetricResult(
                metric_name=self.name,
                category=self.category,
                result_mode=self.result_mode,
                source_path=frame.source_path,
                distance_km=frame.distance_km,
                distance_status=frame.distance_status,
                total_cases=0,
                bad_cases=0,
                severity_score=0.0,
                events=[],
                details=["metric failed: %s" % exc],
                runtime_s=time.time() - start,
                error=str(exc),
                status="ERROR",
            )

    def evaluate(self, frame: SignalFrame) -> MetricResult:
        raise NotImplementedError


SCENE_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "follow_stop": (
        "time_s", "front_ttc", "front_valid", "moving_flag", "acc_active_flag",
        "ego_speed_mps", "stationary_flag", "front_range", "front_velocity_abs",
    ),
    "follow_start": (
        "time_s", "stationary_flag", "ego_speed_mps", "front_velocity_abs", "front_valid", "acc_active_flag",
    ),
    "stop2go": (
        "time_s", "stationary_flag", "moving_flag", "acc_active_flag", "front_ttc",
        "curv_enable_flag", "curv_speed_limit", "ego_speed_mps", "acceleration_total", "set_speed",
    ),
    "slope_no_front": (
        "time_s", "ego_speed_mps", "set_speed", "acc_active_flag", "moving_flag",
        "slope_acc", "front_valid", "curv_speed_limit",
    ),
    "curvature": (
        "time_s", "curvature", "front_ttc", "acc_active_flag", "curv_enable_flag",
        "moving_flag", "ego_speed_mps",
    ),
}


MEASURE_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "min_jerk": ("time_s", "acceleration_total"),
    "max_jerk": ("time_s", "acceleration_total"),
    "max_abs_jerk": ("time_s", "acceleration_total"),
    "min_acc": ("time_s", "acceleration_total"),
    "max_acc": ("time_s", "acceleration_total"),
    "max_abs_acc": ("time_s", "acceleration_total"),
    "min_acc_dynamic": ("time_s", "acceleration_total", "ego_speed_mps"),
    "max_acc_time": ("time_s", "acceleration_total"),
    "start_time": ("time_s", "ego_speed_mps"),
    "accel_time_0p5_to_8": ("time_s", "ego_speed_mps"),
    "min_ttc": ("time_s", "front_ttc"),
    "min_ttc_front_decel": ("time_s", "front_ttc", "front_accel"),
    "min_front_distance": ("time_s", "front_range"),
    "stop_front_distance": (
        "time_s", "front_range", "stationary_flag", "front_valid",
        "front_target_id", "front_velocity_abs", "acc_active_flag",
    ),
    "speed_inflections": ("time_s", "ego_speed_mps"),
    "velocity_fluct": ("time_s", "ego_speed_mps"),
    "start_latency": ("time_s", "ego_speed_mps"),
    "curvature_min_speed": ("time_s", "ego_speed_mps", "curvature_radius", "set_speed"),
}


def metric_required_fields(metric: BaseMetric) -> List[str]:
    fields: List[str] = list(metric.required_fields)
    scene = getattr(metric, "scene", None)
    if scene:
        fields.extend(SCENE_REQUIRED_FIELDS.get(str(scene), ()))
    measure = getattr(metric, "measure", None)
    if measure:
        fields.extend(MEASURE_REQUIRED_FIELDS.get(str(measure), ()))
    return list(dict.fromkeys(fields))


def format_missing_signal_mapping(missing_signals: Mapping[str, str]) -> str:
    if not missing_signals:
        return ""
    return "; ".join("%s=%s" % (field_name, signal_path) for field_name, signal_path in missing_signals.items())


def frame_cached(frame: SignalFrame, key: Tuple[Any, ...], factory: Callable[[], Any]) -> Any:
    cache = getattr(frame, "runtime_cache", None)
    if cache is None:
        return factory()
    if key not in cache:
        cache[key] = factory()
    return cache[key]


def result_from_events(
    name: str,
    category: str,
    frame: SignalFrame,
    total_cases: int,
    bad_events: List[MetricEvent],
    result_mode: str = "case_rate",
) -> MetricResult:
    severity = max((event.severity for event in bad_events), default=0.0)
    details = [event.message for event in bad_events]
    return MetricResult(
        metric_name=name,
        category=category,
        result_mode=result_mode,
        source_path=frame.source_path,
        distance_km=frame.distance_km,
        distance_status=frame.distance_status,
        total_cases=int(total_cases),
        bad_cases=len(bad_events),
        severity_score=round(float(severity), 3),
        events=bad_events,
        details=details,
    )


def event_from_segment(
    frame: SignalFrame,
    seg: Segment,
    severity: float,
    message: str,
    values: Optional[Mapping[str, Any]] = None,
) -> MetricEvent:
    payload = {"start_index": seg.start, "end_index": seg.end, "sample_count": seg.sample_count()}
    primary_length = frame.primary_length
    if primary_length is not None and seg.end >= primary_length:
        payload["cross_h5"] = True
        payload["stitched_source_paths"] = list(frame.stitched_source_paths)
    if values:
        payload.update(dict(values))
    return MetricEvent(
        start_s=float(frame.time_s[seg.start]),
        end_s=float(frame.time_s[seg.end]),
        severity=round(float(severity), 3),
        message=message,
        values=payload,
    )


def enum_mask(values: np.ndarray, active_values: Iterable[int]) -> np.ndarray:
    active = set(int(item) for item in active_values)
    out = np.zeros(values.shape, dtype=bool)
    for idx, value in enumerate(values):
        try:
            out[idx] = int(float(value)) in active
        except (TypeError, ValueError):
            out[idx] = str(value).strip().lower() in {str(item) for item in active}
    return out


def mask_to_segments(t: np.ndarray, mask: np.ndarray) -> List[Segment]:
    if mask.size == 0:
        return []
    segments: List[Segment] = []
    start: Optional[int] = None
    for idx, flag in enumerate(mask.astype(bool)):
        if flag and start is None:
            start = idx
        elif not flag and start is not None:
            segments.append(Segment(start, idx - 1))
            start = None
    if start is not None:
        segments.append(Segment(start, mask.size - 1))
    return segments


def merge_short_gaps(t: np.ndarray, mask: np.ndarray, max_gap_s: float) -> np.ndarray:
    merged = mask.astype(bool).copy()
    idx = 0
    while idx < merged.size:
        if merged[idx]:
            idx += 1
            continue
        gap_start = idx
        while idx < merged.size and not merged[idx]:
            idx += 1
        gap_end = idx - 1
        left = gap_start - 1
        right = idx
        if left >= 0 and right < merged.size and merged[left] and merged[right]:
            if t[right] - t[left] <= max_gap_s:
                merged[gap_start : gap_end + 1] = True
    return merged


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    values = values.astype(float)
    if window <= 1 or values.size <= 2:
        return values.copy()
    kernel = np.ones(int(window), dtype=float) / float(window)
    pad = int(window) // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    return np.convolve(padded, kernel, mode="valid")[: values.size]


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


def derivative(t: np.ndarray, values: np.ndarray) -> np.ndarray:
    if values.size < 2:
        return np.zeros_like(values, dtype=float)
    out = np.zeros_like(values, dtype=float)
    dt = np.diff(t)
    dv = np.diff(values)
    diff = np.divide(dv, dt, out=np.zeros_like(dv), where=np.abs(dt) > 1e-9)
    out[1:] = diff
    out[0] = out[1] if out.size > 1 else 0.0
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


def jerkiness_events(frame: SignalFrame, valid: np.ndarray, accel: np.ndarray) -> List[MetricEvent]:
    t = frame.time_s
    smoothed = time_window_moving_average(t, accel, JERK_SMOOTHING_WINDOW_S)
    jerk = jerk_from_acceleration(t, accel)
    signed = np.where(jerk > 0.8, 1, np.where(jerk < -0.8, -1, 0))
    candidate = np.logical_and(valid, signed != 0)
    # Preserve interruptions; do not bridge inactive or non-candidate samples.
    events: List[MetricEvent] = []
    for seg in mask_to_segments(t, candidate):
        duration = seg.duration(t)
        if duration <= 0.5 or duration > 4.0:
            continue
        acc_window = smoothed[seg.start : seg.end + 1]
        sign_window = signed[seg.start : seg.end + 1]
        amplitude = float(np.nanmax(acc_window) - np.nanmin(acc_window)) if acc_window.size else 0.0
        sign_flips = count_sign_flips(sign_window)
        if amplitude < 0.75 or sign_flips < 1:
            continue
        severity = round((amplitude / 0.75) * 50.0 + sign_flips * 30.0 + duration * 20.0, 3)
        events.append(
            event_from_segment(
                frame,
                seg,
                severity,
                "acceleration jerkiness amplitude=%.3f sign_flips=%d" % (amplitude, sign_flips),
                {"amplitude": round(amplitude, 3), "sign_flips": sign_flips},
            )
        )
    return events


def count_sign_flips(signs: np.ndarray) -> int:
    previous = 0
    count = 0
    for sign in signs:
        sign = int(sign)
        if sign == 0:
            continue
        if previous != 0 and sign != previous:
            count += 1
        previous = sign
    return count


def post_override_release_mask(frame: SignalFrame, window_s: float) -> np.ndarray:
    return frame_cached(
        frame,
        ("post_override_release_mask", float(window_s)),
        lambda: _post_override_release_mask_uncached(frame, window_s),
    )


def _post_override_release_mask_uncached(frame: SignalFrame, window_s: float) -> np.ndarray:
    pedal = frame.col("pedal_override_flag").astype(bool)
    t = frame.time_s
    mask = np.zeros(frame.length, dtype=bool)
    release_indices = np.where(np.logical_and(pedal[:-1], np.logical_not(pedal[1:])))[0] + 1
    for idx in release_indices:
        end = np.searchsorted(t, t[idx] + window_s, side="right")
        mask[idx:end] = True
    return mask


def acc_activation_start_mask(frame: SignalFrame, window_s: float) -> np.ndarray:
    return frame_cached(
        frame,
        ("acc_activation_start_mask", float(window_s)),
        lambda: _acc_activation_start_mask_uncached(frame, window_s),
    )


def _acc_activation_start_mask_uncached(frame: SignalFrame, window_s: float) -> np.ndarray:
    active = frame.col("acc_active_flag").astype(bool)
    t = frame.time_s
    mask = np.zeros(frame.length, dtype=bool)
    starts = np.where(np.logical_and(np.logical_not(active[:-1]), active[1:]))[0] + 1
    for idx in starts:
        end = np.searchsorted(t, t[idx] + window_s, side="right")
        mask[idx:end] = True
    return mask


def find_scene_segments(frame: SignalFrame, scene: str) -> List[Segment]:
    key = ("scene_segments", str(scene), frame.primary_length, frame.has_continuous_previous)
    return list(frame_cached(frame, key, lambda: _find_scene_segments_uncached(frame, scene)))


def _find_scene_segments_uncached(frame: SignalFrame, scene: str) -> List[Segment]:
    if scene == "follow_stop":
        return filter_segments_for_primary(frame, follow_stop_segments(frame))
    if scene == "follow_start":
        return filter_segments_for_primary(frame, follow_start_segments(frame))
    if scene == "stop2go":
        return filter_segments_for_primary(frame, stop2go_segments(frame))
    if scene == "slope_no_front":
        return slope_no_front_segments(frame)
    if scene == "curvature":
        return filter_segments_for_primary(frame, curvature_segments(frame))
    raise KeyError("Unknown scene: %s" % scene)


def filter_segments_for_primary(frame: SignalFrame, segments: List[Segment]) -> List[Segment]:
    primary_length = frame.primary_length if frame.primary_length is not None else frame.length
    filtered = []
    for seg in segments:
        if seg.start >= primary_length:
            continue
        if frame.has_continuous_previous and seg.start == 0:
            continue
        filtered.append(seg)
    return filtered


def follow_stop_segments(frame: SignalFrame) -> List[Segment]:
    min_duration_s = 5.0
    ttc = frame.col("front_ttc", np.inf)
    front_valid = frame.col("front_valid", 0).astype(bool)
    moving = frame.col("moving_flag", 0).astype(bool)
    acc_active = frame.col("acc_active_flag", 0).astype(bool)
    speed = frame.col("ego_speed_mps", 0.0).astype(float)
    front_velocity = frame.col("front_velocity_abs").astype(float)
    front_stopped = np.logical_and(
        np.isfinite(front_velocity),
        np.abs(front_velocity) <= FOLLOW_STOP_FRONT_STOP_SPEED_MPS,
    )
    target_present = np.logical_or(ttc < 30.0, frame.col("front_range", np.inf) < 80.0)
    follow_mask = np.logical_and.reduce([acc_active, front_valid, target_present])
    candidates = mask_to_segments(frame.time_s, np.logical_and(follow_mask, moving))
    result: List[Segment] = []
    stationary = frame.col("stationary_flag", 0).astype(bool)
    for seg in candidates:
        if speed[seg.start] <= 2.0:
            continue

        episode_end = seg.end
        while episode_end + 1 < frame.length and follow_mask[episode_end + 1]:
            episode_end += 1

        stop_hits = np.where(
            np.logical_and(
                stationary[seg.end : episode_end + 1],
                front_stopped[seg.end : episode_end + 1],
            )
        )[0]
        if not stop_hits.size:
            continue
        end = seg.end + int(stop_hits[0])
        if frame.time_s[end] - frame.time_s[seg.start] < min_duration_s:
            continue
        result.append(Segment(seg.start, end))
    return merge_overlapping_segments(result)


def stop2go_segments(frame: SignalFrame) -> List[Segment]:
    min_duration_s = 5.0
    min_peak_speed_mps = 10.0 / KPH_PER_MPS
    stationary = frame.col("stationary_flag", 0).astype(bool)
    moving = frame.col("moving_flag", 0).astype(bool)
    acc_active = frame.col("acc_active_flag", 0).astype(bool)
    ttc = frame.col("front_ttc", np.inf)
    speed = frame.col("ego_speed_mps", 0.0).astype(float)
    acceleration = frame.col("acceleration_total").astype(float)
    set_speed_kph = frame.col("set_speed").astype(float)
    set_speed_stable = np.zeros(frame.length, dtype=bool)
    set_speed_stable[1:] = np.abs(np.diff(set_speed_kph)) <= 1e-6
    valid = np.logical_and.reduce([
        np.isfinite(acceleration),
        acceleration >= 0.0,
        np.isfinite(set_speed_kph),
        set_speed_stable,
        np.isfinite(speed),
        speed <= set_speed_kph / KPH_PER_MPS,
        np.logical_not(np.isfinite(ttc) & (ttc < 4.5)),
    ])
    curv_limited = curvature_speed_limited_mask(frame, 20.0)

    segments: List[Segment] = []
    for idx in range(1, frame.length):
        if not (stationary[idx - 1] and moving[idx] and acc_active[idx]):
            continue
        if curv_limited[idx] or not valid[idx]:
            continue
        end = idx
        while end + 1 < frame.length:
            if not valid[end + 1]:
                break
            end += 1
            if speed[end] >= 8.0:
                break
            if not acc_active[end] or curv_limited[end]:
                break
        if (
            frame.time_s[end] - frame.time_s[idx] >= min_duration_s
            and np.nanmax(speed[idx : end + 1]) >= min_peak_speed_mps
        ):
            segments.append(Segment(idx, end))
    return merge_overlapping_segments(segments)


def slope_no_front_segments(frame: SignalFrame) -> List[Segment]:
    min_duration_s = 5.0
    speed = frame.col("ego_speed_mps", 0.0).astype(float)
    set_speed = frame.col("set_speed", np.inf).astype(float)
    mask = np.logical_and.reduce(
        [
            frame.col("acc_active_flag", 0).astype(bool),
            frame.col("moving_flag", 0).astype(bool),
            np.abs(frame.col("slope_acc", 0.0).astype(float)) >= 0.1,
            np.logical_not(frame.col("front_valid", 0).astype(bool)),
            frame.col("curv_speed_limit", 100.0).astype(float) >= speed,
            speed <= np.maximum(set_speed / 3.6, 0.0) + 3.0,
            np.isfinite(speed),
            np.isfinite(set_speed),
            set_speed > 0.0,
        ]
    )
    segments: List[Segment] = []
    reference = None
    candidate_start = None
    dwell_start = None
    steady_start = None
    for i in range(len(frame.time_s) + 1):
        valid = i < len(frame.time_s) and mask[i]
        changed = valid and reference is not None and abs(set_speed[i] - reference) > 1e-6
        if not valid or changed:
            if steady_start is not None:
                segment = Segment(steady_start, i - 1)
                primary_length = frame.primary_length if frame.primary_length is not None else frame.length
                owned = (
                    candidate_start < primary_length
                    and not (frame.has_continuous_previous and candidate_start == 0)
                )
                if owned and segment.duration(frame.time_s) >= min_duration_s - 1e-9:
                    segments.append(segment)
            reference = dwell_start = steady_start = None
        if not valid:
            continue
        if reference is None:
            reference = set_speed[i]
            candidate_start = i
        if steady_start is not None:
            continue
        if abs(speed[i] - set_speed[i] / 3.6) <= 2.0 / 3.6 + 1e-9:
            if dwell_start is None:
                dwell_start = i
            if frame.time_s[i] - frame.time_s[dwell_start] >= 1.0 - 1e-9:
                steady_start = i
        else:
            dwell_start = None
    return segments


def curvature_segments(frame: SignalFrame) -> List[Segment]:
    min_speed_mps = 10.0 / KPH_PER_MPS
    curvature = frame.col("curvature", 0.0).astype(float)
    ttc = frame.col("front_ttc", np.inf)
    speed = frame.col("ego_speed_mps", 0.0).astype(float)
    mask = np.logical_and.reduce(
        [
            frame.col("acc_active_flag", 0).astype(bool),
            frame.col("curv_enable_flag", 0).astype(bool),
            frame.col("moving_flag", 0).astype(bool),
            speed >= min_speed_mps,
            np.logical_not(np.logical_and(np.isfinite(ttc), ttc < 4.5)),
        ]
    )
    segments = []
    for seg in mask_to_segments(frame.time_s, mask):
        if seg.sample_count() < 50:
            continue
        if int(np.sum(curvature[seg.start : seg.end + 1] > 0.004)) < 5:
            continue
        segments.append(seg)
    return segments


def follow_start_segments(frame: SignalFrame) -> List[Segment]:
    observation_window_s = 8.0
    front_start_window_s = 1.0
    ego_stationary = frame.col("stationary_flag", 0).astype(bool)
    ego_speed = frame.col("ego_speed_mps", 0.0).astype(float)
    front_vel = frame.col("front_velocity_abs", 0.0).astype(float)
    front_valid = frame.col("front_valid", 0).astype(bool)
    acc_active = frame.col("acc_active_flag", 0).astype(bool)
    start_process_mask = np.logical_and.reduce([ego_stationary, acc_active, front_valid, np.isfinite(front_vel)])
    segments: List[Segment] = []
    for process_seg in mask_to_segments(frame.time_s, start_process_mask):
        start = process_seg.start
        while start <= process_seg.end:
            if front_vel[start] > 0.2:
                start += 1
                continue

            window_end = int(np.searchsorted(frame.time_s, frame.time_s[start] + front_start_window_s, side="right") - 1)
            window_end = min(process_seg.end, max(start, window_end))
            start_hits = np.where(front_vel[start + 1 : window_end + 1] > 0.5)[0]
            if not start_hits.size:
                start += 1
                continue

            idx = start + 1 + int(start_hits[0])
            observation_end = int(np.searchsorted(frame.time_s, frame.time_s[idx] + observation_window_s, side="right") - 1)
            observation_end = min(frame.length - 1, max(idx, observation_end))
            ego_move_hits = np.where(ego_speed[idx : observation_end + 1] > 0.2)[0]
            end = idx + int(ego_move_hits[0]) if ego_move_hits.size else observation_end
            segments.append(Segment(idx, end))
            start = idx + 1
    return merge_overlapping_segments(segments)


def curvature_speed_limited_mask(frame: SignalFrame, margin_kph: float) -> np.ndarray:
    curv_on = frame.col("curv_enable_flag", 0).astype(bool)
    curv_speed_limit_mps = frame.col("curv_speed_limit", np.inf).astype(float)
    speed_mps = frame.col("ego_speed_mps", 0.0).astype(float)
    margin_mps = float(margin_kph) / KPH_PER_MPS
    return np.logical_and.reduce(
        [
            curv_on,
            np.isfinite(curv_speed_limit_mps),
            curv_speed_limit_mps < speed_mps + margin_mps,
        ]
    )


def merge_overlapping_segments(segments: List[Segment]) -> List[Segment]:
    if not segments:
        return []
    ordered = sorted(segments, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for seg in ordered[1:]:
        last = merged[-1]
        if seg.start <= last.end:
            merged[-1] = Segment(last.start, max(last.end, seg.end))
        else:
            merged.append(seg)
    return merged


def measure_segment(frame: SignalFrame, seg: Segment, measure: str) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    full_t = frame.time_s
    t = full_t[seg.start : seg.end + 1]
    extra: Dict[str, Any] = {"scene_duration_s": round(seg.duration(frame.time_s), 3)}

    def acceleration_values() -> np.ndarray:
        return frame.col("acceleration_total", np.nan).astype(float, copy=False)

    def acceleration_window() -> np.ndarray:
        return acceleration_values()[seg.start : seg.end + 1]

    def jerk_values() -> np.ndarray:
        return frame_cached(
            frame,
            ("jerk_from_acceleration", "acceleration_total"),
            lambda: jerk_from_acceleration(full_t, acceleration_values()),
        )

    def jerk_window() -> np.ndarray:
        return jerk_values()[seg.start : seg.end + 1]

    def speed_values() -> np.ndarray:
        return frame.col("ego_speed_mps", np.nan).astype(float, copy=False)

    def speed_window() -> np.ndarray:
        return speed_values()[seg.start : seg.end + 1]

    if measure == "min_jerk":
        return min_with_time(t, jerk_window(), extra)
    if measure == "max_jerk":
        return max_with_time(t, jerk_window(), extra)
    if measure == "max_abs_jerk":
        values = jerk_window()
        if values.size == 0 or np.all(np.isnan(values)):
            return None, None, extra
        idx = int(np.nanargmax(np.abs(values)))
        return float(abs(values[idx])), float(t[idx]), extra
    if measure == "min_acc":
        return min_with_time(t, acceleration_window(), extra)
    if measure == "max_acc":
        return max_with_time(t, acceleration_window(), extra)
    if measure == "max_abs_acc":
        values = acceleration_window()
        if values.size == 0 or np.all(np.isnan(values)):
            return None, None, extra
        idx = int(np.nanargmax(np.abs(values)))
        return float(abs(values[idx])), float(t[idx]), extra
    if measure == "min_acc_dynamic":
        value, when, extra = min_with_time(t, acceleration_window(), extra)
        if value is None:
            return None, when, extra
        start_speed = float(speed_values()[seg.start])
        if not np.isfinite(start_speed):
            return None, when, extra
        lower = max(min(-start_speed / 5.5, -1.75), -4.0) - 1.0
        extra["dynamic_lower"] = round(lower, 3)
        return value, when, extra
    if measure == "max_acc_time":
        values = acceleration_window()
        if values.size == 0 or np.all(np.isnan(values)):
            return None, None, extra
        start_hits = np.where(values > STOP2GO_ACCEL_BUILD_START_THRESHOLD_MPS2)[0]
        if start_hits.size == 0:
            return None, None, extra
        start_idx = int(start_hits[0])
        candidate = values[start_idx:]
        if candidate.size == 0 or np.all(np.isnan(candidate)):
            return None, None, extra
        max_idx = start_idx + int(np.nanargmax(candidate))
        when = float(t[max_idx])
        start_when = float(t[start_idx])
        extra["accel_build_start_threshold_mps2"] = STOP2GO_ACCEL_BUILD_START_THRESHOLD_MPS2
        extra["accel_build_start_time_s"] = round(start_when, 3)
        return when - start_when, when, extra
    if measure == "start_time":
        target_speed_mps = STOP2GO_START_TARGET_SPEED_MPS
        window = speed_window()
        hits = np.where(window >= target_speed_mps)[0]
        if hits.size == 0:
            return None, None, extra
        when = float(t[int(hits[0])])
        extra["target_speed_mps"] = round(target_speed_mps, 3)
        extra["target_speed_kph"] = round(target_speed_mps * KPH_PER_MPS, 3)
        return when - float(t[0]), when, extra
    if measure == "accel_time_0p5_to_8":
        window = speed_window()
        start_hits = np.where(window > 0.5)[0]
        end_hits = np.where(window > 8.0)[0]
        if start_hits.size == 0 or end_hits.size == 0:
            return None, None, extra
        start_idx = int(start_hits[0])
        end_candidates = end_hits[end_hits >= start_idx]
        if end_candidates.size == 0:
            return None, None, extra
        end_idx = int(end_candidates[0])
        return float(t[end_idx] - t[start_idx]), float(t[end_idx]), extra
    if measure == "min_ttc":
        return min_with_time(t, frame.col("front_ttc", np.inf)[seg.start : seg.end + 1], extra)
    if measure == "min_ttc_front_decel":
        front_accel = frame.col("front_accel", 0.0).astype(float)[seg.start : seg.end + 1]
        ttc = frame.col("front_ttc", np.inf)[seg.start : seg.end + 1].astype(float)
        valid = front_accel < -0.5
        if not np.any(valid):
            return None, None, extra
        idx = int(np.nanargmin(np.where(valid, ttc, np.inf)))
        return float(ttc[idx]), float(t[idx]), extra
    if measure == "min_front_distance":
        distance = frame.col("front_range", np.inf).astype(float)[seg.start : seg.end + 1]
        return min_with_time(t, distance, extra)
    if measure == "stop_front_distance":
        stop_time = float(full_t[seg.end])
        end_time = stop_time + 2.0
        extra.update(
            stop_time_s=stop_time, observation_end_time_s=end_time,
            stable_window_start_time_s=stop_time + 1.0,
            stable_window_end_time_s=end_time,
            distance_sample="median_1_to_2_seconds_after_stop",
        )
        end = int(np.searchsorted(full_t, end_time - 1e-9, side="left"))
        reason = None
        if end >= frame.length:
            reason = "刹停后不足完整2秒数据"
        else:
            window = slice(seg.end, end + 1)
            targets = frame.col("front_target_id")[window]
            distance = frame.col("front_range")[window].astype(float)
            velocity = frame.col("front_velocity_abs")[window].astype(float)
            if np.any(np.diff(full_t[window]) > 0.2 + 1e-9):
                reason = "观察窗口存在超过0.2秒采样缺口"
            elif not np.all(frame.col("stationary_flag")[window]):
                reason = "观察窗口自车重新移动"
            elif not np.all(frame.col("acc_active_flag")[window]):
                reason = "观察窗口ACC退出"
            elif not np.all(frame.col("front_valid")[window]) or not np.all(np.isfinite(targets)) or targets[0] == 0:
                reason = "观察窗口前车目标丢失或无效"
            elif not np.all(targets == targets[0]):
                reason = "观察窗口前车目标切换"
            elif not np.all(np.isfinite(velocity)):
                reason = "观察窗口前车速度无效"
            elif np.any(np.abs(velocity) > FOLLOW_STOP_FRONT_STOP_SPEED_MPS):
                reason = "观察窗口前车未静止或重新起步"
            elif not np.all(np.isfinite(distance)) or np.any(distance <= 0):
                reason = "观察窗口前车距离无效"
            else:
                stable = (
                    (full_t >= stop_time + 1.0 - 1e-9)
                    & (full_t <= end_time + 1e-9)
                )
                if np.count_nonzero(stable) < 3:
                    reason = "稳定取值窗口有效采样不足3个"
                else:
                    extra["observation_end_index"] = end
                    extra["stable_sample_count"] = int(np.count_nonzero(stable))
                    return float(np.median(frame.col("front_range")[stable])), end_time, extra
        extra["excluded_reason"] = reason
        return None, None, extra
    if measure == "speed_inflections":
        return float(count_speed_inflections(t, speed_window())), None, extra
    if measure == "velocity_fluct":
        window = speed_window()
        if window.size == 0:
            return None, None, extra
        speed_range = float(np.nanmax(window) - np.nanmin(window))
        extra["speed_range_mps"] = round(speed_range, 6)
        return speed_range / 2.0, None, extra
    if measure == "start_latency":
        front_start = float(frame.time_s[seg.start])
        window = speed_window()
        ego_move_hits = np.where(window > 0.2)[0]
        if ego_move_hits.size == 0:
            return None, None, extra
        when = float(t[int(ego_move_hits[0])])
        return when - front_start, when, extra
    if measure == "curvature_min_speed":
        return curvature_min_speed(frame, seg, extra)
    raise KeyError("Unknown measure: %s" % measure)


def min_with_time(t: np.ndarray, values: np.ndarray, extra: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    if values.size == 0 or np.all(np.isnan(values)):
        return None, None, extra
    idx = int(np.nanargmin(values))
    return float(values[idx]), float(t[idx]), extra


def max_with_time(t: np.ndarray, values: np.ndarray, extra: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    if values.size == 0 or np.all(np.isnan(values)):
        return None, None, extra
    idx = int(np.nanargmax(values))
    return float(values[idx]), float(t[idx]), extra


def count_speed_inflections(t: np.ndarray, speed: np.ndarray) -> int:
    if speed.size < 5:
        return 0
    slope = derivative(t, moving_average(speed.astype(float), 50))
    signs = np.where(slope > 0.05, 1, np.where(slope < -0.05, -1, 0))
    return count_sign_flips(signs)


def curvature_min_speed(frame: SignalFrame, seg: Segment, extra: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    speed = frame.col("ego_speed_mps", np.nan).astype(float)[seg.start : seg.end + 1]
    radius = frame.col("curvature_radius", np.inf).astype(float)[seg.start : seg.end + 1]
    set_speed = frame.col("set_speed", np.inf).astype(float)[seg.start : seg.end + 1] / 3.6
    if speed.size == 0:
        return None, None, extra
    idx = int(np.nanargmin(speed))
    min_speed = float(speed[idx])
    min_radius = float(np.nanmin(radius))
    target_set = float(set_speed[idx])
    if not np.isfinite(min_radius) or min_radius <= 0.0:
        return None, None, extra
    set_speed_skip_threshold = math.sqrt(1.0 * min_radius)
    lower = set_speed_skip_threshold - 2.0
    upper = math.sqrt(2.9 * min_radius) + 2.0
    extra["curvature_radius"] = round(min_radius, 3)
    extra["lower_bound"] = round(lower, 3)
    extra["upper_bound"] = round(upper, 3)
    extra["set_speed_mps"] = round(target_set, 3)
    extra["set_speed_skip_threshold_mps"] = round(set_speed_skip_threshold, 3)
    if (lower <= min_speed <= upper) or target_set <= set_speed_skip_threshold:
        return None, float(frame.time_s[seg.start + idx]), extra
    return min_speed, float(frame.time_s[seg.start + idx]), extra


def within_bounds(value: float, lower: Optional[float], upper: Optional[float]) -> bool:
    if lower is not None and value < lower:
        return False
    if upper is not None and value > upper:
        return False
    return True


def severity_outside(value: float, lower: Optional[float], upper: Optional[float], base: float) -> float:
    diffs = []
    if lower is not None and value < lower:
        diffs.append(lower - value)
    if upper is not None and value > upper:
        diffs.append(value - upper)
    if not diffs:
        return 0.0
    return round(max(diffs) / max(base, 1e-6) * 100.0, 3)


BIAS_X = [0.0, 40.0, 80.0, 120.0, 200.0, 300.0]
BIAS_Y = [1.0, 1.075, 1.052, 1.045, 1.039, 1.036]


def display_speed_from_actual(actual_speed: float) -> int:
    speed = max(float(actual_speed), 0.0)
    if speed <= BIAS_X[0]:
        bias = BIAS_Y[0]
    elif speed >= BIAS_X[-1]:
        bias = BIAS_Y[-1]
    else:
        bias = BIAS_Y[-1]
        for idx in range(1, len(BIAS_X)):
            if speed <= BIAS_X[idx]:
                x0, x1 = BIAS_X[idx - 1], BIAS_X[idx]
                y0, y1 = BIAS_Y[idx - 1], BIAS_Y[idx]
                bias = y0 + (y1 - y0) * (speed - x0) / (x1 - x0)
                bias = round_half_up(bias, 3)
                break
    return int(round_half_up(speed * bias, 0))


def display_speed_array(frame: SignalFrame, field_name: str, default: float = np.nan) -> np.ndarray:
    return frame_cached(
        frame,
        ("display_speed_array", str(field_name), _cacheable_default(default)),
        lambda: _display_speed_array_from_values(frame.col(field_name, default).astype(float, copy=False)),
    )


def _display_speed_array_from_values(values: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=float)
    finite_indices = np.where(np.isfinite(values))[0]
    for idx in finite_indices:
        out[idx] = float(display_speed_from_actual(float(values[idx])))
    return out


def _cacheable_default(default: Any) -> Any:
    try:
        return float(default)
    except (TypeError, ValueError):
        return repr(default)


def round_half_up(value: float, digits: int) -> float:
    quantize_str = "1" if digits == 0 else "1." + ("0" * digits)
    decimal_value = Decimal(str(value)).quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
    rounded = float(decimal_value)
    return int(rounded) if digits == 0 else rounded
