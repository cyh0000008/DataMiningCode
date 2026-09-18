import os
import re
import traceback
import json
import time
from typing import List, Dict, Optional, Tuple, Any, Union  # 新增Any/Union，适配3.8类型系统
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

    # 4. 统计每个风险段落的jerk最小值
    min_jerk_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }
    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        (min_jerk, min_jerk_time, start_vel) = _calc_segment_min_jerk(segment)
        if min_jerk is not None:
            if _check_min_jerk_valid(min_jerk,start_vel) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(min_jerk,start_vel))
                check_result = True
                min_jerk_values.append(f"[Dec Dangerous! time:{min_jerk_time} , jerk:{min_jerk:.6f}, start_vel: {start_vel:.6f}]")
            else:
                min_jerk_values.append(f"[time:{min_jerk_time} , jerk:{min_jerk:.6f}, start_vel: {start_vel:.6f}]")

    return (check_result, min_jerk_values, case_statistics)

def _check_min_jerk_valid(min_jerk: Optional[float],start_vel: Optional[float]) -> bool:
    C_jerk_lower_bound = -2

    if min_jerk >= C_jerk_lower_bound:
        return True
    else:
        return False
    
def _cal_severity_score(min_jerk: Optional[float],start_vel: Optional[float]) -> float:
    C_jerk_lower_bound = -2

    if min_jerk >= C_jerk_lower_bound:
        return 0.0
    else:
        C_cal_base = 1  #严重程度得分100分的越限基准
        return abs((min_jerk - C_jerk_lower_bound)/C_cal_base)*100.0

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

def _calc_segment_min_jerk(
    segment: List[Dict]
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """计算风险段落中jerk的最小值：匹配unp的chassis基准时刻（适配字典格式）"""
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

    # 修复17：处理calculate_min_smoothed_jerk_with_time返回None的情况
    min_jerk_item = calculate_min_smoothed_jerk_with_time(acc_with_time)
    if min_jerk_item is None:
        return (None, None, start_vel)
    
    min_jerk = min_jerk_item[0]
    min_jerk_time = min_jerk_item[1]

    return (min_jerk, min_jerk_time, start_vel)

def calculate_min_smoothed_jerk_with_time(acc_with_time: List[Tuple[Optional[float], Optional[float]]]) -> Optional[Tuple[Optional[float], Optional[float]]]:  # 补充返回值注解
    """
    跨平台计算acc_with_time中各点的平滑jerk值，返回(最小jerk值, 对应时刻ms)的元组
    仅使用Python内置模块，排除首尾点，jerk结果平滑
    :param acc_with_time: 列表，每个元素是(加速度, 时刻ms)的元组，值可能为None
    :return: 元组(最小平滑jerk值, 对应时刻ms)，无效数据返回None
    """
    # ========== 步骤1：数据预处理（过滤无效值+排序+去重） ==========
    valid_points = []
    for acc, t_ms in acc_with_time:
        if (acc is not None and t_ms is not None and 
            isinstance(acc, (int, float)) and isinstance(t_ms, (int, float)) and 
            t_ms > 0):
            valid_points.append((float(acc), float(t_ms)))
    
    if len(valid_points) < 3:
        print("警告：有效数据点不足3个，无法计算中间点的jerk")
        return None
    
    # 按时间排序
    valid_points.sort(key=lambda x: x[1])
    
    # 去重时间点
    unique_points = []
    seen_times = set()
    for acc, t_ms in reversed(valid_points):
        if t_ms not in seen_times:
            seen_times.add(t_ms)
            unique_points.append((acc, t_ms))
    unique_points.reverse()
    if len(unique_points) < 3:
        print("警告：去重后有效数据点不足3个")
        return None

    # ========== 步骤2：计算原始jerk值 + 关联对应时刻（排除首尾） ==========
    raw_jerk_with_time = []  # 存储(原始jerk, 对应时刻ms)
    time_list = [p[1] for p in unique_points]
    acc_list = [p[0] for p in unique_points]
    
    for i in range(1, len(unique_points)-1):  # 仅计算中间点
        t_prev = time_list[i-1] / 1000.0
        t_curr = time_list[i] / 1000.0
        t_next = time_list[i+1] / 1000.0
        
        # 避免除零
        dt_prev = t_curr - t_prev
        dt_next = t_next - t_curr
        if abs(dt_prev) < 1e-6 or abs(dt_next) < 1e-6:
            raw_jerk_with_time.append((0.0, time_list[i]))
            continue
        
        # 中心差分法计算jerk
        a_prev = acc_list[i-1]
        a_curr = acc_list[i]
        a_next = acc_list[i+1]
        delta_a = a_next - a_prev
        delta_t = t_next - t_prev
        jerk = delta_a / delta_t if abs(delta_t) > 1e-6 else 0.0
        
        # 存储jerk值 + 对应的原始时刻（ms）
        raw_jerk_with_time.append((jerk, time_list[i]))
    
    if not raw_jerk_with_time:
        print("警告：无有效中间点可计算jerk")
        return None

    # ========== 步骤3：平滑jerk值 + 保留时刻关联 ==========
    # 平滑处理，保留时刻关联
    C_sliding_window_size = 5
    smoothed_jerk_time = sliding_average_with_time(raw_jerk_with_time, C_sliding_window_size)

    # ========== 步骤4：找到最小jerk值 + 对应的时刻 ==========
    # 按jerk值找最小值（原逻辑是按绝对值，这里保持业务逻辑）
    min_jerk_item = min(smoothed_jerk_time, key=lambda x: x[0])
    min_jerk = min_jerk_item[0]
    min_jerk_time_ms = min_jerk_item[1]

    # 返回(最小jerk值, 对应时刻ms)的元组
    return (min_jerk, min_jerk_time_ms)

def sliding_average_with_time(jerk_time_list: List[Tuple[float, float]], window_size: int = 5) -> List[Tuple[float, float]]:
    """纯内置实现滑动平均，保留jerk与时刻的关联"""
    if window_size < 2 or len(jerk_time_list) <= window_size:
        return jerk_time_list.copy()
    
    # 拆分jerk和时刻，分别处理
    jerks = [jt[0] for jt in jerk_time_list]
    times = [jt[1] for jt in jerk_time_list]
    
    # 镜像填充jerk值（时刻无需填充，保持原顺序）
    pad_left = [jerks[0]] * (window_size // 2)
    pad_right = [jerks[-1]] * (window_size // 2)
    padded_jerks = pad_left + jerks + pad_right
    
    # 计算平滑jerk，并关联原时刻
    smoothed_jerk_time = []
    for i in range(len(jerks)):
        start = i
        end = start + window_size
        window_jerks = padded_jerks[start:end]
        smoothed_jerk = sum(window_jerks) / len(window_jerks)
        smoothed_jerk_time.append((smoothed_jerk, times[i]))
    
    return smoothed_jerk_time
