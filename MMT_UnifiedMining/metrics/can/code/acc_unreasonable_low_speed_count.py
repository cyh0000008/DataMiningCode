from __future__ import annotations

import re
import time
from bisect import bisect_left
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from signal_lib import FolderSession, mask_to_segments


METRIC_CATEGORY = "MPI"
SPEED_SIGNAL = "PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1"
SET_SPEED_SIGNAL = "Adaptive_Cruise_Disp_Stat.IACCDrvrSeltdSpd"
ACC_ACTIVE_SIGNAL = "Adaptive_Cruise_Disp_Stat.IACCAct376"
PEDAL_OVERRIDE_SIGNAL = "PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv"

DEFAULT_MIN_STABLE_DURATION_SECONDS = 8.0
DEFAULT_STABLE_RANGE_KPH = 1.5
DEFAULT_MIN_SET_SPEED_GAP_KPH = 8.0
DEFAULT_MIN_MOVING_DISPLAY_SPEED_KPH = 5.0
DEFAULT_TOPIC_MATCH_TOLERANCE_MS = 300.0
DEFAULT_TRAFFIC_LIGHT_STOP_MAX_DISTANCE_M = 150.0
DEFAULT_SCENE_MERGE_MAX_GAP_SECONDS = 1.0
DEFAULT_SCENE_MERGE_SET_SPEED_TOLERANCE_KPH = 1.0

FRONT_TARGET_MIN_X_METERS = 1.0
FRONT_TARGET_MAX_X_METERS = 50.0
FRONT_TARGET_MAX_ABS_Y_METERS = 1.5
FRONT_TARGET_MAX_TTC_SECONDS = 8.0
FRONT_TARGET_CLOSE_DISTANCE_METERS = 5.0

BIAS_X = [0.0, 40.0, 80.0, 120.0, 200.0, 300.0]
BIAS_Y = [1.0, 1.075, 1.052, 1.045, 1.039, 1.036]

REQUIRED_SIGNALS = [
    SPEED_SIGNAL,
    SET_SPEED_SIGNAL,
    ACC_ACTIVE_SIGNAL,
    PEDAL_OVERRIDE_SIGNAL,
]

SIGNAL_KEYS = {
    "speed": REQUIRED_SIGNALS[0],
    "set_speed": REQUIRED_SIGNALS[1],
    "acc_active": REQUIRED_SIGNALS[2],
    "pedal_override": REQUIRED_SIGNALS[3],
}

_UNP_PARSE_CACHE: dict[tuple[str, int, int], list[dict[str, object]]] = {}
_FUSION_PARSE_CACHE: dict[tuple[str, int, int], list[dict[str, object]]] = {}
_CROSS_FOLDER_SCENE_MERGE_STATE: dict[str, object] = {}


def __run_haoran_data_run__(folder_context: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    start_time = time.time()
    result_lines: list[str] = []
    stats: dict[str, Any] = {
        "All Case Count": 0,
        "Bad Case": 0,
        "Severity Score": 0.0,
    }

    session = folder_context.get("session")
    if session is None:
        result_lines.append("No usable HDF5 file found in current folder")
        result_lines.insert(0, f"[Total Time] {time.time() - start_time:.4f}s")
        return False, result_lines, stats

    result = analyze_acc_unreasonable_low_speed(
        session,
        folder_context.get("context_folder_paths") or [folder_context.get("folder_path")],
    )
    result = _apply_cross_folder_scene_merge(result, folder_context)
    all_case_count = int(result["all_case_count"])
    bad_case_count = int(result["bad_case_count"])
    severity_score = float(result["severity_score"])
    topic_summary = result.get("topic_summary", {})

    stats["All Case Count"] = all_case_count
    stats["Bad Case"] = bad_case_count
    stats["Severity Score"] = severity_score

    result_lines.append(
        f"[ACC Unreasonable Low Speed] file={Path(session.primary_h5_file).name}, "
        f"all_case_count={all_case_count}, bad_case_count={bad_case_count}, "
        f"severity_score={severity_score:.3f}"
    )
    result_lines.append(
        "[Topic Match] "
        f"unp_file={topic_summary.get('unp_file') or 'missing'}, "
        f"unp_frames={topic_summary.get('unp_frame_count', 0)}, "
        f"traffic_light_stop_frames={topic_summary.get('traffic_light_stop_frame_count', 0)}, "
        f"fusion_file={topic_summary.get('fusion_file') or 'missing'}, "
        f"fusion_frames={topic_summary.get('fusion_frame_count', 0)}, "
        f"tolerance_ms={topic_summary.get('tolerance_ms', DEFAULT_TOPIC_MATCH_TOLERANCE_MS):.0f}"
    )
    skip_reason = str(result.get("skip_reason", "") or "")
    if skip_reason:
        result_lines.append(f"[Skip] {skip_reason}")
    merged_continuation_count = int(result.get("merged_continuation_count", 0) or 0)
    if merged_continuation_count:
        result_lines.append(
            f"[Merged Continuation] suppressed_continuation_scenes={merged_continuation_count}"
        )
    for scene in result["scenes"]:
        result_lines.append(
            f"[Scene start={scene['start_timestamp']:.3f}, end={scene['end_timestamp']:.3f}, "
            f"duration={scene['duration']:.3f}, set_speed={scene['set_speed']:.1f}, "
            f"stable_display_speed={scene['stable_display_speed']:.1f}, "
            f"v_min={scene['v_min_kph']:.1f}, v_max={scene['v_max_kph']:.1f}, "
            f"display_range={scene['display_range']:.1f}, set_speed_gap={scene['set_speed_gap_kph']:.1f}, "
            f"min_curvature_limit={scene['min_curvature_limit_kph']:.1f}, "
            f"max_topic_gap_ms={scene['max_topic_gap_ms']:.1f}, "
            f"max_fusion_gap_ms={scene['max_fusion_gap_ms']:.1f}, "
            f"merged_windows={scene.get('merged_window_count', 1)}, "
            f"low_speed={scene['low_speed']}, severity={scene['severity']:.3f}]"
        )

    result_lines.insert(0, f"[Total Time] {time.time() - start_time:.4f}s")
    return bad_case_count > 0, result_lines, stats


def analyze_acc_unreasonable_low_speed(
    session_or_h5_path: FolderSession | str | Path,
    folder_path: str | Path | list[str | Path] | None = None,
    min_stable_duration_seconds: float = DEFAULT_MIN_STABLE_DURATION_SECONDS,
    stable_range_kph: float = DEFAULT_STABLE_RANGE_KPH,
    min_set_speed_gap_kph: float = DEFAULT_MIN_SET_SPEED_GAP_KPH,
    min_moving_display_speed_kph: float = DEFAULT_MIN_MOVING_DISPLAY_SPEED_KPH,
    topic_match_tolerance_ms: float = DEFAULT_TOPIC_MATCH_TOLERANCE_MS,
    traffic_light_stop_max_distance_m: float = DEFAULT_TRAFFIC_LIGHT_STOP_MAX_DISTANCE_M,
    scene_merge_max_gap_seconds: float = DEFAULT_SCENE_MERGE_MAX_GAP_SECONDS,
) -> dict[str, object]:
    session = session_or_h5_path if isinstance(session_or_h5_path, FolderSession) else FolderSession(session_or_h5_path)
    source_folders = _normalize_source_folders(folder_path, Path(session.primary_h5_file).parent)
    topic_context = _load_topic_context(source_folders, topic_match_tolerance_ms)
    start_timestamp, end_timestamp = session.common_time_range(REQUIRED_SIGNALS)
    if not topic_context["unp_frames"]:
        return _empty_result(
            start_timestamp,
            end_timestamp,
            min_stable_duration_seconds,
            stable_range_kph,
            min_set_speed_gap_kph,
            min_moving_display_speed_kph,
            topic_match_tolerance_ms,
            traffic_light_stop_max_distance_m,
            scene_merge_max_gap_seconds,
            topic_context,
            skip_reason="missing _unp_planning_info.txt or no parseable unp frames",
        )

    axis, speed_raw = session.get_signal_slice(SIGNAL_KEYS["speed"], start_timestamp, end_timestamp)
    if len(axis) < 2:
        return _empty_result(
            start_timestamp,
            end_timestamp,
            min_stable_duration_seconds,
            stable_range_kph,
            min_set_speed_gap_kph,
            min_moving_display_speed_kph,
            topic_match_tolerance_ms,
            traffic_light_stop_max_distance_m,
            scene_merge_max_gap_seconds,
            topic_context,
        )

    actual_speed = [float(value) for value in speed_raw]
    display_speed = [_display_speed_from_actual(speed) for speed in actual_speed]
    set_speed_raw = [
        float(value)
        for value in session.sample_signal_to_axis(
            SIGNAL_KEYS["set_speed"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
        )
    ]
    set_speed = [_display_speed_from_actual(speed) for speed in set_speed_raw]
    acc_active = session.sample_signal_to_axis(
        SIGNAL_KEYS["acc_active"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )
    pedal_override = session.sample_signal_to_axis(
        SIGNAL_KEYS["pedal_override"], axis, method="nearest", start_timestamp=start_timestamp, end_timestamp=end_timestamp
    )

    base_mask: list[bool] = []
    curvature_limit_kph: list[float] = []
    unp_match_gap_ms: list[float | None] = []
    fusion_match_gap_ms: list[float | None] = []

    unp_frames = topic_context["unp_frames"]
    unp_timestamps = topic_context["unp_timestamps"]
    fusion_frames = topic_context["fusion_frames"]
    fusion_timestamps = topic_context["fusion_timestamps"]

    for timestamp, acc_on, override, selected_speed_raw, selected_speed in zip(
        axis,
        acc_active,
        pedal_override,
        set_speed_raw,
        set_speed,
    ):
        target_time_ms = float(timestamp) * 1000.0
        unp_frame, unp_gap = _nearest_frame(unp_frames, unp_timestamps, target_time_ms, topic_match_tolerance_ms)
        fusion_frame, fusion_gap = _nearest_frame(
            fusion_frames, fusion_timestamps, target_time_ms, topic_match_tolerance_ms
        )
        unp_match_gap_ms.append(unp_gap)
        fusion_match_gap_ms.append(fusion_gap)

        no_front_target = fusion_frame is not None and not bool(fusion_frame.get("has_front_target", False))
        no_traffic_light_stop = unp_frame is not None and not _is_traffic_light_stop(
            unp_frame,
            traffic_light_stop_max_distance_m,
        )
        curve_allows = (
            unp_frame is not None
            and _curve_limit_allows_set_speed(
                bool(unp_frame.get("curvature_limit_enabled", False)),
                float(unp_frame.get("curvature_limit_kph", 0.0)),
                float(selected_speed),
            )
        )

        curvature_limit_kph.append(float(unp_frame.get("curvature_limit_kph", 0.0)) if unp_frame else 0.0)
        base_mask.append(
            _as_bool(acc_on)
            and (not _as_bool(override))
            and selected_speed_raw > 0.0
            and no_front_target
            and no_traffic_light_stop
            and curve_allows
        )

    stable_scenes = _collect_stable_speed_scenes(
        axis=axis,
        base_mask=base_mask,
        display_speed=display_speed,
        set_speed=set_speed,
        curvature_limit_kph=curvature_limit_kph,
        unp_match_gap_ms=unp_match_gap_ms,
        fusion_match_gap_ms=fusion_match_gap_ms,
        min_stable_duration_seconds=min_stable_duration_seconds,
        stable_range_kph=stable_range_kph,
        min_set_speed_gap_kph=min_set_speed_gap_kph,
        min_moving_display_speed_kph=min_moving_display_speed_kph,
    )

    for scene in stable_scenes:
        _score_scene(scene, min_set_speed_gap_kph)
    stable_scenes = _merge_contiguous_scenes(stable_scenes, scene_merge_max_gap_seconds)
    bad_case_count, severity_score = _scene_counts(stable_scenes)

    return {
        "metric_name": "acc_unreasonable_low_speed_count",
        "all_case_count": len(stable_scenes),
        "bad_case_count": bad_case_count,
        "severity_score": round(severity_score, 3),
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "parameters": {
            "min_stable_duration_seconds": min_stable_duration_seconds,
            "stable_range_kph": stable_range_kph,
            "min_set_speed_gap_kph": min_set_speed_gap_kph,
            "min_moving_display_speed_kph": min_moving_display_speed_kph,
            "topic_match_tolerance_ms": topic_match_tolerance_ms,
            "traffic_light_stop_max_distance_m": traffic_light_stop_max_distance_m,
            "scene_merge_max_gap_seconds": scene_merge_max_gap_seconds,
            "front_target_max_ttc_seconds": FRONT_TARGET_MAX_TTC_SECONDS,
            "front_target_close_distance_m": FRONT_TARGET_CLOSE_DISTANCE_METERS,
        },
        "topic_summary": _topic_summary(topic_context),
        "scenes": stable_scenes,
    }


def _collect_stable_speed_scenes(
    axis: list[float],
    base_mask: list[bool],
    display_speed: list[int],
    set_speed: list[int],
    curvature_limit_kph: list[float],
    unp_match_gap_ms: list[float | None],
    fusion_match_gap_ms: list[float | None],
    min_stable_duration_seconds: float,
    stable_range_kph: float,
    min_set_speed_gap_kph: float,
    min_moving_display_speed_kph: float,
) -> list[dict[str, object]]:
    scenes: list[dict[str, object]] = []

    for segment in mask_to_segments(axis, base_mask):
        start_index = segment.start_index
        while start_index <= segment.end_index:
            current_set_speed = set_speed[start_index]
            set_speed_end_index = start_index
            while (
                set_speed_end_index + 1 <= segment.end_index
                and set_speed[set_speed_end_index + 1] == current_set_speed
            ):
                set_speed_end_index += 1

            window_start = start_index
            while window_start <= set_speed_end_index:
                window_end = window_start
                while (
                    window_end <= set_speed_end_index
                    and float(axis[window_end] - axis[window_start]) < min_stable_duration_seconds
                ):
                    window_end += 1

                if window_end > set_speed_end_index:
                    break

                display_window = display_speed[window_start : window_end + 1]
                v_min = min(display_window)
                v_max = max(display_window)
                display_range = v_max - v_min
                set_speed_gap = current_set_speed - v_max

                if (
                    v_min > min_moving_display_speed_kph
                    and v_max > min_moving_display_speed_kph
                    and display_range < stable_range_kph
                    and set_speed_gap >= min_set_speed_gap_kph
                ):
                    _append_scene_if_qualified(
                        scenes,
                        axis,
                        display_speed,
                        set_speed,
                        curvature_limit_kph,
                        unp_match_gap_ms,
                        fusion_match_gap_ms,
                        window_start,
                        window_end,
                        min_stable_duration_seconds,
                        stable_range_kph,
                        min_set_speed_gap_kph,
                        min_moving_display_speed_kph,
                    )
                    window_start = window_end + 1
                else:
                    window_start += 1

            start_index = set_speed_end_index + 1

    return scenes


def _append_scene_if_qualified(
    scenes: list[dict[str, object]],
    axis: list[float],
    display_speed: list[int],
    set_speed: list[int],
    curvature_limit_kph: list[float],
    unp_match_gap_ms: list[float | None],
    fusion_match_gap_ms: list[float | None],
    start_index: int,
    end_index: int,
    min_stable_duration_seconds: float,
    stable_range_kph: float,
    min_set_speed_gap_kph: float,
    min_moving_display_speed_kph: float,
) -> None:
    if end_index < start_index:
        return
    duration = float(axis[end_index] - axis[start_index])
    if duration < min_stable_duration_seconds:
        return

    display_window = display_speed[start_index : end_index + 1]
    set_speed_window = set_speed[start_index : end_index + 1]
    limit_window = curvature_limit_kph[start_index : end_index + 1]
    unp_gap_window = [gap for gap in unp_match_gap_ms[start_index : end_index + 1] if gap is not None]
    fusion_gap_window = [gap for gap in fusion_match_gap_ms[start_index : end_index + 1] if gap is not None]
    if not display_window or not set_speed_window:
        return

    v_min = min(display_window)
    v_max = max(display_window)
    display_range = v_max - v_min
    scene_set_speed = set_speed_window[0]
    set_speed_gap = scene_set_speed - v_max
    if any(item != scene_set_speed for item in set_speed_window):
        return
    if (
        v_min <= min_moving_display_speed_kph
        or v_max <= min_moving_display_speed_kph
        or display_range >= stable_range_kph
        or set_speed_gap < min_set_speed_gap_kph
    ):
        return

    stable_display_speed = sum(display_window) / len(display_window)
    valid_limits = [limit for limit in limit_window if limit > 0.0]
    min_curve_limit = min(valid_limits) if valid_limits else 0.0

    scenes.append(
        {
            "start_timestamp": float(axis[start_index]),
            "end_timestamp": float(axis[end_index]),
            "duration": duration,
            "sample_count": end_index - start_index + 1,
            "set_speed": round(scene_set_speed, 3),
            "stable_display_speed": round(stable_display_speed, 3),
            "v_min_kph": round(v_min, 3),
            "v_max_kph": round(v_max, 3),
            "display_range": round(display_range, 3),
            "set_speed_gap_kph": round(set_speed_gap, 3),
            "min_curvature_limit_kph": round(min_curve_limit, 3),
            "max_topic_gap_ms": round(max(unp_gap_window), 3) if unp_gap_window else 0.0,
            "max_fusion_gap_ms": round(max(fusion_gap_window), 3) if fusion_gap_window else 0.0,
            "low_speed": False,
            "severity": 0.0,
            "merged_window_count": 1,
        }
    )


def _score_scene(scene: dict[str, object], min_set_speed_gap_kph: float) -> None:
    severity = max(float(scene["set_speed"]) - float(scene["v_max_kph"]), 0.0)
    scene["severity"] = round(severity, 3)
    scene["low_speed"] = severity >= min_set_speed_gap_kph


def _scene_counts(scenes: list[dict[str, object]]) -> tuple[int, float]:
    bad_scenes = [scene for scene in scenes if bool(scene.get("low_speed", False))]
    severity_score = max((float(scene.get("severity", 0.0) or 0.0) for scene in bad_scenes), default=0.0)
    return len(bad_scenes), round(severity_score, 3)


def _merge_contiguous_scenes(
    scenes: list[dict[str, object]],
    max_gap_seconds: float,
) -> list[dict[str, object]]:
    if not scenes:
        return []

    sorted_scenes = sorted(scenes, key=lambda scene: float(scene["start_timestamp"]))
    merged: list[dict[str, object]] = []
    for scene in sorted_scenes:
        if not merged or not _can_merge_scenes(merged[-1], scene, max_gap_seconds):
            merged.append(dict(scene))
            continue
        _merge_scene_into(merged[-1], scene)
    return merged


def _can_merge_scenes(
    previous_scene: dict[str, object],
    current_scene: dict[str, object],
    max_gap_seconds: float,
) -> bool:
    previous_end = float(previous_scene.get("end_timestamp", 0.0) or 0.0)
    current_start = float(current_scene.get("start_timestamp", 0.0) or 0.0)
    if current_start - previous_end > max_gap_seconds:
        return False
    previous_set_speed = float(previous_scene.get("set_speed", 0.0) or 0.0)
    current_set_speed = float(current_scene.get("set_speed", 0.0) or 0.0)
    return abs(previous_set_speed - current_set_speed) <= DEFAULT_SCENE_MERGE_SET_SPEED_TOLERANCE_KPH


def _merge_scene_into(target: dict[str, object], source: dict[str, object]) -> None:
    target_start = float(target["start_timestamp"])
    target_end = max(float(target["end_timestamp"]), float(source["end_timestamp"]))
    target_samples = int(target.get("sample_count", 0) or 0)
    source_samples = int(source.get("sample_count", 0) or 0)
    total_samples = target_samples + source_samples

    if total_samples > 0:
        target_avg = float(target.get("stable_display_speed", 0.0) or 0.0)
        source_avg = float(source.get("stable_display_speed", 0.0) or 0.0)
        target["stable_display_speed"] = round(
            ((target_avg * target_samples) + (source_avg * source_samples)) / total_samples,
            3,
        )

    target["end_timestamp"] = target_end
    target["duration"] = round(target_end - target_start, 3)
    target["sample_count"] = total_samples
    target["v_min_kph"] = min(float(target["v_min_kph"]), float(source["v_min_kph"]))
    target["v_max_kph"] = max(float(target["v_max_kph"]), float(source["v_max_kph"]))
    target["display_range"] = round(float(target["v_max_kph"]) - float(target["v_min_kph"]), 3)
    target["set_speed_gap_kph"] = max(float(target["set_speed_gap_kph"]), float(source["set_speed_gap_kph"]))

    target_limit = float(target.get("min_curvature_limit_kph", 0.0) or 0.0)
    source_limit = float(source.get("min_curvature_limit_kph", 0.0) or 0.0)
    positive_limits = [value for value in (target_limit, source_limit) if value > 0.0]
    target["min_curvature_limit_kph"] = round(min(positive_limits), 3) if positive_limits else 0.0
    target["max_topic_gap_ms"] = max(float(target.get("max_topic_gap_ms", 0.0) or 0.0), float(source.get("max_topic_gap_ms", 0.0) or 0.0))
    target["max_fusion_gap_ms"] = max(float(target.get("max_fusion_gap_ms", 0.0) or 0.0), float(source.get("max_fusion_gap_ms", 0.0) or 0.0))
    target["severity"] = max(float(target.get("severity", 0.0) or 0.0), float(source.get("severity", 0.0) or 0.0))
    target["low_speed"] = bool(target.get("low_speed", False)) or bool(source.get("low_speed", False))
    target["merged_window_count"] = int(target.get("merged_window_count", 1) or 1) + int(
        source.get("merged_window_count", 1) or 1
    )


def _apply_cross_folder_scene_merge(
    result: dict[str, object],
    folder_context: dict[str, Any],
) -> dict[str, object]:
    scenes = [dict(scene) for scene in result.get("scenes", [])]
    if not scenes:
        return result

    folder_path = Path(str(folder_context.get("folder_path") or Path(str(folder_context.get("primary_h5_file", ""))).parent))
    run_key = str(folder_path.parent.resolve()) if str(folder_path) else ""
    start_timestamp = float(result.get("start_timestamp", 0.0) or 0.0)

    state_run_key = _CROSS_FOLDER_SCENE_MERGE_STATE.get("run_key")
    last_seen_start = _CROSS_FOLDER_SCENE_MERGE_STATE.get("last_seen_start")
    if state_run_key != run_key or (
        isinstance(last_seen_start, (int, float)) and start_timestamp < float(last_seen_start) - DEFAULT_SCENE_MERGE_MAX_GAP_SECONDS
    ):
        _CROSS_FOLDER_SCENE_MERGE_STATE.clear()
        _CROSS_FOLDER_SCENE_MERGE_STATE["run_key"] = run_key

    _CROSS_FOLDER_SCENE_MERGE_STATE["last_seen_start"] = start_timestamp

    counted_scenes: list[dict[str, object]] = []
    suppressed_count = 0
    for scene in sorted(scenes, key=lambda item: float(item["start_timestamp"])):
        if _is_cross_folder_continuation(scene):
            suppressed_count += 1
            _extend_cross_folder_state(scene)
            continue
        counted_scenes.append(scene)
        _set_cross_folder_state(scene)

    result = dict(result)
    result["scenes"] = counted_scenes
    bad_case_count, severity_score = _scene_counts(counted_scenes)
    result["all_case_count"] = len(counted_scenes)
    result["bad_case_count"] = bad_case_count
    result["severity_score"] = round(severity_score, 3)
    if suppressed_count:
        result["merged_continuation_count"] = suppressed_count
    return result


def _is_cross_folder_continuation(scene: dict[str, object]) -> bool:
    last_end = _CROSS_FOLDER_SCENE_MERGE_STATE.get("last_scene_end")
    if not isinstance(last_end, (int, float)):
        return False
    last_set_speed = float(_CROSS_FOLDER_SCENE_MERGE_STATE.get("last_set_speed", 0.0) or 0.0)
    current_set_speed = float(scene.get("set_speed", 0.0) or 0.0)
    if abs(last_set_speed - current_set_speed) > DEFAULT_SCENE_MERGE_SET_SPEED_TOLERANCE_KPH:
        return False
    current_start = float(scene.get("start_timestamp", 0.0) or 0.0)
    return current_start <= float(last_end) + DEFAULT_SCENE_MERGE_MAX_GAP_SECONDS


def _set_cross_folder_state(scene: dict[str, object]) -> None:
    _CROSS_FOLDER_SCENE_MERGE_STATE["last_scene_end"] = float(scene.get("end_timestamp", 0.0) or 0.0)
    _CROSS_FOLDER_SCENE_MERGE_STATE["last_set_speed"] = float(scene.get("set_speed", 0.0) or 0.0)


def _extend_cross_folder_state(scene: dict[str, object]) -> None:
    previous_end = float(_CROSS_FOLDER_SCENE_MERGE_STATE.get("last_scene_end", 0.0) or 0.0)
    _CROSS_FOLDER_SCENE_MERGE_STATE["last_scene_end"] = max(
        previous_end,
        float(scene.get("end_timestamp", 0.0) or 0.0),
    )
    _CROSS_FOLDER_SCENE_MERGE_STATE["last_set_speed"] = float(scene.get("set_speed", 0.0) or 0.0)


def _normalize_source_folders(
    folder_path: str | Path | list[str | Path] | None,
    default_folder: Path,
) -> list[Path]:
    if folder_path is None:
        return [default_folder]
    if isinstance(folder_path, (str, Path)):
        return [Path(folder_path)]

    folders: list[Path] = []
    for item in folder_path:
        if item is None:
            continue
        folders.append(Path(item))
    return folders or [default_folder]


def _load_topic_context(folder_paths: list[Path], tolerance_ms: float) -> dict[str, object]:
    unp_files: list[Path] = []
    fusion_files: list[Path] = []
    unp_frames: list[dict[str, object]] = []
    fusion_frames: list[dict[str, object]] = []
    primary_unp_file: Path | None = None
    primary_unp_frame_count = 0

    for folder_index, folder_path in enumerate(folder_paths):
        unp_file = _find_topic_file(folder_path, ["_unp_planning_info.txt"], ["unp_planning_info"])
        if unp_file:
            unp_files.append(unp_file)
            parsed_unp_frames = _parse_unp_planning_file(unp_file)
            if folder_index == 0:
                primary_unp_file = unp_file
                primary_unp_frame_count = len(parsed_unp_frames)
            unp_frames.extend(parsed_unp_frames)

    if primary_unp_file is None or primary_unp_frame_count == 0:
        return {
            "folder_paths": [str(path) for path in folder_paths],
            "tolerance_ms": float(tolerance_ms),
            "unp_file": str(unp_files[0]) if unp_files else "",
            "primary_unp_file": str(primary_unp_file or ""),
            "primary_unp_frame_count": primary_unp_frame_count,
            "fusion_file": "",
            "unp_files": [str(path) for path in unp_files],
            "fusion_files": [],
            "unp_frames": [],
            "fusion_frames": [],
            "unp_timestamps": [],
            "fusion_timestamps": [],
        }

    for folder_path in folder_paths:
        fusion_file = _find_topic_file(
            folder_path,
            ["_perception_fusion_object_auto.txt", "_perception_fusion_object.txt"],
            ["perception_fusion_object"],
        )
        if fusion_file:
            fusion_files.append(fusion_file)
            fusion_frames.extend(_parse_fusion_object_file(fusion_file))

    unp_frames.sort(key=lambda frame: float(frame["timestamp_ms"]))
    fusion_frames.sort(key=lambda frame: float(frame["timestamp_ms"]))

    return {
        "folder_paths": [str(path) for path in folder_paths],
        "tolerance_ms": float(tolerance_ms),
        "unp_file": str(unp_files[0]) if unp_files else "",
        "primary_unp_file": str(primary_unp_file or ""),
        "primary_unp_frame_count": primary_unp_frame_count,
        "fusion_file": str(fusion_files[0]) if fusion_files else "",
        "unp_files": [str(path) for path in unp_files],
        "fusion_files": [str(path) for path in fusion_files],
        "unp_frames": unp_frames,
        "fusion_frames": fusion_frames,
        "unp_timestamps": [float(frame["timestamp_ms"]) for frame in unp_frames],
        "fusion_timestamps": [float(frame["timestamp_ms"]) for frame in fusion_frames],
    }


def _find_topic_file(folder_path: Path, exact_names: list[str], required_name_parts: list[str]) -> Path | None:
    for file_name in exact_names:
        candidate = folder_path / file_name
        if candidate.exists():
            return candidate

    candidates = []
    for path in folder_path.glob("*.txt"):
        lower_name = path.name.lower()
        if all(part.lower() in lower_name for part in required_name_parts):
            candidates.append(path)
    if not candidates:
        return None

    noisy_parts = ("early", "static", "parking", "smooth", "adb", "aeb", "ground", "uss", "vision")
    candidates.sort(key=lambda item: (sum(part in item.name.lower() for part in noisy_parts), len(item.name), item.name))
    return candidates[0]


def _parse_unp_planning_file(file_path: Path) -> list[dict[str, object]]:
    cache_key = _file_cache_key(file_path)
    cached = _UNP_PARSE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    content = _read_text(file_path)
    paragraph_pattern = re.compile(r"(?m)^(\d{19})\s+seq:\s*\d+\s*([\s\S]*?)(?=^\d{19}\s+seq:|\Z)")
    frames: list[dict[str, object]] = []

    for match in paragraph_pattern.finditer(content):
        paragraph = match.group(2).replace("\\", "")
        timestamp_ms = _extract_stamp_ms(paragraph)
        if timestamp_ms is None:
            continue

        curve_enabled = _parse_bool_value(
            _extract_nested_value(paragraph, "curvature_info", "is_curv_v_limit_enable"),
            default=False,
        )
        curve_limit_mps = _parse_float_value(
            _extract_nested_value(paragraph, "curvature_info", "curv_velocity_limit"),
            default=0.0,
        )
        set_speed_mps = _parse_float_value(_extract_nested_value(paragraph, "acc_info", "cruise_velocity"), default=0.0)
        traffic_light_stop_flag = _parse_bool_value(
            _extract_nested_value(paragraph, "traffic_light_decider_info", "stop_flag"),
            default=False,
        )
        traffic_light_stop_distance = _parse_float_value(
            _extract_nested_value(paragraph, "traffic_light_decider_info", "stop_distance"),
            default=None,
        )
        traffic_light_status = _parse_float_value(
            _extract_nested_value(paragraph, "traffic_light_decider_info", "traffic_light_status"),
            default=None,
        )
        raw_traffic_light_status = _parse_float_value(
            _extract_nested_value(paragraph, "traffic_light_decider_info", "raw_traffic_light_status"),
            default=None,
        )
        frames.append(
            {
                "timestamp_ms": timestamp_ms,
                "curvature_limit_enabled": curve_enabled,
                "curvature_limit_kph": max(curve_limit_mps, 0.0) * 3.6,
                "set_speed_kph": max(set_speed_mps, 0.0) * 3.6,
                "traffic_light_stop_flag": traffic_light_stop_flag,
                "traffic_light_stop_distance_m": traffic_light_stop_distance,
                "traffic_light_status": traffic_light_status,
                "raw_traffic_light_status": raw_traffic_light_status,
            }
        )

    _UNP_PARSE_CACHE[cache_key] = frames
    return frames


def _parse_fusion_object_file(file_path: Path) -> list[dict[str, object]]:
    cache_key = _file_cache_key(file_path)
    cached = _FUSION_PARSE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    content = _read_text(file_path)
    paragraph_pattern = re.compile(r"(?m)^(\d{19})\s+header:\s*([\s\S]*?)(?=^\d{19}\s+header:|\Z)")
    frames: list[dict[str, object]] = []

    for match in paragraph_pattern.finditer(content):
        paragraph = match.group(2)
        timestamp_ms = _extract_sensor_timestamp_ms(paragraph)
        if timestamp_ms is None:
            timestamp_ms = _extract_stamp_ms(paragraph)
        if timestamp_ms is None:
            continue

        fusion_match = re.search(r"perception_fusion_objects_data:\s*([\s\S]*?)(?=\nreserved_infos:|\Z)", paragraph)
        fusion_data = fusion_match.group(1) if fusion_match else ""
        has_front_target = False
        has_close_front_target = False
        closest_front_x: float | None = None
        closest_front_ttc: float | None = None

        for object_text in re.split(r"\n\s*-\s*", fusion_data):
            if "track_id:" not in object_text or not re.search(r"available:\s*True", object_text, re.IGNORECASE):
                continue
            pos_x = _parse_float_value(_extract_proto_vector_value(object_text, "relative_position", "x"), default=None)
            pos_y = _parse_float_value(_extract_proto_vector_value(object_text, "relative_position", "y"), default=None)
            if pos_x is None or pos_y is None:
                continue
            if abs(pos_y) > FRONT_TARGET_MAX_ABS_Y_METERS:
                continue

            ttc = _parse_float_value(_extract_proto_scalar_value(object_text, "crash_risk_ttc"), default=None)
            close_front = 0.0 < pos_x < FRONT_TARGET_CLOSE_DISTANCE_METERS
            ttc_front = (
                ttc is not None
                and ttc < FRONT_TARGET_MAX_TTC_SECONDS
                and FRONT_TARGET_MIN_X_METERS <= pos_x <= FRONT_TARGET_MAX_X_METERS
            )
            if close_front or ttc_front:
                has_front_target = True
                has_close_front_target = has_close_front_target or close_front
                closest_front_x = pos_x if closest_front_x is None else min(closest_front_x, pos_x)
                if ttc is not None:
                    closest_front_ttc = ttc if closest_front_ttc is None else min(closest_front_ttc, ttc)

        frames.append(
            {
                "timestamp_ms": timestamp_ms,
                "has_front_target": has_front_target,
                "has_close_front_target": has_close_front_target,
                "closest_front_x": closest_front_x,
                "closest_front_ttc": closest_front_ttc,
            }
        )

    _FUSION_PARSE_CACHE[cache_key] = frames
    return frames


def _file_cache_key(file_path: Path) -> tuple[str, int, int]:
    stat = file_path.stat()
    return (str(file_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _nearest_frame(
    frames: list[dict[str, object]],
    timestamps: list[float],
    target_time_ms: float,
    tolerance_ms: float,
) -> tuple[dict[str, object] | None, float | None]:
    if not frames:
        return None, None

    insert_pos = bisect_left(timestamps, target_time_ms)
    candidate_indexes = []
    if insert_pos < len(timestamps):
        candidate_indexes.append(insert_pos)
    if insert_pos > 0:
        candidate_indexes.append(insert_pos - 1)
    if not candidate_indexes:
        return None, None

    best_index = min(candidate_indexes, key=lambda index: abs(timestamps[index] - target_time_ms))
    gap_ms = abs(timestamps[best_index] - target_time_ms)
    if gap_ms > tolerance_ms:
        return None, gap_ms
    return frames[best_index], gap_ms


def _curve_limit_allows_set_speed(
    curve_enabled: bool,
    curve_limit_kph: float,
    selected_set_speed_kph: float,
) -> bool:
    if not curve_enabled:
        return True
    if curve_limit_kph <= 0.0:
        return True
    return curve_limit_kph >= selected_set_speed_kph


def _is_traffic_light_stop(unp_frame: dict[str, object], max_stop_distance_m: float) -> bool:
    if not bool(unp_frame.get("traffic_light_stop_flag", False)):
        return False
    stop_distance = _parse_float_value(unp_frame.get("traffic_light_stop_distance_m"), default=None)
    if stop_distance is None:
        return True
    if stop_distance < 0.0:
        return False
    return stop_distance <= max_stop_distance_m


def _extract_stamp_ms(text: str) -> float | None:
    match = re.search(r"stamp:\s*\n\s*secs:\s*(\d+)\s*\n\s*nsecs:\s*(\d+)", text)
    if not match:
        return None
    return int(match.group(1)) * 1000.0 + int(match.group(2)) / 1_000_000.0


def _extract_sensor_timestamp_ms(text: str) -> float | None:
    match = re.search(r"meta:\s*[\s\S]*?sensor_timestamp_us:\s*(\d+)", text)
    if not match:
        return None
    return int(match.group(1)) / 1000.0


def _extract_nested_value(text: str, section_name: str, field_name: str) -> str | None:
    match = re.search(
        rf'"{re.escape(section_name)}"\s*:\s*{{[\s\S]*?"\s*{re.escape(field_name)}\s*"\s*:\s*([^,}}]+)',
        text,
    )
    if not match:
        return None
    return match.group(1).strip().strip('"')


def _extract_proto_vector_value(text: str, section_name: str, field_name: str) -> str | None:
    match = re.search(
        rf"{re.escape(section_name)}:\s*[\s\S]*?{re.escape(field_name)}:\s*(-?\d+(?:\.\d+)?)",
        text,
    )
    if not match:
        return None
    return match.group(1)


def _extract_proto_scalar_value(text: str, field_name: str) -> str | None:
    match = re.search(rf"\b{re.escape(field_name)}:\s*(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return match.group(1)


def _read_text(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="gbk", errors="ignore")


def _parse_float_value(value: str | None, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_bool_value(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().strip('"').lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return default


def _display_speed_from_actual(actual_speed: float) -> int:
    bias = _speed_bias_factor(actual_speed)
    display_speed = actual_speed * bias
    return _round_half_up(display_speed, 0)


def _speed_bias_factor(actual_speed: float) -> float:
    speed = max(float(actual_speed), 0.0)
    if speed <= BIAS_X[0]:
        return BIAS_Y[0]
    if speed >= BIAS_X[-1]:
        return BIAS_Y[-1]
    for index in range(1, len(BIAS_X)):
        if speed <= BIAS_X[index]:
            x0 = BIAS_X[index - 1]
            x1 = BIAS_X[index]
            y0 = BIAS_Y[index - 1]
            y1 = BIAS_Y[index]
            slope = (y1 - y0) / (x1 - x0)
            value = slope * (speed - x0) + y0
            return _round_half_up(value, 3)
    return BIAS_Y[-1]


def _round_half_up(value: float, digits: int) -> float:
    quantize_str = "1" if digits == 0 else "1." + ("0" * digits)
    decimal_value = Decimal(str(value)).quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
    rounded = float(decimal_value)
    return int(rounded) if digits == 0 else rounded


def _empty_result(
    start_timestamp: float,
    end_timestamp: float,
    min_stable_duration_seconds: float,
    stable_range_kph: float,
    min_set_speed_gap_kph: float,
    min_moving_display_speed_kph: float,
    topic_match_tolerance_ms: float,
    traffic_light_stop_max_distance_m: float,
    scene_merge_max_gap_seconds: float,
    topic_context: dict[str, object],
    skip_reason: str = "",
) -> dict[str, object]:
    result = {
        "metric_name": "acc_unreasonable_low_speed_count",
        "all_case_count": 0,
        "bad_case_count": 0,
        "severity_score": 0.0,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "scenes": [],
        "parameters": {
            "min_stable_duration_seconds": min_stable_duration_seconds,
            "stable_range_kph": stable_range_kph,
            "min_set_speed_gap_kph": min_set_speed_gap_kph,
            "min_moving_display_speed_kph": min_moving_display_speed_kph,
            "topic_match_tolerance_ms": topic_match_tolerance_ms,
            "traffic_light_stop_max_distance_m": traffic_light_stop_max_distance_m,
            "scene_merge_max_gap_seconds": scene_merge_max_gap_seconds,
            "front_target_max_ttc_seconds": FRONT_TARGET_MAX_TTC_SECONDS,
            "front_target_close_distance_m": FRONT_TARGET_CLOSE_DISTANCE_METERS,
        },
        "topic_summary": _topic_summary(topic_context),
    }
    if skip_reason:
        result["skip_reason"] = skip_reason
    return result


def _topic_summary(topic_context: dict[str, object]) -> dict[str, object]:
    unp_files = [Path(str(path)).name for path in topic_context.get("unp_files", [])]
    fusion_files = [Path(str(path)).name for path in topic_context.get("fusion_files", [])]
    unp_frames = list(topic_context.get("unp_frames", []))
    return {
        "unp_file": " + ".join(unp_files),
        "fusion_file": " + ".join(fusion_files),
        "unp_frame_count": len(unp_frames),
        "traffic_light_stop_frame_count": sum(
            1 for frame in unp_frames if _is_traffic_light_stop(frame, DEFAULT_TRAFFIC_LIGHT_STOP_MAX_DISTANCE_M)
        ),
        "fusion_frame_count": len(topic_context.get("fusion_frames", [])),
        "tolerance_ms": float(topic_context.get("tolerance_ms", DEFAULT_TOPIC_MATCH_TOLERANCE_MS)),
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in {"0", "false", "none", ""}
