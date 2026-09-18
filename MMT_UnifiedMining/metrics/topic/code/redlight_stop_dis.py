import os
import re
import traceback
import json
import time
from typing import List, Dict, Optional, Tuple, Any, Union
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

    # 4. 统计每个风险段落的stop_dis最小值
    min_stop_dis_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }
    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        (min_stop_dis, min_stop_dis_time, start_vel) = _calc_segment_min_stop_dis(segment)
        if min_stop_dis is not None:
            if not _check_min_stop_dis_valid(min_stop_dis, start_vel):
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(min_stop_dis, start_vel))
                check_result = True
                min_stop_dis_values.append(f"[StopDis Dangerous! time:{min_stop_dis_time} , stop_dis:{min_stop_dis:.6f}, start_vel: {start_vel:.6f}]")
            else:
                min_stop_dis_values.append(f"[time:{min_stop_dis_time} , stop_dis:{min_stop_dis:.6f}, start_vel: {start_vel:.6f}]")

    return (check_result, min_stop_dis_values, case_statistics)

def _check_min_stop_dis_valid(min_stop_dis: Optional[float], start_vel: Optional[float]) -> bool:
    """检查stop_dis是否在有效范围内"""
    C_stop_dis_lower_bound = 0
    C_stop_dis_upper_bound = 4

    if min_stop_dis is None or start_vel is None:
        return False
    return C_stop_dis_lower_bound <= min_stop_dis <= C_stop_dis_upper_bound

def _cal_severity_score(min_stop_dis: Optional[float], start_vel: Optional[float]) -> float:
    C_stop_dis_lower_bound = 0
    C_stop_dis_upper_bound = 4

    if min_stop_dis is None or start_vel is None:
        return 0.0
    if C_stop_dis_lower_bound <= min_stop_dis <= C_stop_dis_upper_bound:
        return 0.0
    else:
        C_cal_base = 2  #严重程度得分100分的越限基准
        if min_stop_dis < C_stop_dis_lower_bound:
            exceed_amount = C_stop_dis_lower_bound - min_stop_dis
        else:
            exceed_amount = min_stop_dis - C_stop_dis_upper_bound
        return abs(exceed_amount/C_cal_base)*100.0

# ------------------------------ 辅助函数：获取单帧风险判定参数 ------------------------------
def _get_frame_risk_params(comp_para: Dict) -> Dict:
    """提取单帧的风险判定参数，返回结构化字典"""
    unp_frame_data = comp_para.get("unp_frame_data", {})
    
    # 获取Fusion相关参数
    matched_fusion = comp_para.get("matched_fusion", {})
    fusion_ttc = matched_fusion["min_ttc"] if (matched_fusion and "min_ttc" in matched_fusion) else None
    f_potential_cipv_in_trafficlight = matched_fusion.get("f_potential_cipv_in_trafficlight", False) if matched_fusion else False
    
    # 获取Chassis相关参数
    matched_chassis = comp_para.get("matched_chassis", {})
    f_stationary = matched_chassis.get("stationary", False) if matched_chassis else False
    velocity = matched_chassis.get("vehicle_speed_average", 0.0) if matched_chassis else 0.0
    
    # 获取UNP相关参数
    f_np_on = unp_frame_data.get("NP_State", {}).get("f_Np_on", False)

    is_non_straight_driving = _is_non_straight_driving_scene(unp_frame_data)
    traffic_light = unp_frame_data.get("TrafficLight", {})
    f_redlight = traffic_light.get("f_RedLightOn", False)
    f_stop_dis = traffic_light.get("stop_distance", 999.0)
    
    return {
        "f_redlight": f_redlight,
        "f_stop_dis": f_stop_dis,
        "f_np_on": f_np_on,
        "is_non_straight_driving": is_non_straight_driving,
        "f_stationary": f_stationary,
        "velocity": velocity,
        "fusion_ttc": fusion_ttc,
        "f_potential_cipv_in_trafficlight": f_potential_cipv_in_trafficlight,
        "has_valid_data": bool(matched_fusion and unp_frame_data)
    }

def _find_risk_segments(
    comprehensive_frames: List[Dict]
) -> List[List[Dict]]:
    """
    查找连续风险段落：无前车红灯刹停
    核心逻辑修改：
    1. 当前帧不满足条件但current_segment有数据时，向后搜索20帧
    2. 若20帧内找到满足条件的帧t2，将t1-t2纳入风险段，从t2继续搜索
    3. 否则清空current_segment
    """
    risk_segments = []
    current_segment = []
    # 配置参数
    C_min_risk_segment_length = 5  # 有效风险段最小帧数
    C_min_risk_ttc = 2             # 最小风险TTC阈值
    C_min_start_vel = 2
    C_search_frames = 20           # 向后搜索帧数
    total_frames = len(comprehensive_frames)
    i = 0  # 帧遍历指针
    
    while i < total_frames:
        unp_para = comprehensive_frames[i]
        # 获取当前帧风险参数
        params = _get_frame_risk_params(unp_para)
        prev_frame = comprehensive_frames[i - 1] if i > 0 else None
        set_speed_stable = _is_set_speed_stable(unp_para, prev_frame)
        
        # 数据无效时，重置当前段并继续
        if not params["has_valid_data"]:
            if current_segment:
                current_segment = []
            i += 1
            continue
        
        # 判定核心风险条件
        is_no_cipv = ((params["fusion_ttc"] is None or params["fusion_ttc"] >= C_min_risk_ttc) and
                      not params["f_potential_cipv_in_trafficlight"])
        # 核心风险条件：红灯+近距离+NP开启+非静止+无前车
        core_risk_cond = (set_speed_stable and is_no_cipv and params["f_redlight"] and params["f_np_on"] and
                          not params["f_stationary"] and params["f_stop_dis"] < 150)
        
        #有效风险段初始条件
        is_valid_start = not params["f_stationary"] and params["velocity"] > C_min_start_vel
        
        if core_risk_cond:
            if current_segment:
                # 满足核心条件，加入当前段
                current_segment.append(unp_para)
                i += 1
            else:
                if is_valid_start and _is_primary_sequence_frame(unp_para):  #空集合的话需要第一个点满足有效条件
                    current_segment.append(unp_para)
                i += 1
        else:
            if current_segment:  # 当前段已有数据，触发向后搜索逻辑
                # 向后搜索最多20帧
                found_valid = False
                search_end = min(i + C_search_frames, total_frames)
                # 遍历搜索范围内的帧
                for j in range(i, search_end):
                    search_params = _get_frame_risk_params(comprehensive_frames[j])
                    # 检查搜索帧是否满足核心风险条件
                    search_is_no_cipv = ((search_params["fusion_ttc"] is None or search_params["fusion_ttc"] >= C_min_risk_ttc) and
                                         not search_params["f_potential_cipv_in_trafficlight"])
                    search_core_cond_hold = (search_is_no_cipv and search_params["f_np_on"] and
                                              _is_set_speed_stable(comprehensive_frames[j], comprehensive_frames[j - 1] if j > 0 else None) and
                                              not search_params["f_stationary"])
                    search_core_cond = (search_params["f_redlight"] and search_params["f_stop_dis"] < 150)
                    
                    if not search_core_cond_hold:   #必须一直满足
                        break

                    if search_core_cond:
                        # 找到满足条件的帧，将t1-t2全部加入当前段
                        current_segment.extend(comprehensive_frames[i:j+1])
                        found_valid = True
                        i = j + 1  # 从t2的下一帧继续搜索
                        break
                
                if not found_valid:
                    # 20帧内未找到满足条件的帧，检查当前段是否有效
                    if (len(current_segment) > C_min_risk_segment_length and
                        params["f_stationary"] and
                        is_no_cipv and
                        params["f_redlight"] and
                        params["f_np_on"]
                        ):
                        risk_segments.append(current_segment.copy())
                    current_segment = []
                    i += 1
            else:
                # 当前段无数据，直接跳过
                i += 1
    
    print(f"————————————找到 {len(risk_segments)} 个无前车红灯刹停连续风险段落————————————————")
    return risk_segments

def _calc_segment_min_stop_dis(
    segment: List[Dict]
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    stopdis_with_time = []
    start_vel = None  # 修复：从硬编码40改为None，避免类型错误
    for i, unp_para in enumerate(segment):
        unp_frame_data = unp_para["unp_frame_data"]  # 从Dict中取frame_data
        base_time_ms = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        if base_time_ms is None:
            print("当前unp段落无chassis基准时刻，跳过")
            continue
        
        stop_dis = unp_frame_data["TrafficLight"]["stop_distance"]
        stopdis_with_time.append((stop_dis, base_time_ms))

        if i == 0:
            matched_chassis = unp_para.get("matched_chassis", {})
            if matched_chassis:
                start_vel = matched_chassis["vehicle_speed_average"]  # 从Dict中取velocity

    if not stopdis_with_time:
        print("警告：当前风险段落无有效停止线距离数据")
        return (None, None, None)

    min_stop_dis_item = calculate_min_stop_dis_with_time(stopdis_with_time)
    if min_stop_dis_item is None:
        return (None, None, start_vel)
    
    min_stop_dis = min_stop_dis_item[0]
    min_stop_dis_time = min_stop_dis_item[1]

    C_stopdis_offset = 1.3

    return (min_stop_dis + C_stopdis_offset, min_stop_dis_time, start_vel)

def calculate_min_stop_dis_with_time(stop_dis_with_time: List[Tuple[Optional[float], Optional[float]]]) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """计算最小停止线距离及对应时间"""
    # ========== 步骤1：数据预处理（过滤无效值+排序+去重） ==========
    min_stop_dis = 1000.0
    min_stop_dis_time = None
    
    for stop_dis, t_ms in stop_dis_with_time:
        if (stop_dis is not None and t_ms is not None and 
            isinstance(stop_dis, (int, float)) and isinstance(t_ms, (int, float)) and 
            t_ms > 0):
            if stop_dis < min_stop_dis:
                min_stop_dis = stop_dis
                min_stop_dis_time = t_ms
    
    # 检查是否找到有效数据
    if min_stop_dis_time is None:
        print("警告：无有效停止线距离供计算")
        return None

    # 返回(最小stop_dis值, 对应时刻ms)的元组
    return (min_stop_dis, min_stop_dis_time)
