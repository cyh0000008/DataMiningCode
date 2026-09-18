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

    # 新增：定义阈值
    MAX_ACCELERATION_THRESHOLD = 3.0  # 最大加速度阈值 3m/s²
    MAX_DURATION_THRESHOLD = 2.5     # 最大起步时间阈值 2.5秒

    #计算每一个片段的红绿灯起步的耗时 + 最大加速度
    C_velocity_threshold = 0.2  # 车速阈值：0.2m/s
    stop2go_delaytime = 0.0  # 初始化为数值，避免后续字符串比较 【关键】
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
        
        # 4.2 查找起始时间后车速首次超过0.2m/s的时间戳（原有逻辑）
        velocity_over_time_ms = _find_velocity_over_threshold(
            chassis_paragraphs, 
            start_base_time_ms, 
            C_velocity_threshold
        )   

        # 4.3 计算耗时（原有逻辑）
        if velocity_over_time_ms is not None:
            duration_seconds = (velocity_over_time_ms - start_base_time_ms) / 1000
            if duration_seconds < MAX_DURATION_THRESHOLD:
                duration_str = f"片段{idx+1}：红绿灯起步耗时 {duration_seconds:.2f} 秒（从{start_base_time_ms:.0f}ms到{velocity_over_time_ms:.0f}ms，车速>{C_velocity_threshold}m/s）"
                stop2go_delaytime = duration_seconds  # 修正：赋值为数值，而非字符串 【关键】
            else:
                duration_str = f"片段{idx+1}：耗时超过{MAX_DURATION_THRESHOLD}秒"
                stop2go_delaytime = duration_seconds
        else:
            duration_str = f"片段{idx+1}：未找到车速>{C_velocity_threshold}m/s的时间点（起始时间：{start_base_time_ms:.0f}ms）"
            stop2go_delaytime = 0.0
        if stop2go_delaytime > 2.0:
            case_statistics["Bad Case"] += 1
            case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(stop2go_delaytime))
        
        # 4.4 新增：计算当前片段内的最大加速度
        max_acceleration, acc_data_count = _extract_acceleration_from_chassis(
            chassis_paragraphs, 
            start_base_time_ms, 
            end_base_time_ms  # 用片段结束时间限定加速度提取范围
        )

        if max_acceleration is not None:
            if max_acceleration < MAX_ACCELERATION_THRESHOLD:
                acc_str = f"片段{idx+1}：起步过程最大加速度 {max_acceleration:.3f} m/s²（时间范围：{start_base_time_ms:.0f}ms~{end_base_time_ms:.0f}ms）"
            else:
                acc_str = f"片段{idx+1}：加速数据异常（最大加速度{max_acceleration:.3f}≥阈值{MAX_ACCELERATION_THRESHOLD}，时间范围：{start_base_time_ms:.0f}ms~{end_base_time_ms:.0f}ms）"
        else:
            acc_str = f"片段{idx+1}：未提取到有效加速度数据（有效数据{acc_data_count}条）"
        
        stop2go_duration_list.append(duration_str)
        acceleration_max_list.append(acc_str)
        print(f"📈 {duration_str}")
        print(f"🚀 {acc_str}")

    # 5. 结果整合（合并耗时+最大加速度信息）
    if case_statistics["Bad Case"] > 0:
        check_result = True

    time_info_list.extend(stop2go_duration_list)
    time_info_list.extend(acceleration_max_list)  # 新增：将加速度信息加入返回列表

    return (check_result, time_info_list, case_statistics)

def _cal_severity_score(accestop2go_delaytimel_time: Optional[float]) -> float:
    C_min_acc = 2

    C_cal_base = 3  #严重程度得分100分的越限基准
    return abs((accestop2go_delaytimel_time - C_min_acc)/C_cal_base)*100.0

# 新增：提取指定时间范围内的底盘加速度并计算最大值
def _extract_acceleration_from_chassis(
    chassis_paragraphs: List[Dict], 
    start_time_ms: float, 
    end_time_ms: float
) -> Tuple[Optional[float], int]:
    """
    从chassis数据中提取指定时间范围内的加速度，返回最大值和有效数据条数
    :param chassis_paragraphs: 解析后的底盘数据列表（含timestamp_ms和acceleration）
    :param start_time_ms: 时间范围起始（ms）
    :param end_time_ms: 时间范围结束（ms）
    :return: (最大加速度值, 有效数据条数)，无有效数据则返回(None, 0)
    """
    # 过滤时间范围内的chassis数据
    time_filtered_chassis = [
        p for p in chassis_paragraphs 
        if start_time_ms <= p["timestamp_ms"] <= end_time_ms
    ]
    if not time_filtered_chassis:
        return None, 0
    
    # 提取有效加速度值（排除None/非数字）
    valid_accelerations = []
    for chassis_para in time_filtered_chassis:
        # 适配chassis数据中加速度字段的常见命名（根据实际字段名调整）
        acc_value = chassis_para.get("accleration_on_wheel")
        if acc_value is not None and isinstance(acc_value, (int, float)):
            valid_accelerations.append(acc_value)
    
    if not valid_accelerations:
        return None, 0
    
    # 返回最大值和有效数据条数
    max_acc = max(valid_accelerations)
    return max_acc, len(valid_accelerations)


def _find_velocity_over_threshold(chassis_paragraphs: List[Dict], start_time: float, threshold: float) -> Optional[float]:
    """
    优化：查找start_time之后，车速连续N帧超过threshold的第一个时间戳（避免偶发波动）
    :param chassis_paragraphs: chassis解析结果（已排序）
    :param start_time: 起始时间（stop_flag切换时间）
    :param threshold: 车速阈值（如0.2）
    :return: 首次超过阈值的时间戳（None表示未找到）
    """
    # 过滤出start_time之后的chassis段落
    post_switch_chassis = [p for p in chassis_paragraphs if p["timestamp_ms"] >= start_time]
    if not post_switch_chassis:
        return None

    # 新增：连续2帧超过阈值才判定为有效（可调整帧数）
    consecutive_count = 0
    C_consecutive_frames = 2
    for chassis_para in post_switch_chassis:
        if chassis_para["vehicle_speed_average"] is not None and chassis_para["vehicle_speed_average"] > threshold:
            consecutive_count += 1
            if consecutive_count >= C_consecutive_frames:
                return chassis_para["timestamp_ms"]
        else:
            consecutive_count = 0  # 重置连续计数

    return None

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
