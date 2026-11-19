import math
from typing import Callable, Dict, List
from longitudinal_preprocessor import is_front_clear
from powertrain_utils import get_powertrain_type, value_for_powertrain


def slope_speed_threshold(frames: List[Dict]) -> float:
    """SetSpeed deviation threshold in m/s, matching the slope fluctuation KPI."""
    return value_for_powertrain(
        get_powertrain_type(frames), ice_value=0.6, phev_value=0.3,
        bev_value=0.3, erev_value=0.3,
    )


def find_steady_slope_segments(
    frames: List[Dict], get_params: Callable[[Dict], Dict],
) -> List[List[Dict]]:
    if not frames:
        return []
    threshold = slope_speed_threshold(frames)
    excursion_observation_ms = 2000.0
    segments: List[List[Dict]] = []
    reference = None
    dwell_time = None
    steady_start = None
    excursion_start = None
    excursion_time = None
    previous_time = None

    def finish(end: int) -> None:
        if steady_start is not None and end >= steady_start:
            start_time = float(frames[steady_start]["unp_timestamp_ms"])
            end_time = float(frames[end]["unp_timestamp_ms"])
            if end_time - start_time >= 5000.0 - 1e-6:
                segments.append(frames[steady_start:end + 1])

    for i, frame in enumerate(frames):
        params = get_params(frame)
        timestamp = frame.get("unp_timestamp_ms")
        speed = params["velocity"]
        set_speed = frame.get("unp_frame_data", {}).get("Acc_info", {}).get("SetSpeed")
        front_clear = is_front_clear(frame.get("matched_fusion") or params)
        numeric = all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in (timestamp, speed, set_speed, params["slope_acc"], params["curv_spd_limit"])
        )
        valid = (
            numeric
            and set_speed > 0.0
            and params["has_valid_data"] is True
            and params["f_np_on"] is True
            and params.get("is_non_straight_driving", False) is False
            and params["f_stationary"] is False
            and front_clear
            and abs(params["slope_acc"]) >= 0.1
            and params["curv_spd_limit"] >= speed
            and not (params["f_redlight"] and params["f_stop_dis"] < 40)
            and (previous_time is None or timestamp > previous_time)
        )
        changed = valid and reference is not None and abs(set_speed - reference) > 1e-6
        if not valid or changed:
            finish((excursion_start if excursion_start is not None else i) - 1)
            reference = dwell_time = steady_start = None
            excursion_start = excursion_time = None
        previous_time = timestamp if numeric else None
        if not valid:
            continue
        if reference is None:
            if frame.get("_sequence_meta", {}).get("source_index", 0) != 0:
                continue
            reference = set_speed

        # Both chassis speed and SetSpeed are m/s. Keep brief excursions in the
        # measurement, but remove a sustained departure from its first sample.
        in_band = abs(speed - set_speed) <= threshold + 1e-9
        if steady_start is not None:
            if in_band:
                excursion_start = excursion_time = None
            elif excursion_start is None:
                excursion_start, excursion_time = i, timestamp
            elif timestamp - excursion_time >= excursion_observation_ms - 1e-6:
                finish(excursion_start - 1)
                reference = dwell_time = steady_start = None
                excursion_start = excursion_time = None
            continue

        if in_band:
            if dwell_time is None:
                dwell_time = timestamp
            if timestamp - dwell_time >= 1000.0 - 1e-6:
                steady_start = i
        else:
            dwell_time = None

    finish((excursion_start if excursion_start is not None else len(frames)) - 1)
    return segments
