import math
from typing import Callable, Dict, List


def find_steady_slope_segments(
    frames: List[Dict], get_params: Callable[[Dict], Dict],
) -> List[List[Dict]]:
    segments: List[List[Dict]] = []
    reference = None
    dwell_time = None
    steady_start = None
    previous_time = None

    def finish(end: int) -> None:
        if steady_start is not None:
            start_time = float(frames[steady_start]["unp_timestamp_ms"])
            end_time = float(frames[end]["unp_timestamp_ms"])
            if end_time - start_time >= 5000.0 - 1e-6:
                segments.append(frames[steady_start:end + 1])

    for i, frame in enumerate(frames):
        params = get_params(frame)
        timestamp = frame.get("unp_timestamp_ms")
        speed = params["velocity"]
        set_speed = frame.get("unp_frame_data", {}).get("Acc_info", {}).get("SetSpeed")
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
            and params["f_potential_cipv_by_pos"] is False
            and abs(params["slope_acc"]) >= 0.1
            and params["curv_spd_limit"] >= speed
            and not (params["f_redlight"] and params["f_stop_dis"] < 40)
            and (previous_time is None or timestamp > previous_time)
        )
        changed = valid and reference is not None and abs(set_speed - reference) > 1e-6
        if not valid or changed:
            finish(i - 1)
            reference = dwell_time = steady_start = None
        previous_time = timestamp if numeric else None
        if not valid:
            continue
        if reference is None:
            if frame.get("_sequence_meta", {}).get("source_index", 0) != 0:
                continue
            reference = set_speed
        if steady_start is not None:
            continue
        # Topic cruise_velocity and vehicle_speed_average are both m/s.
        if abs(speed - set_speed) <= 2.0 / 3.6 + 1e-9:
            if dwell_time is None:
                dwell_time = timestamp
            if timestamp - dwell_time >= 1000.0 - 1e-6:
                steady_start = i
        else:
            dwell_time = None
    finish(len(frames) - 1)
    return segments
