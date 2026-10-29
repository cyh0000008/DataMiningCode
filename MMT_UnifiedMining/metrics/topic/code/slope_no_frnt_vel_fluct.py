import math
from slope_scene_utils import find_steady_slope_segments
import os
import re
import traceback
import json
import time
from typing import List, Dict, Optional, Tuple, Any, Union  # 新增Any/Union，修复3.8兼容
import sys
current_dir = os.path.abspath(os.getcwd())
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
import longitudinal_preprocessor as lonprepro
from powertrain_utils import get_powertrain_type, value_for_powertrain


def _is_non_straight_driving_scene(unp_frame_data: Dict) -> bool:
    return lonprepro.is_non_straight_driving_scene(unp_frame_data)








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
    powertrain_type = get_powertrain_type(comprehensive_frames)
    fluct_threshold = value_for_powertrain(powertrain_type, ice_value=0.6, phev_value=0.3, bev_value=0.3)

    # 3. 查找所有连续风险段落（基于_unp_planning_info的段落顺序）
    risk_segments = _find_risk_segments(comprehensive_frames)

    # 4. 统计每个风险段落的前车距离最小值
    jerk_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }
    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        result_values = _calc_segment_max_vel_fluct(segment)
        if result_values:
            if _check_max_vel_fluct_valid(result_values, fluct_threshold) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values, fluct_threshold))
                check_result = True
                jerk_values.append(f"[Vel Fluct Dangerous! setspeed_kph:{result_values['set_speed']} , max_vel_diff:{result_values['max_vel_diff']:.6f}, min_vel_kph:{result_values['min_vel_chassis']:.6f}, min_vel_time_ms:{result_values['min_vel_chassis_time']:.3f}, max_vel_kph:{result_values['max_vel_chassis']:.6f}, max_vel_time_ms:{result_values['max_vel_chassis_time']:.3f}]")
            else:
                jerk_values.append(f"[setspeed_kph:{result_values['set_speed']} , max_vel_diff:{result_values['max_vel_diff']:.6f}, min_vel_kph:{result_values['min_vel_chassis']:.6f}, min_vel_time_ms:{result_values['min_vel_chassis_time']:.3f}, max_vel_kph:{result_values['max_vel_chassis']:.6f}, max_vel_time_ms:{result_values['max_vel_chassis_time']:.3f}]")
        else:
            jerk_values.append(f"当前段落算不出jerk")

    return (check_result, jerk_values, case_statistics)

def _check_max_vel_fluct_valid(result_values: Optional[Dict], C_fluct_upper_bound: float) -> bool:

    if result_values["vel_chassis_diff"] <= C_fluct_upper_bound:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict], C_fluct_upper_bound: float) -> float:

    if result_values["vel_chassis_diff"] <= C_fluct_upper_bound:
        return 0.0
    else:
        C_cal_base = 2  #严重程度得分100分的越限基准
        return abs((result_values["vel_chassis_diff"] - C_fluct_upper_bound)/C_cal_base)*100.0

# ------------------------------ 辅助函数：获取单帧风险判定参数 ------------------------------
def _get_frame_risk_params(comp_frame: Dict) -> Dict:
    """
    提取单帧的风险判定参数（修改版）
    输入改为comprehensive_frames中的单个帧字典，直接复用已匹配的数据
    """
    # 【修改1】从comp_frame中直接获取UNP数据（不再传多个paragraphs）
    unp_frame_data = comp_frame.get("unp_frame_data", {})
    
    # 【修改2】直接从comp_frame获取已匹配的模块数据，无需重新计算基准时间和匹配
    matched_fusion = comp_frame.get("matched_fusion")
    matched_chassis = comp_frame.get("matched_chassis")
    matched_msd_control = comp_frame.get("matched_msd_control")

    # 获取Fusion相关参数（逻辑不变，仅数据源改为已匹配的结果）
    fusion_ttc = matched_fusion["min_ttc"] if (matched_fusion and "min_ttc" in matched_fusion) else None
    f_potential_cipv_in_trafficlight = matched_fusion.get("f_potential_cipv_in_trafficlight", False) if matched_fusion else False
    f_potential_cipv_by_pos = matched_fusion.get("f_potential_cipv_by_pos", False) if matched_fusion else False
    closest_obj_x = matched_fusion.get("closest_obj_x", 0) if matched_fusion else 0
    closest_obj_vel = matched_fusion.get("closest_obj_vel", 0) if matched_fusion else 0
    closest_obj_acc = matched_fusion.get("closest_obj_acc", 0) if matched_fusion else 0
    
    # 获取Chassis相关参数（逻辑不变，数据源改为已匹配的结果）
    f_stationary = matched_chassis.get("stationary", False) if matched_chassis else False
    velocity = matched_chassis.get("vehicle_speed_average", 0) if matched_chassis else 0
    acceleration = matched_chassis.get("accleration_on_wheel", 0) if matched_chassis else 0
    
    # 获取UNP相关参数（逻辑完全不变）
    f_np_on = lonprepro.is_acc_controlling(comp_frame)

    is_non_straight_driving = _is_non_straight_driving_scene(unp_frame_data)
    traffic_light = unp_frame_data.get("TrafficLight", {})
    f_redlight = traffic_light.get("f_RedLightOn", False)
    f_stop_dis = traffic_light.get("stop_distance", 999)
    f_curv_limit_enable = unp_frame_data.get("curvature_info", {}).get("is_curv_v_limit_enable", False)
    curv_spd_limit = unp_frame_data.get("curvature_info", {}).get("curv_velocity_limit", 100.0)
    if f_curv_limit_enable is not True:
        curv_spd_limit = 100.0

    # 新增：提取SetSpeed（UNP帧内的目标速度）
    set_speed = unp_frame_data.get("Acc_info", {}).get("SetSpeed", 0.0)
    
    # 新增：提取msd_control的slope_acc（数据源改为已匹配的结果）
    slope_acc = matched_msd_control.get("slope_acc", 0.0) if matched_msd_control else 0.0
    
    return {
        "f_redlight": f_redlight,
        "f_stop_dis": f_stop_dis,
        "f_np_on": f_np_on,
        "is_non_straight_driving": is_non_straight_driving,
        "f_stationary": f_stationary,
        "velocity": velocity,
        "accel": acceleration,
        "fusion_ttc": fusion_ttc,
        "f_potential_cipv_in_trafficlight": f_potential_cipv_in_trafficlight,
        "f_potential_cipv_by_pos": f_potential_cipv_by_pos,
        "closest_obj_x": closest_obj_x,
        "closest_obj_vel": closest_obj_vel,
        "closest_obj_acc": closest_obj_acc,
        "set_speed": set_speed,
        "slope_acc": slope_acc,
        "curv_spd_limit": curv_spd_limit,
        # 【修改3】数据有效性判断改为检查comp_frame中的匹配结果
        "has_valid_data": bool(matched_fusion and unp_frame_data and matched_chassis and matched_msd_control)
    }

def _find_risk_segments(comprehensive_frames: List[Dict]) -> List[List[Dict]]:
    return find_steady_slope_segments(comprehensive_frames, _get_frame_risk_params)

def _calc_segment_max_vel_fluct(segment: List[Dict]) -> Optional[Dict]:
    values = []
    for frame in segment:
        body = frame.get("matched_body") or {}
        speed = body.get("velocity_on_dashboard")
        timestamp = frame.get("unp_timestamp_ms")
        if speed is not None and timestamp is not None and math.isfinite(speed) and math.isfinite(timestamp):
            values.append((float(speed), float(timestamp)))
    if not values:
        print("警告：稳速段无有效仪表车速数据")
        return None
    maximum, maximum_time = max(values, key=lambda item: item[0])
    minimum, minimum_time = min(values, key=lambda item: item[0])
    return {
        "max_vel_chassis": maximum,
        "max_vel_chassis_time": maximum_time,
        "min_vel_chassis": minimum,
        "min_vel_chassis_time": minimum_time,
        "vel_chassis_diff": maximum - minimum,
        "max_vel_diff": maximum - minimum,
        "set_speed": segment[0]["unp_frame_data"]["Acc_info"]["SetSpeed"] * 3.6,
    }
