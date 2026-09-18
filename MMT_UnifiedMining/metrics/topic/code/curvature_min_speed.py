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
    jerk_values: List[str] = []
    case_statistics: Dict = {}

    case_statistics = {
        "All Case Count": len(risk_segments),
        "Bad Case": 0,
        "Severity Score": float(0.0)
    }

    for i,segment in enumerate(risk_segments):
        print(f"处理第 {i+1} 个风险段落（含 {len(segment)} 个unp段落）")
        result_values = _calc_segment_curv_speed(segment)
        if result_values:
            if _check_min_curv_speed_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                jerk_values.append(f"[Min Curv Speed Dangerous! setspeed_mps:{result_values['set_speed_at_min_speed']} , min_speed:{result_values['min_vehicle_speed']:.6f}, min_curv_radius:{result_values['min_curvature_radius']:.2f}, min_spd_time:{result_values['min_speed_time']:.6f}]")
            else:
                jerk_values.append(f"[setspeed_mps:{result_values['set_speed_at_min_speed']} , min_speed:{result_values['min_vehicle_speed']:.6f}, min_curv_radius:{result_values['min_curvature_radius']:.2f}, min_spd_time:{result_values['min_speed_time']:.6f}]")
        else:
            jerk_values.append(f"当前段落无有效弯道限速")

    return (check_result, jerk_values, case_statistics)

def _check_min_curv_speed_valid(result_values: Optional[Dict]) -> bool:

    min_speed = result_values["min_vehicle_speed"]
    min_curv_radius = result_values["min_curvature_radius"]
    set_speed = result_values["set_speed_at_min_speed"]

    lower_bound = pow(1.0*min_curv_radius,1/2)
    upper_bound = pow(2.9*min_curv_radius,1/2)

    C_vel_offset = 2

    if (min_speed >= lower_bound - C_vel_offset and min_speed <= upper_bound + C_vel_offset) or (set_speed <= lower_bound):
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    min_speed = result_values["min_vehicle_speed"]
    min_curv_radius = result_values["min_curvature_radius"]
    set_speed = result_values["set_speed_at_min_speed"]

    lower_bound = pow(1.0*min_curv_radius,1/2)
    upper_bound = pow(2.9*min_curv_radius,1/2)

    C_vel_offset = 2

    if (min_speed >= lower_bound - C_vel_offset and min_speed <= upper_bound + C_vel_offset) or (set_speed <= lower_bound):
        return 0.0
    else:
        C_cal_base = 5  #严重程度得分100分的越限基准
        lower_limit = lower_bound - C_vel_offset
        upper_limit = upper_bound + C_vel_offset
        if min_speed < lower_limit:
            exceed_amount = lower_limit - min_speed
        else:
            exceed_amount = min_speed - upper_limit
        return abs(exceed_amount/C_cal_base)*100.0

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
    f_np_on = unp_frame_data.get("NP_State", {}).get("f_Np_on", False)

    is_non_straight_driving = _is_non_straight_driving_scene(unp_frame_data)
    traffic_light = unp_frame_data.get("TrafficLight", {})
    f_redlight = traffic_light.get("f_RedLightOn", False)
    f_stop_dis = traffic_light.get("stop_distance", 999)
    f_curvature_control_on = unp_frame_data.get("curvature_info", {}).get("is_curv_v_limit_enable", False)
    curvature_val = unp_frame_data.get("curvature_info", {}).get("max_curvature", 0.0)
    curvature_spdlmt = unp_frame_data.get("curvature_info", {}).get("curv_velocity_limit", 100.0)

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
        "f_curvature_control_on": f_curvature_control_on,
        "curvature_val": curvature_val,
        "curvature_spdlmt": curvature_spdlmt,
        # 【修改3】数据有效性判断改为检查comp_frame中的匹配结果
        "has_valid_data": bool(matched_fusion and unp_frame_data and matched_chassis and matched_msd_control)
    }

def _find_risk_segments(
    comprehensive_frames: List[Dict]  # 【修改1】替换原有4个paragraphs参数
) -> List[List[Dict]]:
    """
    最终规则查找连续风险段落（修改版）
    输入改为comprehensive_frames，内部遍历综合帧数据，复用已匹配的结果
    """
    # ====================== Debug 配置（保留原有）=====================
    DEBUG_MODE = True
    DEBUG_MODE = False
    DEBUG_FILE_NAME = "risk_segment_debug.txt"
    DEBUG_FILE_PATH = os.path.join(os.getcwd(), DEBUG_FILE_NAME)
    debug_file = None

    if DEBUG_MODE:
        debug_file = open(DEBUG_FILE_PATH, 'w', encoding='utf-8')
        debug_file.write("===== 最终规则风险段落搜索调试日志（含slope_acc例外逻辑）=====\n\n")

    def write_debug_log(content: str):
        if DEBUG_MODE and debug_file:
            debug_file.write(content + '\n')

    # ====================== 核心配置（简化版）=====================
    risk_segments = []
    current_candidate = []  # 存储连续满足条件的综合帧
    C_min_risk_segment_length = 50  # 有效风险段落最小帧数（可根据需求调整）
    C_min_ttc = 3.5
    C_min_stop_dis = 50
    C_min_curv_frames = 5  # 段落内需满足curvature_val>0.004的最小帧数
    C_CURVATURE_THRESH = 0.004  # 曲率阈值

    # ====================== 遍历每帧：核心逻辑（新规则）=====================
    try:
        total_frames = len(comprehensive_frames)
        write_debug_log(f"[DEBUG] 开始遍历，总帧数：{total_frames}")

        for idx, comp_frame in enumerate(comprehensive_frames):
            # 提取单帧风险参数（仅保留核心字段）
            curr_params = _get_frame_risk_params(comp_frame)
            set_speed_stable = _is_set_speed_stable(comp_frame, comprehensive_frames[idx - 1] if idx > 0 else None)

            # -------------------- Debug 日志：当前帧核心参数 --------------------
            write_debug_log(f"\n[DEBUG] 处理第 {idx} 帧（UNP时间戳：{comp_frame.get('unp_timestamp_ms', '未知')}）：")
            write_debug_log(f"  - 基础条件：f_np_on={curr_params['f_np_on']} | f_curvature_control_on={curr_params['f_curvature_control_on']}")
            write_debug_log(f"  - 新增条件1：fusion_ttc={curr_params['fusion_ttc']}（需>10）")
            write_debug_log(f"  - 新增条件2：f_redlight={curr_params['f_redlight']} | f_stop_dis={curr_params['f_stop_dis']}（需不同时满足redlight=True+stop_dis<50）")
            write_debug_log(f"  - 新增条件3：f_stationary={curr_params['f_stationary']}（需=False）")
            write_debug_log(f"  - 曲率参数：curvature_val={curr_params['curvature_val']:.6f}（阈值={C_CURVATURE_THRESH}）")
            write_debug_log(f"  - 有效数据：{curr_params['has_valid_data']}")

            # 新规则：单帧风险条件（仅判断两个核心字段，且数据有效）
            frame_risk_condition = (
                curr_params["has_valid_data"] is True  # 确保UNP数据存在，避免空值判断
                and (curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False)
                and curr_params["f_curvature_control_on"] is True
                and curr_params["f_stationary"] is False
                and not (curr_params["fusion_ttc"] and curr_params["fusion_ttc"] < C_min_ttc)
                and not (curr_params["f_redlight"] is True and curr_params["f_stop_dis"] < C_min_stop_dis) 
            )

            # 处理连续候选段落
            if frame_risk_condition and (current_candidate or _is_primary_sequence_frame(comp_frame)):
                # 满足条件：加入当前候选段落
                current_candidate.append(comp_frame)
                write_debug_log(f"[DEBUG] 第 {idx} 帧 - 满足新规则风险条件 → 加入候选（当前长度：{len(current_candidate)}）")
            else:
                # 不满足条件：检查当前候选段落是否有效
                write_debug_log(f"[DEBUG] 第 {idx} 帧 - 不满足新规则风险条件 → 终止候选段落（当前长度：{len(current_candidate)}）")
                
                # 仅当候选段落长度≥最小阈值时，视为有效风险段落
                if len(current_candidate) >= C_min_risk_segment_length:
                    write_debug_log(f"[DEBUG] 候选段落长度≥{C_min_risk_segment_length} → 开始统计曲率达标帧数...")
                    
                    # 2. 统计段落内满足curvature_val>0.004的帧数
                    curv_qualified_frames = 0
                    for candidate_frame in current_candidate:
                        frame_params = _get_frame_risk_params(candidate_frame)
                        if frame_params["curvature_val"] > C_CURVATURE_THRESH:
                            curv_qualified_frames += 1
                    
                    write_debug_log(f"[DEBUG] 候选段落内曲率>0.004的帧数：{curv_qualified_frames}（需≥{C_min_curv_frames}）")
                    
                    # 3. 段落级条件：曲率达标帧数≥5
                    if curv_qualified_frames >= C_min_curv_frames:
                        risk_segments.append(current_candidate.copy())
                        write_debug_log(f"[DEBUG] 候选段落满足所有段落级条件 → 加入风险段落列表")
                    else:
                        write_debug_log(f"[DEBUG] 候选段落曲率达标帧数不足 → 放弃")
                else:
                    write_debug_log(f"[DEBUG] 候选段落长度<{C_min_risk_segment_length} → 放弃")
                
                # 重置候选段落
                current_candidate = []

        # 遍历结束后：检查最后一个候选段落（避免遗漏末尾的连续段落）
        write_debug_log(f"\n[DEBUG] 遍历结束 - 剩余候选段落长度：{len(current_candidate)}")
        if len(current_candidate) >= C_min_risk_segment_length:
            # 统计最后一个候选段落的曲率达标帧数
            curv_qualified_frames = 0
            for candidate_frame in current_candidate:
                frame_params = _get_frame_risk_params(candidate_frame)
                if frame_params["curvature_val"] > C_CURVATURE_THRESH:
                    curv_qualified_frames += 1
            write_debug_log(f"[DEBUG] 剩余候选段落曲率>{C_CURVATURE_THRESH}的帧数：{curv_qualified_frames}（需≥{C_min_curv_frames}）")
            
            if curv_qualified_frames >= C_min_curv_frames:
                risk_segments.append(current_candidate.copy())
                write_debug_log(f"[DEBUG] 剩余候选段落满足所有条件 → 加入风险段落列表")
            else:
                write_debug_log(f"[DEBUG] 剩余候选段落曲率达标帧数不足 → 放弃")
        else:
            write_debug_log(f"[DEBUG] 剩余候选段落长度<{C_min_risk_segment_length} → 放弃")

        # 重置最后候选段落
        current_candidate = []

        # -------------------- Debug 日志：最终结果 --------------------
        write_debug_log(f"\n[DEBUG] 新规则搜索完成 - 共找到 {len(risk_segments)} 个有效风险段落")
        if DEBUG_MODE:
            write_debug_log("\n===== 调试日志结束 =====")
            print(f"[提示] Debug日志已写入：{DEBUG_FILE_PATH}")

    finally:
        if DEBUG_MODE and debug_file:
            debug_file.close()

    print(f"————————————找到 {len(risk_segments)} 个符合规则的无干扰弯道控速段落—————————————")
    
    return risk_segments

def _calc_segment_curv_speed(segment: List[Dict]) -> Optional[Dict]:
    """
    计算风险段落的曲率半径、纵向加速度及最小速度相关参数
    核心功能：
    1. 计算段落内最大曲率对应的曲率半径
    2. 计算每一帧的真实纵向加速度a = accleration_on_wheel + slope_acc（减速度 = -a）
    3. 找到减速度从≤-0.5降到≥-0.1的转折点，在转折点后abs(减速度)<0.1的段落内找最小车速
    4. 返回包含曲率半径、最小车速及对应参数的字典
    """
    # ========== 步骤1：提取并验证段落基础数据 ==========
    if not segment:
        print("警告：输入的segment为空，无数据可处理")
        return None

    # 存储关键数据的列表
    frame_data_list = []  # 存储每帧的核心计算数据
    curvature_values = []  # 存储每帧的max_curvature（用于找最大k）

    # ========== 步骤2：遍历每帧，提取数据并计算中间值 ==========
    for frame_idx, frame in enumerate(segment):
        # 提取各模块数据（处理None，避免KeyError）
        unp_frame_data = frame.get("unp_frame_data", {})
        matched_msd_control = frame.get("matched_msd_control", {})
        matched_chassis = frame.get("matched_chassis", {})

        # -------------------- 2.1 提取曲率值（用于计算r=1/k） --------------------
        max_curvature = unp_frame_data.get("curvature_info", {}).get("max_curvature", 0.0)
        if max_curvature > 0:  # 仅收集非零曲率（避免后续除零）
            curvature_values.append(max_curvature)

        # -------------------- 2.2 计算真实纵向加速度a（减速度 = -a） --------------------
        # 提取加速度相关参数（默认0.0，避免None导致计算错误）
        accleration_on_wheel = matched_chassis.get("accleration_on_wheel", 0.0)
        slope_acc = matched_msd_control.get("slope_acc", 0.0)
        deceleration = accleration_on_wheel + slope_acc  # 真实纵向加速度a

        # -------------------- 2.3 提取速度、时间及SetSpeed --------------------
        vehicle_speed_average = matched_chassis.get("vehicle_speed_average", None)
        set_speed = unp_frame_data.get("Acc_info", {}).get("SetSpeed", 0.0)
        chassis_time = matched_chassis.get("timestamp_ms", None)  # 处理None，避免报错

        # -------------------- 2.4 存储当前帧的所有计算数据 --------------------
        frame_data = {
            "deceleration": deceleration,    # 减速度（-a）
            "vehicle_speed": vehicle_speed_average,  # 平均车速
            "slope_acc": slope_acc,  # 坡度加速度
            "accleration_on_wheel": accleration_on_wheel,  # 轮端加速度
            "set_speed": set_speed,  # 目标速度SetSpeed
            "frame_idx": frame_idx,  # 帧索引（便于追溯）
            "chassis_time": chassis_time  # 底盘时间戳
        }
        frame_data_list.append(frame_data)

    # ========== 步骤3：处理曲率半径（r=1/k，k为最大曲率） ==========
    min_curv_radius = 1000.0
    if curvature_values:
        max_k = max(curvature_values)  # 段落内最大曲率k
        if max_k != 0:  # 确保不除零
            min_curv_radius = 1 / max_k  # 曲率半径r=1/k（最大k对应最小r）
    else:
        print("警告：当前段落无有效非零曲率值，无法计算曲率半径")

    # ========== 步骤4：找到减速度从≤-0.5降到≥-0.1的转折点，筛选后续abs(减速度)<0.1的帧 ==========
    # 4.1 先过滤出车速有效的帧（车速为None的帧无意义）
    speed_valid_frames = [fd for fd in frame_data_list if fd["vehicle_speed"] is not None]
    if not speed_valid_frames:
        print("警告：当前段落无有效车速帧")
        return None

    # 4.2 遍历有效帧，寻找转折点：先出现减速度≤-0.5，之后首次出现减速度≥-0.1
    C_dec_upper = -0.5
    C_dec_lower = -0.1
    has_decel_below_minus_05 = False  # 标记是否出现过减速度≤-0.5
    turning_point_idx = -1            # 转折点索引（减速度降到≥-0.1的首次帧）
    for idx, fd in enumerate(speed_valid_frames):
        curr_decel = fd["deceleration"]
        # 第一步：检测是否出现过减速度≤-0.5（用户说的“减速度从-0.5及更小”）
        if curr_decel <= C_dec_upper:
            has_decel_below_minus_05 = True
        # 第二步：已出现过减速度≤-0.5，且当前减速度≥-0.1 → 标记转折点
        if has_decel_below_minus_05 and curr_decel >= C_dec_lower:
            turning_point_idx = idx
            break  # 取首次出现的转折点，保证是“逐渐降低到-0.1”的起点

    # 4.3 处理无转折点的情况
    if turning_point_idx == -1:
        print("警告：当前段落未找到减速度从-0.5及更小降到-0.1的转折点")
        return None

    # 4.4 筛选转折点之后的帧中，abs(减速度)<0.1的帧（核心候选帧）
    candidate_frames = [
        fd for idx, fd in enumerate(speed_valid_frames)
        if idx > turning_point_idx and abs(fd["deceleration"]) < 0.1
    ]

    if not candidate_frames:
        print("警告：转折点后无满足abs(减速度)<0.1的有效车速帧")
        return None

    # 4.5 找到候选帧中vehicle_speed最小的帧
    min_speed_frame = min(candidate_frames, key=lambda x: x["vehicle_speed"])

    # ========== 步骤5：组装返回字典 ==========
    result = {
        # 最小车速及对应参数
        "min_vehicle_speed": min_speed_frame["vehicle_speed"],
        "slope_acc_at_min_speed": min_speed_frame["slope_acc"],
        "accleration_on_wheel_at_min_speed": min_speed_frame["accleration_on_wheel"],
        # 最小曲率半径（最大k对应的r）
        "min_curvature_radius": min_curv_radius,
        # 最小车速对应帧的SetSpeed和时间
        "set_speed_at_min_speed": min_speed_frame["set_speed"],
        "min_speed_time": min_speed_frame["chassis_time"],
        # 新增：转折点索引（便于调试）
        "turning_point_frame_idx": turning_point_idx
    }

    return result
