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
        result_values = _calc_segment_min_ttc_frntdec(segment)
        if result_values is not None:
            if _check_min_ttc_frntdec_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                ttc_values.append(f"[TTC Dangerous! time:{result_values['time']} , ttc:{result_values['min_ttc']:.6f}, curr_vel: {result_values['ego_vel']:.6f}, obj_vel: {result_values['obj_vel']:.6f}, obj_lon_dis: {result_values['obj_lon_dis']:.6f}]")
            else:
                ttc_values.append(f"[time:{result_values['time']} , ttc:{result_values['min_ttc']:.6f}, curr_vel: {result_values['ego_vel']:.6f}, obj_vel: {result_values['obj_vel']:.6f}, obj_lon_dis: {result_values['obj_lon_dis']:.6f}]")
        else:
            ttc_values.append(f"不存在前车减速度≤-0.5开始，自车速度≥0.2的时段，不考虑")

    return (check_result, ttc_values, case_statistics)

def _check_min_ttc_frntdec_valid(result_values: Optional[Dict]) -> bool:
    C_ttc_lower_bound = 1.5

    if result_values["min_ttc"] >= C_ttc_lower_bound:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_ttc_lower_bound = 1.5

    if result_values["min_ttc"] >= C_ttc_lower_bound:
        return 0.0
    else:
        C_cal_base = 1  #严重程度得分100分的越限基准
        return abs((result_values["min_ttc"] - C_ttc_lower_bound)/C_cal_base)*100.0

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

# ========== 核心新增：新函数 _calc_segment_min_ttc_frntdec ==========
def _calc_segment_min_ttc_frntdec(
    segment: List[Dict]
) -> Optional[Dict]:
    """
    新函数：按起止点规则计算segment的最小min_ttc
    1. 找fusion中closest_obj_acc≤-0.5的第一个点作为起点
    2. 找chassis中vehicle_speed_average≥0.2的最后一个点作为终点
    3. 起止点无效则返回None，否则在范围内找min_ttc最小值
    """
    C_start_obj_acc = -0.5
    C_end_ego_vel = 0.2

    # 步骤1：收集segment中每个unp对应的fusion/chassis数据（带索引）
    segment_data = []
    for idx, unp_para in enumerate(segment):
        unp_frame_data = unp_para.get("unp_frame_data", {})
        # 获取fusion和chassis的基准时间
        fusion_base_time = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        chassis_base_time = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        
        # 匹配对应的fusion和chassis段落
        matched_fusion = unp_para.get("matched_fusion", {})
        matched_chassis = unp_para.get("matched_chassis", {})
        
        # 存储当前unp的索引、fusion/chassis数据
        segment_data.append({
            "idx": idx,
            "fusion": matched_fusion,
            "chassis": matched_chassis,
            "fusion_time": fusion_base_time,
            "chassis_time": chassis_base_time
        })
    
    # 步骤2：找起点（第一个fusion.closest_obj_acc ≤ -0.5的unp索引）
    start_idx = None
    for data in segment_data:
        fusion = data.get("fusion")
        if fusion and fusion.get("closest_obj_acc") is not None:
            if fusion["closest_obj_acc"] <= C_start_obj_acc:
                start_idx = data["idx"]
                break  # 找到第一个满足条件的就停止
    
    # 步骤3：找终点（最后一个chassis.vehicle_speed_average ≥ 0.2的unp索引）
    end_idx = None
    for data in reversed(segment_data):  # 倒序遍历找最后一个
        chassis = data.get("chassis")
        if chassis and chassis.get("vehicle_speed_average") is not None:
            if chassis["vehicle_speed_average"] >= C_end_ego_vel:
                end_idx = data["idx"]
                break  # 找到最后一个满足条件的就停止
    
    # 步骤4：校验起止点有效性
    if start_idx is None or end_idx is None or start_idx > end_idx:
        print("警告：当前风险段落无有效起止点（起点/终点不存在或起点>终点）")
        return None
    
    # 步骤5：在[start_idx, end_idx]范围内收集min_ttc，找最小值
    ttc_candidates = []
    for data in segment_data:
        idx = data["idx"]
        if start_idx <= idx <= end_idx:  # 仅处理范围内的unp
            fusion = data.get("fusion")
            if fusion and fusion.get("min_ttc") is not None:
                # 收集：(min_ttc, closest_obj_acc, vehicle_speed_average, fusion_time)
                chassis = data.get("chassis")
                vel = chassis["vehicle_speed_average"] if (chassis and chassis.get("vehicle_speed_average")) else None
                ttc_candidates.append({
                    "min_ttc": fusion["min_ttc"],
                    "obj_vel": fusion["closest_obj_vel"],
                    "obj_lon_dis": fusion["closest_obj_x"],
                    "ego_vel": vel,
                    "time": data["fusion_time"]
                })
    
    # 步骤6：处理候选结果
    if not ttc_candidates:
        print("警告：起止点范围内无有效min_ttc数据")
        return None
    
    # 找min_ttc最小的候选
    min_ttc_item = min(ttc_candidates, key=lambda x: x["min_ttc"])
    return min_ttc_item
