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

    # 4. 统计每个风险段落的前车距离最小值
    ttc_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }
    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        (curr_acc, curr_vel, curr_ttc, ttc_time) = _calc_segment_ttc(segment)
        if curr_ttc is not None:
            if _check_ttc_valid(curr_ttc) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(curr_ttc))
                check_result = True
                ttc_values.append(f"[TTC Dangerous! time:{ttc_time} , ttc:{curr_ttc:.6f}, curr_vel: {curr_vel:.6f}, curr_acc: {curr_acc:.6f}]")
            else:
                ttc_values.append(f"[time:{ttc_time} , ttc:{curr_ttc:.6f}, curr_vel: {curr_vel:.6f}, curr_acc: {curr_acc:.6f}]")
        else:
            ttc_values.append(f"不存在减速度≤-0.5的时刻，不考虑")

    return (check_result, ttc_values, case_statistics)

def _check_ttc_valid(ttc: Optional[float]) -> bool:
    C_ttc_lower_bound = 2.5

    if ttc >= C_ttc_lower_bound:
        return True
    else:
        return False

def _cal_severity_score(ttc: Optional[float]) -> float:
    C_ttc_lower_bound = 2.5

    if ttc >= C_ttc_lower_bound:
        return 0.0
    else:
        C_cal_base = 2  #严重程度得分100分的越限基准
        return abs((ttc - C_ttc_lower_bound)/C_cal_base)*100.0

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

def _calc_segment_ttc(
    segment: List[Dict]
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    ref_ego_dec = -0.5

    for i, unp_para in enumerate(segment):
        unp_frame_data = unp_para.get("unp_frame_data", {})
        base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        if base_time_ms is None:
            print("当前unp段落无fusion基准时刻，跳过")
            continue
        chassis_base_time_ms = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        if chassis_base_time_ms is None:
            print("当前unp段落无chassis基准时刻，跳过")
            continue

        matched_chassis = unp_para.get("matched_chassis", {})
        if matched_chassis:
            acc = matched_chassis.get("accleration_on_wheel")
            vel = matched_chassis.get("vehicle_speed_average")
            if acc is not None and vel is not None:
                if acc <= ref_ego_dec:
                    matched_fusion = unp_para.get("matched_fusion", {})
                    if matched_fusion:
                        curr_ttc = matched_fusion.get("min_ttc")
                        if curr_ttc is not None:
                            return (acc, vel, curr_ttc, base_time_ms)

    print("警告：当前风险段落无有效刹停反应数据")
    return (None, None, None, None)
