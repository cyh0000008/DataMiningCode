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
        result_values = _calc_segment_max_acc_val(segment)
        if result_values:
            if _check_max_acc_val_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                jerk_values.append(f"[Max Acc Dangerous! setspeed_kph:{result_values['set_speed']} , max_acc_val:{result_values['max_acc_chassis_val']:.6f}, max_acc_val_time:{result_values['max_acc_chassis_val_time']:.6f}, curr_acc:{result_values['curr_acc']:.6f}, curr_slope_acc:{result_values['curr_slope_acc']:.6f}]")
            else:
                jerk_values.append(f"[setspeed_kph:{result_values['set_speed']} , max_acc_val:{result_values['max_acc_chassis_val']:.6f}, max_acc_val_time:{result_values['max_acc_chassis_val_time']:.6f}, curr_acc:{result_values['curr_acc']:.6f}, curr_slope_acc:{result_values['curr_slope_acc']:.6f}]")
        else:
            jerk_values.append(f"当前段落无有效加速度")

    return (check_result, jerk_values, case_statistics)

def _check_max_acc_val_valid(result_values: Optional[Dict]) -> bool:
    C_acc_val_upper_bound = 0.75

    if abs(result_values["max_acc_chassis_val"]) <= C_acc_val_upper_bound:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_acc_val_upper_bound = 0.75

    if abs(result_values["max_acc_chassis_val"]) <= C_acc_val_upper_bound:
        return 0.0
    else:
        C_cal_base = 0.75  #严重程度得分100分的越限基准
        exceed_amount = abs(result_values["max_acc_chassis_val"]) - C_acc_val_upper_bound
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
        debug_file.write("===== 最终规则风险段落搜索调试日志（set_speed严格一致版）=====\n\n")

    def write_debug_log(content: str):
        if DEBUG_MODE and debug_file:
            debug_file.write(content + '\n')

    # ====================== 规则参数配置 ======================
    risk_segments = []
    current_candidate = []                # 存储连续满足条件的候选帧
    current_has_vel_lt_setspeed = False   # 候选段落是否存在vel < SetSpeed的帧
    current_has_vel_ge_setspeed = False   # 候选段落是否存在vel ≥ SetSpeed的帧
    current_base_set_speed = None         # 当前候选段落的基准set_speed（首帧值）
    
    C_min_risk_segment_length = 50         # 有效段落最小帧数
    SLOPE_ACC_THRESH = 0.1                # slope_acc绝对值阈值
    SEARCH_FRAMES_AFTER = 20              # 例外场景下向后搜索的帧数
    SET_SPEED_TOLERANCE = 0.1             # set_speed允许的误差范围（浮点精度）

    # ====================== 遍历每帧：核心逻辑 ======================
    try:
        idx = 0  # while循环支持帧跳转
        total_frames = len(comprehensive_frames)
        
        while idx < total_frames:
            # 获取当前帧和参数
            comp_frame = comprehensive_frames[idx]
            curr_params = _get_frame_risk_params(comp_frame)
            set_speed_stable = _is_set_speed_stable(comp_frame, comprehensive_frames[idx - 1] if idx > 0 else None)
            curr_set_speed = curr_params["set_speed"]  # 当前帧的set_speed
            curr_timestamp = comp_frame.get("unp_timestamp_ms", idx)

            # -------------------- Debug 日志 --------------------
            write_debug_log(f"\n[DEBUG] 处理第 {idx} 帧（时间戳：{curr_timestamp}）：")
            write_debug_log(f"  - 当前set_speed：{curr_set_speed:.2f} | 基准set_speed：{current_base_set_speed}")
            write_debug_log(f"  - 基础条件：f_np_on={curr_params['f_np_on']} | f_potential_cipv_by_pos={curr_params['f_potential_cipv_by_pos']}")
            write_debug_log(f"  - 坡度加速度：{abs(curr_params['slope_acc']):.4f}（阈值={SLOPE_ACC_THRESH}）")

            # 1. 判断单帧基础条件（原有逻辑不变）
            frame_base_condition = (
                set_speed_stable and
                (curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False)
                and curr_params["curv_spd_limit"] >= curr_params["velocity"]
                and curr_params["f_potential_cipv_by_pos"] is False
                and curr_params["has_valid_data"] is True
                and curr_params["f_stationary"] is False
                and abs(curr_params["slope_acc"]) >= SLOPE_ACC_THRESH
                and not (curr_params["f_redlight"] and curr_params["f_stop_dis"] < 40)
            )

            # 2. 核心逻辑：set_speed不一致立即终止当前段落
            if current_candidate:  # 已有候选段落时，优先检查set_speed一致性
                speed_diff = abs(curr_set_speed - current_base_set_speed)
                if speed_diff > SET_SPEED_TOLERANCE:
                    # ========== set_speed不一致：立即终止当前段落 ==========
                    write_debug_log(f"[DEBUG] set_speed不一致（差值={speed_diff:.3f}>阈值{SET_SPEED_TOLERANCE}）→ 立即终止当前段落")
                    
                    # 校验当前段落是否有效
                    if len(current_candidate) > C_min_risk_segment_length:
                        speed_condition_met = current_has_vel_lt_setspeed and current_has_vel_ge_setspeed
                        if speed_condition_met:
                            risk_segments.append(current_candidate.copy())
                            write_debug_log(f"[DEBUG] 终止的段落有效（长度={len(current_candidate)}）→ 加入风险段落列表")
                        else:
                            write_debug_log(f"[DEBUG] 终止的段落无效（速度条件不满足）→ 放弃")
                    else:
                        write_debug_log(f"[DEBUG] 终止的段落无效（长度={len(current_candidate)}≤{C_min_risk_segment_length}）→ 放弃")
                    
                    # 重置所有状态，重新开始
                    current_candidate = []
                    current_has_vel_lt_setspeed = False
                    current_has_vel_ge_setspeed = False
                    current_base_set_speed = None
                    idx += 1
                    continue  # 跳过后续逻辑，处理下一帧

            # 3. 处理候选段落（set_speed一致的前提下）
            if frame_base_condition and (current_candidate or _is_primary_sequence_frame(comp_frame)):
                # 初始化基准set_speed（候选段落首帧）
                if not current_candidate:
                    current_base_set_speed = curr_set_speed
                    write_debug_log(f"[DEBUG] 初始化候选段落，基准set_speed={current_base_set_speed:.2f}")
                
                # 加入当前帧到候选段落（已确保set_speed一致）
                current_candidate.append(comp_frame)
                write_debug_log(f"[DEBUG] 满足所有条件 → 加入候选（当前长度：{len(current_candidate)}）")

                # 更新速度标记（原有逻辑）
                curr_vel = curr_params["velocity"]
                K_speed_offset = -0.5
                if curr_vel < curr_set_speed + K_speed_offset:
                    current_has_vel_lt_setspeed = True
                    write_debug_log(f"[DEBUG] 速度<SetSpeed → 标记vel_lt=True")
                if curr_vel >= curr_set_speed + K_speed_offset:
                    current_has_vel_ge_setspeed = True
                    write_debug_log(f"[DEBUG] 速度≥SetSpeed → 标记vel_ge=True")
                
                idx += 1

            else:
                # ========== 例外逻辑（仅在set_speed一致时触发）==========
                other_base_conditions_met = (
                    (curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False)
                    and curr_params["f_potential_cipv_by_pos"] is False
                    and curr_params["has_valid_data"] is True
                    and curr_params["f_stationary"] is False
                    and not (curr_params["f_redlight"] and curr_params["f_stop_dis"] < 40)
                )
                slope_acc_not_met = abs(curr_params["slope_acc"]) < SLOPE_ACC_THRESH
                candidate_long_enough = len(current_candidate) >= C_min_risk_segment_length

                if other_base_conditions_met and slope_acc_not_met and candidate_long_enough:
                    write_debug_log(f"[DEBUG] 触发例外逻辑：仅slope_acc不满足，向后搜索T2帧...")
                    
                    # 向后搜索T2帧（同时检查set_speed一致性）
                    t2_idx = -1
                    search_end_idx = min(idx + SEARCH_FRAMES_AFTER, total_frames - 1)
                    
                    for search_idx in range(idx + 1, search_end_idx + 1):
                        search_comp_frame = comprehensive_frames[search_idx]
                        search_params = _get_frame_risk_params(search_comp_frame)
                        
                        # 检查T2帧：基础条件满足 + set_speed与基准一致
                        search_frame_base_condition = (
                            _is_set_speed_stable(comprehensive_frames[search_idx], comprehensive_frames[search_idx - 1] if search_idx > 0 else None) and
                            search_params["f_np_on"] is True
                            and search_params["f_potential_cipv_by_pos"] is False
                            and search_params["has_valid_data"] is True
                            and search_params["f_stationary"] is False
                            and abs(search_params["slope_acc"]) >= SLOPE_ACC_THRESH
                            and not (search_params["f_redlight"] and search_params["f_stop_dis"] < 40)
                        )
                        search_speed_consistent = abs(search_params["set_speed"] - current_base_set_speed) <= SET_SPEED_TOLERANCE
                        
                        if search_frame_base_condition and search_speed_consistent:
                            t2_idx = search_idx
                            write_debug_log(f"[DEBUG] 找到T2帧（{search_idx}）：slope_acc达标 + set_speed一致")
                            break
                    
                    if t2_idx != -1:
                        # 将T1到T2的帧加入候选（仅保留set_speed一致的帧）
                        write_debug_log(f"[DEBUG] 将{idx}~{t2_idx}帧加入候选（严格检查set_speed）")
                        for add_idx in range(idx, t2_idx + 1):
                            add_comp_frame = comprehensive_frames[add_idx]
                            add_params = _get_frame_risk_params(add_comp_frame)
                            
                            # 仅加入：基础条件满足 + set_speed与基准一致
                            if (
                                add_params["f_np_on"] is True
                                and add_params["f_potential_cipv_by_pos"] is False
                                and add_params["has_valid_data"] is True
                                and add_params["f_stationary"] is False
                                and not (add_params["f_redlight"] and add_params["f_stop_dis"] < 40)
                                and abs(add_params["set_speed"] - current_base_set_speed) <= SET_SPEED_TOLERANCE
                            ):
                                current_candidate.append(add_comp_frame)
                                # 更新速度标记
                                add_vel = add_params["velocity"]
                                if add_vel < add_params["set_speed"]:
                                    current_has_vel_lt_setspeed = True
                                if add_vel >= add_params["set_speed"]:
                                    current_has_vel_ge_setspeed = True
                                write_debug_log(f"[DEBUG] 第 {add_idx} 帧 - 加入候选（例外逻辑），当前长度={len(current_candidate)}")
                            else:
                                # 中途set_speed不一致：终止例外逻辑，直接结束当前段落
                                write_debug_log(f"[DEBUG] 第 {add_idx} 帧 - set_speed不一致 → 终止例外逻辑和当前段落")
                                # 校验并保存当前段落
                                if len(current_candidate) > C_min_risk_segment_length and current_has_vel_lt_setspeed and current_has_vel_ge_setspeed:
                                    risk_segments.append(current_candidate.copy())
                                # 重置状态
                                current_candidate = []
                                current_has_vel_lt_setspeed = False
                                current_has_vel_ge_setspeed = False
                                current_base_set_speed = None
                                break
                        
                        idx = t2_idx + 1
                    else:
                        # 未找到T2帧：终止当前段落
                        write_debug_log(f"[DEBUG] 未找到T2帧 → 终止当前段落")
                        if len(current_candidate) > C_min_risk_segment_length and current_has_vel_lt_setspeed and current_has_vel_ge_setspeed:
                            risk_segments.append(current_candidate.copy())
                        # 重置状态
                        current_candidate = []
                        current_has_vel_lt_setspeed = False
                        current_has_vel_ge_setspeed = False
                        current_base_set_speed = None
                        idx += 1
                else:
                    # 非例外场景：终止当前段落
                    write_debug_log(f"[DEBUG] 不满足基础条件 → 终止当前段落")
                    if len(current_candidate) > C_min_risk_segment_length and current_has_vel_lt_setspeed and current_has_vel_ge_setspeed:
                        risk_segments.append(current_candidate.copy())
                    # 重置状态
                    current_candidate = []
                    current_has_vel_lt_setspeed = False
                    current_has_vel_ge_setspeed = False
                    current_base_set_speed = None
                    idx += 1

        # 4. 遍历结束后：检查最后一个候选段落
        write_debug_log(f"\n[DEBUG] 遍历结束 - 剩余候选段落长度：{len(current_candidate)}")
        if len(current_candidate) > C_min_risk_segment_length and current_has_vel_lt_setspeed and current_has_vel_ge_setspeed:
            risk_segments.append(current_candidate.copy())
            write_debug_log(f"[DEBUG] 剩余候选段落有效 → 加入风险段落")
        else:
            write_debug_log(f"[DEBUG] 剩余候选段落无效 → 放弃")
        
        # 最终重置
        current_candidate = []
        current_has_vel_lt_setspeed = False
        current_has_vel_ge_setspeed = False
        current_base_set_speed = None

        # -------------------- Debug 日志：最终结果 --------------------
        write_debug_log(f"\n[DEBUG] 搜索完成 - 共找到 {len(risk_segments)} 个有效风险段落（set_speed严格一致）")
        if DEBUG_MODE:
            write_debug_log("\n===== 调试日志结束 =====")
            print(f"[提示] Debug日志已写入：{DEBUG_FILE_PATH}")

    finally:
        if DEBUG_MODE and debug_file:
            debug_file.close()

    print(f"————————————找到 {len(risk_segments)} 个符合规则的坡道无前车控速段落（set_speed严格一致）————————————————")
    
    return risk_segments

def _calc_segment_max_acc_val(segment: List[Dict]) -> Optional[Dict]:
    """
    计算风险段落中稳速段的底盘速度极值及相关差值
    1. 根据segment中的matched_body中的velocity_on_dashboard确定稳速SetSpeed
    2. SetSpeed规则：不低于该段内速度最高值-5，且是速度最高段持续最长的帧数
    3. 统计稳速段内vehicle_speed_average的最大值、最小值及对应时间，还有相关差值
    """
    # 步骤1：提取有效速度数据（velocity_on_dashboard和vehicle_speed_average）
    vel_dashboard_with_time = []
    acc_chassis_with_time = []
    setspd_dashboard_with_time = []
    
    for frame in segment:
        matched_body = frame.get("matched_body")
        matched_chassis = frame.get("matched_chassis")
        matched_msd_control = frame.get("matched_msd_control")
        matched_mff_info = frame.get("matched_mff_info")
        
        # 提取仪表盘速度（用于计算SetSpeed）
        if matched_body and "velocity_on_dashboard" in matched_body:
            vel_dash = matched_body["velocity_on_dashboard"]
            ts = frame.get("unp_timestamp_ms")
            if vel_dash is not None and ts is not None:
                vel_dashboard_with_time.append((vel_dash, ts))

        # 提取仪表盘设定速度
        if matched_mff_info and "cruise_velocity_kph" in matched_mff_info:
            setspd_dash = matched_mff_info["cruise_velocity_kph"]
            ts = frame.get("unp_timestamp_ms")
            if setspd_dash is not None and ts is not None:
                setspd_dashboard_with_time.append((setspd_dash, ts))
        
        # 提取底盘平均速度（用于统计极值）
        if matched_chassis and "accleration_on_wheel" in matched_chassis:
            acc_chassis = matched_chassis["accleration_on_wheel"]
            ts = frame.get("unp_timestamp_ms")
            slope_acc = matched_msd_control["slope_acc"] if (matched_msd_control and "slope_acc" in matched_msd_control) else 0.0
            
            # 关键修复1：严格校验acc_chassis非空，避免None进入列表
            if acc_chassis is not None and ts is not None:
                final_acc = acc_chassis + slope_acc
                # 额外校验final_acc非空（防御性编程）
                if final_acc is not None:
                    acc_chassis_with_time.append((final_acc, slope_acc, acc_chassis, ts))

    # 校验有效数据（补充setspd_dashboard_with_time的非空判断）
    if not vel_dashboard_with_time or not acc_chassis_with_time:
        print("警告：当前段落无有效加速度数据")
        return None
    # 修复拼写错误：setspd_dashborad → setspd_dashboard
    if not setspd_dashboard_with_time:
        print("警告：当前段落无有效设定速度数据")
        return None
    
    # 步骤2：确定SetSpeed
    # 2.1 提取所有仪表盘速度值
    vel_dash_values = [v[0] for v in vel_dashboard_with_time]
    max_vel = max(vel_dash_values)
    min_set_speed = max_vel - 5  # SetSpeed下限
    
    # 关键修复2：先判断列表非空（虽然前面已校验，但双重保险）
    setspd_dashboard = max(setspd_dashboard_with_time, key=lambda x:x[0])[0]

    # 2.2 统计各速度段的持续帧数（按0.5km/h粒度分组）
    speed_groups = {}
    for vel, _ in vel_dashboard_with_time:
        # 按0.5粒度取整，便于分组
        group_key = round(vel / 0.5) * 0.5
        if group_key not in speed_groups:
            speed_groups[group_key] = 0
        speed_groups[group_key] += 1
    
    # 2.3 筛选出≥min_set_speed的速度段，取持续帧数最长的作为SetSpeed
    valid_groups_realsetspd = {k: v for k, v in speed_groups.items() if k >= setspd_dashboard}
    if not valid_groups_realsetspd:
        valid_groups = {k: v for k, v in speed_groups.items() if k >= min_set_speed}
        if not valid_groups:
            # 无满足条件的，取min_set_speed
            set_speed = min_set_speed
        else:
            # 取帧数最长的速度值
            set_speed = max(valid_groups.items(), key=lambda x: x[1])[0]
    else:
        # 取帧数最长的速度值
        set_speed = setspd_dashboard
    
    # 步骤3：确定稳速段
    # 3.1 标记每个帧是否达到SetSpeed（允许±0.5的误差）
    is_set_speed = []
    for vel, ts in vel_dashboard_with_time:
        # 关键修复3：校验vel非空，避免abs(None - set_speed)报错
        if vel is not None:
            if abs(vel - set_speed) <= 0.5:
                is_set_speed.append((True, ts))
            else:
                is_set_speed.append((False, ts))
        else:
            is_set_speed.append((False, ts))  # 空值标记为不满足
    
    # 3.2 找出所有连续≥3帧满足SetSpeed的区间（记录区间的起始和结束索引）
    continuous_intervals = []
    current_start = None
    continuous_count = 0
    
    for idx, (flag, ts) in enumerate(is_set_speed):
        if flag:
            continuous_count += 1
            if current_start is None:
                current_start = idx  # 标记连续区间的起始索引
            # 当连续帧数≥3时，记录完整的连续区间（仅记录首次满足3帧的区间）
            if continuous_count == 3:
                # 区间范围：[current_start, idx]（连续3帧的索引）
                continuous_intervals.append({
                    "start_idx": current_start,
                    "end_idx": idx,
                    "start_ts": is_set_speed[current_start][1],  # 第1帧时间
                    "third_ts": is_set_speed[idx][1],  # 第3帧时间
                    "final_ts": is_set_speed[idx][1]
                })
            if continuous_count > 3:
                continuous_intervals[-1]["final_ts"] = is_set_speed[idx][1]
            # 连续帧数超过3时，不重复记录（只保留首次满足3帧的区间）
        else:
            # 中断连续，重置计数和起始索引
            continuous_count = 0
            current_start = None
    
    # 3.3 确定稳速段的起止时间（处理边界情况）
    start_ts = None
    end_ts = None
    
    if continuous_intervals:
        if len(continuous_intervals) == 1:
            start_ts = continuous_intervals[0]["third_ts"]
            end_ts = continuous_intervals[0]["final_ts"]
        else:
            # 起点：第一个连续3帧区间的第3帧时间
            first_interval = continuous_intervals[0]
            start_ts = first_interval["third_ts"]
            
            # 终点：最后一个连续3帧区间的第1帧时间
            last_interval = continuous_intervals[-1]
            end_ts = last_interval["start_ts"]
    else:
        # 无连续3帧满足SetSpeed的情况，沿用原逻辑兜底
        print("警告：无连续3帧满足SetSpeed，沿用原稳速段逻辑")
        # 找第一次达到
        for flag, ts in is_set_speed:
            if flag:
                start_ts = ts
                break
        # 找最后一次离开
        reversed_is_set_speed = list(reversed(is_set_speed))
        for flag, ts in reversed_is_set_speed:
            if not flag:
                end_ts = ts
                break
        # 若全程都是SetSpeed，取首尾时间
        if start_ts is None:
            start_ts = vel_dashboard_with_time[0][1] if vel_dashboard_with_time else None
        if end_ts is None:
            end_ts = vel_dashboard_with_time[-1][1] if vel_dashboard_with_time else None
    
    # 步骤4：统计稳速段内的底盘速度极值及差值
    # 筛选稳速段内的底盘速度数据
    steady_acc_chassis = []
    for final_acc, acc_slope, acc, ts in acc_chassis_with_time:
        # 关键修复4：校验时间戳非空，避免None参与比较
        if start_ts is not None and end_ts is not None and ts is not None:
            if start_ts <= ts <= end_ts:
                steady_acc_chassis.append((final_acc, acc_slope, acc, ts))
        # 处理时间戳为空的边界情况
        elif start_ts is None and end_ts is None and ts is not None:
            steady_acc_chassis.append((final_acc, acc_slope, acc, ts))
    
    # 初始化返回字段
    max_acc_chassis_val = None
    max_acc_chassis_val_time = None
    curr_slope_acc = None
    curr_acc = None
    
    # 关键修复5：先校验列表非空，再调用max；且过滤final_acc为None的元素
    if steady_acc_chassis:
        # 过滤掉final_acc为None的元素
        valid_steady_acc = [item for item in steady_acc_chassis if item[0] is not None]
        if valid_steady_acc:
            # 找到最大底盘加速度及对应时间（按绝对值）
            max_item = max(valid_steady_acc, key=lambda x: abs(x[0]))
            max_acc_chassis_val = max_item[0]
            curr_slope_acc = max_item[1]
            curr_acc = max_item[2]
            max_acc_chassis_val_time = max_item[3]
        else:
            print("警告：稳速段内无有效加速度数值（均为None）")
            return None
    else:
        print("警告：稳速段内无加速度数据")
        return None

    # 返回结果（替换为新的统计字段）
    return {
        "max_acc_chassis_val": max_acc_chassis_val,
        "curr_slope_acc": curr_slope_acc,
        "curr_acc": curr_acc,
        "max_acc_chassis_val_time": max_acc_chassis_val_time,
        "set_speed": setspd_dashboard  # 保留SetSpeed便于调试
    }
