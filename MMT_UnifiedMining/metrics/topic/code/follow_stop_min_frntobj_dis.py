import os
import re
import traceback
import json
import time
import math
import statistics
from typing import List, Dict, Optional, Tuple, Any, Union  # 新增Any/Union，修复3.8兼容
import sys
current_dir = os.path.abspath(os.getcwd())
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
import longitudinal_preprocessor as lonprepro


def _is_non_straight_driving_scene(unp_frame_data: Dict) -> bool:
    return lonprepro.is_non_straight_driving_scene(unp_frame_data)



def _get_unp_set_speed(comp_frame: Dict) -> float:
    unp_frame_data = comp_frame.get("unp_frame_data", {})
    set_speed = unp_frame_data.get("Acc_info", {}).get("SetSpeed", 0.0)
    try:
        return float(set_speed)
    except (TypeError, ValueError):
        return 0.0


def _is_set_speed_stable(current_frame: Dict, previous_frame: Optional[Dict], tolerance: float = 1e-6) -> bool:
    if previous_frame is None:
        return True
    return abs(_get_unp_set_speed(current_frame) - _get_unp_set_speed(previous_frame)) <= tolerance

def _is_primary_sequence_frame(comp_frame: Dict) -> bool:
    return comp_frame.get("_sequence_meta", {}).get("source_index", 0) == 0

def __run_haoran_data_run__(comprehensive_frames: List[Dict]) -> Tuple[bool, List[Union[str, Dict]], Dict]:  # 修复1：替换tuple为Tuple，List[str]改为兼容字典
    # -------------------------- 新增：记录开始时间 --------------------------
    start_time = time.time()
    # -------------------------- 初始化核心变量（显式类型标注） --------------------------
    check_result: bool = False  # 是否找到符合条件内容
    result_dict: List[Union[str, Dict]] = []  # 修复2：改为兼容str和Dict的类型
    case_Statistics: Dict = {}

    folder_result = _core_process(comprehensive_frames)

    if folder_result is not None:
        # 修复3：内层引号改为单引号，解决f-string引号冲突
        result_items = folder_result[1]
        if isinstance(result_items, list):
            result_dict.extend(str(item) for item in result_items)
        elif result_items is not None:
            result_dict.append(str(result_items))
        check_result = folder_result[0]
        case_Statistics = folder_result[2]

    # -------------------------- 新增：计算总耗时并插入到result_dict最前端 --------------------------
    total_time = time.time() - start_time
    # 插入到列表第一个位置
    result_dict.insert(0, f"【运算总耗时】: {total_time:.4f} 秒")
    
    print(f"【运算总耗时】: {total_time:.4f} 秒")

    return check_result, result_dict, case_Statistics

# ------------------------------ 核心处理函数（单文件夹立即处理） ------------------------------
def _core_process(comprehensive_frames: List[Dict]) -> Tuple[bool, List[str], Dict]:  # 修复7：改为Any，兼容字符串/字典

    check_result: bool = False
    dis_lower_bound = 3.0
    dis_upper_bound = 4.0

    # 3. 查找所有连续风险段落（基于_unp_planning_info的段落顺序）
    risk_segments = _find_risk_segments(comprehensive_frames)

    # 4. 统计每个风险段落完成刹停时的前车距离
    stop_frntobj_dis_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": 0,
        "Bad Case": 0,
        "Excluded Case": 0,
        "Severity Score": float(0.0)
    }
    frame_indices = {id(frame): index for index, frame in enumerate(comprehensive_frames)}
    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        observation = _measure_stable_stop_distance(
            segment, comprehensive_frames, frame_indices[id(segment[-1])],
        )
        if observation.get("excluded_reason"):
            case_statistics["Excluded Case"] += 1
            stop_frntobj_dis_values.append(
                "[未统计跟车刹停距离 time:%s, 原因:%s]" % (
                    observation.get("stop_time_ms"), observation["excluded_reason"],
                )
            )
            continue
        stop_lon_dis = observation["distance"]
        stop_dis_time = observation["stop_time_ms"]
        curr_lat_dis = observation["lateral_distance"]
        curr_vel = observation["ego_speed"]
        case_statistics["All Case Count"] += 1
        window_description = (
            ", stable_window_ms:[%.3f,%.3f], distance_sample:median, sample_count:%d"
            % (stop_dis_time + 1000, stop_dis_time + 2000, observation["sample_count"])
        )
        if stop_lon_dis is not None:
            if _check_stop_frntobj_dis_valid(stop_lon_dis, dis_lower_bound, dis_upper_bound) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(stop_lon_dis, dis_lower_bound, dis_upper_bound))
                check_result = True
                stop_frntobj_dis_values.append(f"[StopFrntObjDis Dangerous! time:{stop_dis_time} , stop_obj_pos:(lon_dis:{stop_lon_dis:.6f}, lat_dis:{curr_lat_dis:.6f}), curr_vel: {curr_vel:.6f}{window_description}]")
            else:
                stop_frntobj_dis_values.append(f"[time:{stop_dis_time} , stop_obj_pos:(lon_dis:{stop_lon_dis:.6f}, lat_dis:{curr_lat_dis:.6f}), curr_vel: {curr_vel:.6f}{window_description}]")

    return (check_result, stop_frntobj_dis_values, case_statistics)

def _check_stop_frntobj_dis_valid(
    stop_frntobj_dis: Optional[float],
    C_dis_lower_bound: float,
    C_dis_upper_bound: float,
) -> bool:

    ref_frntobj_dis = stop_frntobj_dis

    if (
        (ref_frntobj_dis >= C_dis_lower_bound or math.isclose(ref_frntobj_dis, C_dis_lower_bound, rel_tol=0, abs_tol=1e-9))
        and (ref_frntobj_dis <= C_dis_upper_bound or math.isclose(ref_frntobj_dis, C_dis_upper_bound, rel_tol=0, abs_tol=1e-9))
    ):
        return True
    else:
        return False
    
def _cal_severity_score(
    min_frntobj_dis: Optional[float],
    C_dis_lower_bound: float,
    C_dis_upper_bound: float,
) -> float:

    ref_frntobj_dis = min_frntobj_dis

    if _check_stop_frntobj_dis_valid(ref_frntobj_dis, C_dis_lower_bound, C_dis_upper_bound):
        return 0.0
    else:
        C_cal_base = 2  #严重程度得分100分的越限基准
        if ref_frntobj_dis < C_dis_lower_bound:
            exceed_amount = C_dis_lower_bound - ref_frntobj_dis
        else:
            exceed_amount = ref_frntobj_dis - C_dis_upper_bound
        return abs(exceed_amount/C_cal_base)*100.0

def _find_risk_segments(
    comprehensive_frames: List[Dict]
) -> List[List[Dict]]:
    """查找连续风险段落：unp段落匹配的fusion段落crash_risk_ttc < 20（适配字典格式）"""
    risk_segments = []
    current_segment = []
    # 有效风险段最小持续的帧数
    C_min_risk_segment_length = 5
    C_min_risk_ttc = 30   
    C_min_start_vel = 2

    prev_unp_para = None
    for unp_para in comprehensive_frames:
        unp_frame_data = unp_para.get("unp_frame_data", {})
        base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        if base_time_ms is None:
            if current_segment:
                current_segment = []
            continue

        matched_fusion = unp_para.get("matched_fusion", {})
        if not matched_fusion:
            if current_segment:
                current_segment = []
            continue

        matched_chassis = unp_para.get("matched_chassis", {})
        if matched_chassis is not None:
            f_stationary = matched_chassis.get("stationary", False)
            curr_vel = matched_chassis.get("vehicle_speed_average", 0)
        else:
            f_stationary = False
            curr_vel = 0

        fusion_ttc = matched_fusion.get("min_ttc", None)
        f_potential_cipv_by_pos = matched_fusion.get("f_potential_cipv_by_pos", False)
        f_np_on = unp_frame_data.get("NP_State", {}).get("f_Np_on", False)

        is_non_straight_driving = _is_non_straight_driving_scene(unp_frame_data)
        is_set_speed_stable = _is_set_speed_stable(unp_para, prev_unp_para)
        traffic_light = unp_frame_data.get("TrafficLight", {})
        f_redlight = traffic_light.get("f_RedLightOn", False)
        f_stop_dis = traffic_light.get("stop_distance", 999)
        
        #有效风险段初始条件
        is_valid_start = not f_stationary and curr_vel > C_min_start_vel
        
        # 判断是否是跟车
        is_risk = (fusion_ttc is not None and
                    (f_potential_cipv_by_pos or fusion_ttc < C_min_risk_ttc) and
                    (f_np_on == True and not is_non_straight_driving and is_set_speed_stable) and
                    f_stationary == False and
                    not (f_redlight and f_stop_dis < 10)
                    )

        if is_risk:
            if current_segment:
                current_segment.append(unp_para)
            else:
                if is_valid_start and _is_primary_sequence_frame(unp_para):
                    current_segment.append(unp_para)
        else:
            if current_segment:
                if len(current_segment) > C_min_risk_segment_length and f_stationary == True:
                    # 把首次 stationary 帧纳入场景，用于读取完成刹停后的前车距离。
                    current_segment.append(unp_para)
                    risk_segments.append(current_segment)
                current_segment = []

        prev_unp_para = unp_para

    print(f"————————————找到 {len(risk_segments)} 个跟停连续风险段落————————————————")
    return risk_segments

def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _measure_stable_stop_distance(segment: List[Dict], frames: List[Dict], stop_index: int) -> Dict[str, Any]:
    stop_time = lonprepro._get_unp_fusion_meta_time(segment[-1].get("unp_frame_data", {}))
    result: Dict[str, Any] = {"stop_time_ms": stop_time}

    def exclude(reason: str) -> Dict[str, Any]:
        result["excluded_reason"] = reason
        return result

    if not _finite(stop_time):
        return exclude("刹停时刻无效")
    target_id = segment[-1].get("matched_fusion", {}).get("closest_obj_id")
    if target_id is None:
        return exclude("缺少前车track_id，无法确认同一目标")
    end_time = stop_time + 2000.0
    distances: Dict[float, float] = {}
    previous_time = None
    reached_end = False
    for frame_index in range(stop_index, len(frames)):
        frame = frames[frame_index]
        unp = frame.get("unp_frame_data", {})
        timestamp = lonprepro._get_unp_fusion_meta_time(unp)
        chassis_time = lonprepro._get_unp_chassis_meta_time(unp)
        if not _finite(timestamp) or not _finite(chassis_time):
            return exclude("观察窗口缺少有效时间戳")
        if previous_time is not None:
            if timestamp < previous_time:
                return exclude("观察窗口时间戳倒序")
            if timestamp - previous_time > 200.0 + 1e-6:
                return exclude("观察窗口存在超过0.2秒采样缺口")
        previous_time = timestamp
        if abs(chassis_time - timestamp) > 200.0 + 1e-6:
            return exclude("观察窗口底盘与融合时间相差超过0.2秒")
        chassis = frame.get("matched_chassis") or {}
        fusion = frame.get("matched_fusion") or {}
        for data, reference_time in ((chassis, chassis_time), (fusion, timestamp)):
            matched_time = data.get("timestamp_ms")
            if not _finite(matched_time) or abs(matched_time - reference_time) > 200.0 + 1e-6:
                return exclude("观察窗口底盘或融合匹配数据缺失、过期")
        if not chassis.get("stationary", False):
            return exclude("观察窗口自车重新移动")
        if not unp.get("NP_State", {}).get("f_Np_on", False):
            return exclude("观察窗口NP退出")
        if fusion.get("closest_obj_id") is None or not fusion.get("has_valid_fusion_data", False):
            return exclude("观察窗口前车目标丢失或无效")
        if fusion["closest_obj_id"] != target_id:
            return exclude("观察窗口前车目标切换")
        velocity = fusion.get("closest_obj_vel")
        if not _finite(velocity):
            return exclude("观察窗口前车速度无效")
        if abs(velocity) > 0.2:
            return exclude("观察窗口前车未静止或重新起步")
        x, length = fusion.get("closest_obj_x"), fusion.get("closest_obj_len")
        if not _finite(x) or x <= 0 or not _finite(length) or length <= 0:
            return exclude("观察窗口前车距离或车长无效")
        distance = x - length / 2.0 - 3.8
        if stop_time + 1000.0 - 1e-6 <= timestamp <= end_time + 1e-6:
            distances[timestamp] = distance
        if timestamp >= end_time - 1e-6:
            reached_end = True
            break
    if not reached_end:
        return exclude("刹停后不足完整2秒数据")
    if len(distances) < 3:
        return exclude("稳定取值窗口有效采样不足3个")
    stop_frame = segment[-1]
    lateral = stop_frame.get("matched_fusion", {}).get("closest_obj_y")
    ego_speed = stop_frame.get("matched_chassis", {}).get("vehicle_speed_average")
    if not _finite(lateral) or not _finite(ego_speed):
        return exclude("刹停帧横向距离或自车速度无效")
    result.update(
        distance=float(statistics.median(distances.values())), sample_count=len(distances),
        lateral_distance=float(lateral), ego_speed=float(ego_speed),
    )
    return result
