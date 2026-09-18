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

    # 3. 查找所有连续风险段落（基于_unp_planning_info的段落顺序）
    risk_segments = _find_risk_segments(comprehensive_frames)

    # 4. 统计每个风险段落的accleration_on_wheel最小值
    min_acc_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }
    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        (min_acc, min_acc_time, start_vel) = _calc_segment_min_acc(segment)
        if min_acc is not None:
            if _check_max_acc_valid(min_acc,start_vel) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(min_acc,start_vel))
                check_result = True
                min_acc_values.append(f"[Dec Dangerous! time:{min_acc_time} , acc:{min_acc:.6f}, start_vel: {start_vel:.6f}]")
            else:
                min_acc_values.append(f"[time:{min_acc_time} , acc:{min_acc:.6f}, start_vel: {start_vel:.6f}]")

    return (check_result, min_acc_values, case_statistics)

def _check_max_acc_valid(min_acc: Optional[float],start_vel: Optional[float]) -> bool:
    C_k = 5.5
    C_dec_upper_bound = -1.75
    C_dec_lower_bound = -4
    K_max_valid_dec = max(min(-start_vel/C_k,C_dec_upper_bound),C_dec_lower_bound)

    C_acc_offset = -1

    if min_acc >= K_max_valid_dec + C_acc_offset:
        return True
    else:
        return False
    
def _cal_severity_score(min_acc: Optional[float],start_vel: Optional[float]) -> float:
    C_k = 5.5
    C_dec_upper_bound = -1.75
    C_dec_lower_bound = -4
    K_max_valid_dec = max(min(-start_vel/C_k,C_dec_upper_bound),C_dec_lower_bound)

    C_acc_offset = -1

    if min_acc >= K_max_valid_dec + C_acc_offset:
        return 0.0
    else:
        C_cal_base = 2  #严重程度得分100分的越限基准
        return abs((min_acc - (K_max_valid_dec + C_acc_offset))/C_cal_base)*100.0

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
                    risk_segments.append(current_segment)
                current_segment = []

        prev_unp_para = unp_para

    print(f"————————————找到 {len(risk_segments)} 个跟停连续风险段落————————————————")
    return risk_segments

def _calc_segment_min_acc(
    segment: List[Dict]
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """计算风险段落中accleration_on_wheel的最小值：匹配unp的chassis基准时刻（适配字典格式）"""
    acc_with_time = []
    start_vel = 40
    for i, unp_para in enumerate(segment):
        unp_frame_data = unp_para.get("unp_frame_data", {})
        base_time_ms = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        if base_time_ms is None:
            print("当前unp段落无chassis基准时刻，跳过")
            continue

        matched_chassis = unp_para.get("matched_chassis", {})
        if matched_chassis:
            acc = matched_chassis.get("accleration_on_wheel")
            vel = matched_chassis.get("vehicle_speed_average")
            if acc is not None:
                acc_with_time.append((acc, base_time_ms))
                if i == 0 and vel is not None:
                    start_vel = vel

    if not acc_with_time:
        print("警告：当前风险段落无有效加速度数据")
        return (None, None, None)

    min_acc_item = min(acc_with_time, key=lambda x: x[0])
    min_acc = min_acc_item[0]
    min_acc_time = min_acc_item[1]

    return (min_acc, min_acc_time, start_vel)
