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
#Version2. 20260113 18:38
# 适配Python3.8 语法/类型注解/返回值 全修正
# 主要修改：引号嵌套、类型注解、返回值数量、变量判断、字典取值、异常兜底



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
    chassis_paragraphs: List[Dict] = []
    for comp_para in comprehensive_frames:
        chassis_paragraphs.append(comp_para.get("matched_chassis", {}))
    time_info_list: List[str] = []  # 存储超1.5秒的时间信息
    stop2go_duration_list: List[str] = []  # 新增：存储每个风险片段的红绿灯起步耗时
    acceleration_max_list: List[str] = []  # 新增：存储每个风险片段的最大加速度
    # 3. 查找所有连续风险段落（基于_unp_planning_info的段落顺序）
    risk_segments = _find_risk_segments(comprehensive_frames)

    case_statistics: Dict = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }

    for idx, segment in enumerate(risk_segments):
        if not segment:
            stop2go_duration_list.append(f"片段{idx+1}：无有效数据")
            acceleration_max_list.append(f"片段{idx+1}：无有效数据")
            continue
        
        # 4.1 提取片段时间范围（起始+结束）
        first_unp_para = segment[0].get("unp_frame_data", {})
        last_unp_para = segment[-1].get("unp_frame_data", {})
        # 修正：字典取值加兜底，避免KeyError 【关键】
        start_base_time_ms = lonprepro._get_unp_fusion_meta_time(first_unp_para)
        end_base_time_ms = lonprepro._get_unp_fusion_meta_time(last_unp_para)
        
        max_jerk_val , max_jerk_idx , start_vel = _calc_segment_max_jerk(segment,chassis_paragraphs)

        if max_jerk_val is not None:
            duration_str = f"第{idx+1}片段，最大的jerk为{max_jerk_val}，max_jerk_time_ms:{max_jerk_idx:.3f}"
            if max_jerk_val > 2.5 or max_jerk_val < 1.0:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(max_jerk_val))
        else:
            duration_str = f"第{idx+1}片段，数据异常"
        
        stop2go_duration_list.append(duration_str)
        print(f"📈 {duration_str}")

    # 5. 结果整合（合并耗时+最大加速度信息）
    if case_statistics["Bad Case"] > 0:
        check_result = True

    time_info_list.extend(stop2go_duration_list)
    time_info_list.extend(acceleration_max_list)  # 新增：将加速度信息加入返回列表

    return (check_result, time_info_list, case_statistics)

def _cal_severity_score(max_jerk_val: Optional[float]) -> float:
    C_min_acc = 1
    C_max_acc = 2.5

    C_cal_base = 1  #严重程度得分100分的越限基准
    if max_jerk_val < C_min_acc:
        exceed_amount = C_min_acc - max_jerk_val
    else:
        exceed_amount = max_jerk_val - C_max_acc
    return abs(exceed_amount/C_cal_base)*100.0

def _find_risk_segments(comprehensive_frames: List[Dict]) -> List[List[Dict]]:
    """
    识别绿灯起步风险段落：找到stop_flag从True→False的节点，追踪车速变化
    :return: 风险段落列表（每个元素是连续的unp段落）
    """
    risk_segments = []
    current_segment = []
    C_min_risk_segment_length = 10  # 有效风险段最小帧数
    C_max_velocity_upper_limit = 10.0  # 车速上限阈值
    f_redlight_prev = None
    f_stop_dis_prev = None
    is_recording = False
    current_max_velocity = 0.0
    velocity_history = []
    
    prev_unp_para = None
    for unp_para in comprehensive_frames:
        unp_frame_data = unp_para.get("unp_frame_data", {})
        base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        if base_time_ms is None:
            continue
        # 匹配融合和底盘数据
        matched_fusion = unp_para.get("matched_fusion", {})
        matched_chassis = unp_para.get("matched_chassis", {})
        if not matched_fusion or not matched_chassis:
            continue
        # 提取核心参数
        velocity_value = matched_chassis.get("vehicle_speed_average", 0.0)
        f_stationary = matched_chassis.get("stationary", False)
        f_np_on = unp_frame_data.get("NP_State", {}).get("f_Np_on", False)

        is_non_straight_driving = _is_non_straight_driving_scene(unp_frame_data)
        is_set_speed_stable = _is_set_speed_stable(unp_para, prev_unp_para)
        f_redlight = unp_frame_data.get("TrafficLight", {}).get("f_RedLightOn", False)
        f_stop_dis = unp_frame_data.get("TrafficLight", {}).get("stop_distance", 1000.0)
        f_potential_cipv_in_trafficlight = matched_fusion.get("f_potential_cipv_in_trafficlight", False)

        # 判定绿灯起步触发条件
        is_traffic_light_stop2go = (
            f_redlight_prev is True and
            f_redlight is False and
            (f_np_on is True and not is_non_straight_driving and is_set_speed_stable) and
            f_stationary is True and
            f_stop_dis_prev < 5.0 and
            not f_potential_cipv_in_trafficlight
        )

        # 场景1：触发绿灯起步，开始记录
        if is_traffic_light_stop2go and not is_recording and _is_primary_sequence_frame(unp_para):
            is_recording = True
            current_segment.append(unp_para)
            current_max_velocity = velocity_value if velocity_value is not None else 0.0
            velocity_history = [current_max_velocity]
            print(f"📌{base_time_ms:.0f}ms 触发绿灯起步，开启记录（初始车速：{current_max_velocity:.2f}m/s）")
        # 场景2：正在记录，持续缓存
        elif is_recording and is_set_speed_stable:
            if velocity_value is not None:
                velocity_history.append(velocity_value)
                current_max_velocity = max(current_max_velocity, velocity_value)
            current_segment.append(unp_para)
            # 判定停止记录条件：车速到上限 或 连续3帧下降
            is_velocity_drop = len(velocity_history) >= 3 and velocity_history[-1] < velocity_history[-2] < velocity_history[-3]
            reach_upper_limit = current_max_velocity >= C_max_velocity_upper_limit
            if reach_upper_limit or is_velocity_drop:
                # 有效片段才归档
                if len(current_segment) > C_min_risk_segment_length:
                    risk_segments.append(current_segment.copy())
                    print(f"✅ 归档绿灯起步片段（帧数：{len(current_segment)}，最大车速：{current_max_velocity:.2f}m/s）")
                # 重置状态
                is_recording = False
                current_segment = []
                current_max_velocity = 0.0
                velocity_history = []
        # 场景3：未触发，清空无效缓存
        else:
            current_segment = []
            velocity_history = []

        prev_unp_para = unp_para

        # 更新上一帧状态
        f_redlight_prev = f_redlight
        f_stop_dis_prev = f_stop_dis

    # 处理末尾未完成的记录
    if is_recording and len(current_segment) > C_min_risk_segment_length:
        risk_segments.append(current_segment)
        print(f"⚠️  数据结束，归档未完成的绿灯起步片段（帧数：{len(current_segment)}）")

    print(f"————————————共找到 {len(risk_segments)} 个完整的绿灯起步场景————————————")
    return risk_segments

def _calc_segment_max_jerk(segment: List[Dict], chassis_paragraphs: List[Dict]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """计算风险段落的最小平滑jerk值"""
    acc_with_time = []
    start_vel = None
    for i, unp_para in enumerate(segment):
        base_time_ms = lonprepro._get_unp_chassis_meta_time(unp_para.get("unp_frame_data", {}))
        if base_time_ms is None:
            continue
        matched_chassis = unp_para.get("matched_chassis", {})
        if matched_chassis:
            acc = matched_chassis.get("accleration_on_wheel")
            if acc is not None:
                acc_with_time.append((acc, base_time_ms))
            if i == 0:
                start_vel = matched_chassis.get("vehicle_speed_average")

    if not acc_with_time:
        print("警告：当前风险段落无有效加速度数据")
        return (None, None, start_vel)

    max_jerk_item = calculate_max_smoothed_jerk_with_time(acc_with_time)
    return (max_jerk_item[0], max_jerk_item[1], start_vel) if max_jerk_item else (None, None, start_vel)

def calculate_max_smoothed_jerk_with_time(acc_with_time: List[Tuple[Optional[float], Optional[float]]]) -> Optional[Tuple[float, float]]:
    """计算平滑后的最小jerk值及对应时刻，适配Python3.8"""
    # 过滤有效数据
    valid_points = [
        (float(acc), float(t_ms)) for acc, t_ms in acc_with_time
        if acc is not None and t_ms is not None and t_ms > 0
    ]
    if len(valid_points) < 3:
        print("警告：有效加速度点不足3个，无法计算jerk")
        return None
    # 排序去重
    valid_points.sort(key=lambda x: x[1])
    unique_points = []
    seen_times = set()
    for acc, t_ms in reversed(valid_points):
        if t_ms not in seen_times:
            seen_times.add(t_ms)
            unique_points.append((acc, t_ms))
    unique_points.reverse()
    if len(unique_points) < 3:
        return None

    # 计算原始jerk
    raw_jerk_with_time = []
    time_list = [p[1] for p in unique_points]
    acc_list = [p[0] for p in unique_points]
    for i in range(1, len(unique_points)-1):
        t_prev, t_curr, t_next = time_list[i-1]/1000, time_list[i]/1000, time_list[i+1]/1000
        delta_t = t_next - t_prev
        if abs(delta_t) < 1e-6:
            raw_jerk_with_time.append((0.0, time_list[i]))
            continue
        # 中心差分法
        jerk = (acc_list[i+1] - acc_list[i-1]) / delta_t
        raw_jerk_with_time.append((jerk, time_list[i]))

    if not raw_jerk_with_time:
        return None
    # 滑动平均平滑
    smoothed_jerk = sliding_average_with_time(raw_jerk_with_time, window_size=5)
    # 找到最小jerk
    max_jerk_item = max(smoothed_jerk, key=lambda x: x[0])
    return (max_jerk_item[0], max_jerk_item[1])

def sliding_average_with_time(jerk_time_list: List[Tuple[float, float]], window_size: int = 5) -> List[Tuple[float, float]]:
    """滑动平均，保留时刻关联，纯Python3.8内置实现"""
    if window_size < 2 or len(jerk_time_list) <= window_size:
        return jerk_time_list.copy()
    # 拆分数据
    jerks, times = [j for j, t in jerk_time_list], [t for j, t in jerk_time_list]
    # 镜像填充
    pad = window_size // 2
    padded_jerks = [jerks[0]] * pad + jerks + [jerks[-1]] * pad
    # 计算滑动平均
    smoothed = []
    for i in range(len(jerks)):
        window = padded_jerks[i:i+window_size]
        smoothed.append((sum(window)/len(window), times[i]))
    return smoothed
