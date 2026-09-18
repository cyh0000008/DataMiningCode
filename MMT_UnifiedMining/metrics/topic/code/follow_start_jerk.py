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
        result_values = _calc_segment_max_jerk(segment)
        if result_values:
            if _check_max_jerk_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                jerk_values.append(f"[Acc Dangerous! time:{result_values['max_jerk_time']} , jerk:{result_values['max_jerk']:.6f}, end_vel: {result_values['end_vel']:.6f}]")
            else:
                jerk_values.append(f"[time:{result_values['max_jerk_time']} , jerk:{result_values['max_jerk']:.6f}, end_vel: {result_values['end_vel']:.6f}]")
        else:
            jerk_values.append(f"当前段落算不出jerk")

    return (check_result, jerk_values, case_statistics)

def _check_max_jerk_valid(result_values: Optional[Dict]) -> bool:
    C_jerk_upper_bound = 2.5
    C_jerk_lower_bound = 1.0

    if result_values["max_jerk"] >= C_jerk_lower_bound and result_values["max_jerk"] <= C_jerk_upper_bound:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_jerk_upper_bound = 2.5
    C_jerk_lower_bound = 1.0

    if result_values["max_jerk"] >= C_jerk_lower_bound and result_values["max_jerk"] <= C_jerk_upper_bound:
        return 0.0
    else:
        C_cal_base = 1  #严重程度得分100分的越限基准
        if result_values["max_jerk"] < C_jerk_lower_bound:
            exceed_amount = C_jerk_lower_bound - result_values["max_jerk"]
        else:
            exceed_amount = result_values["max_jerk"] - C_jerk_upper_bound
        return abs(exceed_amount/C_cal_base)*100.0

# ------------------------------ 辅助函数：获取单帧风险判定参数 ------------------------------
def _get_frame_risk_params(comp_frame: Dict) -> Dict:
    """提取单帧的风险判定参数，返回结构化字典"""
    unp_frame_data = comp_frame.get("unp_frame_data", {})
    fusion_base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
    chassis_base_time_ms = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
    frame_time_ms = fusion_base_time_ms if fusion_base_time_ms is not None else chassis_base_time_ms
    if frame_time_ms is None:
        frame_time_ms = comp_frame.get("unp_timestamp_ms")
    
    # 获取Fusion相关参数
    matched_fusion = comp_frame.get("matched_fusion")
    fusion_ttc = matched_fusion["min_ttc"] if (matched_fusion and "min_ttc" in matched_fusion) else None
    f_potential_cipv_in_trafficlight = matched_fusion.get("f_potential_cipv_in_trafficlight", False) if matched_fusion else False
    f_potential_cipv_by_pos = matched_fusion.get("f_potential_cipv_by_pos", False) if matched_fusion else False
    closest_obj_x = matched_fusion.get("closest_obj_x", 0) if matched_fusion else 0
    closest_obj_vel = matched_fusion.get("closest_obj_vel", 0) if matched_fusion else 0
    closest_obj_acc = matched_fusion.get("closest_obj_acc", 0) if matched_fusion else 0
    
    # 获取Chassis相关参数
    matched_chassis = comp_frame.get("matched_chassis")
    # 修改17：处理matched_chassis为None的情况，避免KeyError
    f_stationary = matched_chassis.get("stationary", False) if matched_chassis else False
    velocity = matched_chassis.get("vehicle_speed_average", 0) if matched_chassis else 0
    acceleration = matched_chassis.get("accleration_on_wheel", 0) if matched_chassis else 0
    
    # 获取UNP相关参数
    f_np_on = unp_frame_data.get("NP_State", {}).get("f_Np_on", False)

    is_non_straight_driving = _is_non_straight_driving_scene(unp_frame_data)
    traffic_light = unp_frame_data.get("TrafficLight", {})
    f_redlight = traffic_light.get("f_RedLightOn", False)
    f_stop_dis = traffic_light.get("stop_distance", 999)
    
    return {
        "time_ms": frame_time_ms,
        "f_redlight": f_redlight,
        "f_stop_dis": f_stop_dis,
        "f_np_on": f_np_on,
        "is_non_straight_driving": is_non_straight_driving,
        "f_stationary": f_stationary,
        "velocity": velocity,
        "accel":acceleration,
        "fusion_ttc": fusion_ttc,
        "f_potential_cipv_in_trafficlight": f_potential_cipv_in_trafficlight,
        "f_potential_cipv_by_pos": f_potential_cipv_by_pos,
        "closest_obj_x": closest_obj_x,
        "closest_obj_vel": closest_obj_vel,
        "closest_obj_acc": closest_obj_acc,
        "has_valid_data": bool(matched_fusion and unp_frame_data and matched_chassis)
    }

def _safe_float(value: object, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _is_ego_stationary(params: Dict, stationary_threshold: float) -> bool:
    velocity = _safe_float(params.get("velocity"), 0.0) or 0.0
    return bool(params.get("f_stationary", False)) and velocity <= stationary_threshold

def _is_acc_active(params: Dict) -> bool:
    return bool(params.get("f_np_on", False))

def _has_front_vehicle(params: Dict) -> bool:
    return bool(params.get("has_valid_data", False)) and bool(params.get("f_potential_cipv_by_pos", False))

def _find_front_start_window(
    params_history: List[Dict],
    current_history_index: int,
    ego_stationary_threshold: float,
    front_stationary_threshold: float,
    front_start_threshold: float,
    max_window_seconds: float,
) -> Tuple[bool, Dict]:
    current_params = params_history[current_history_index]
    current_time_ms = _safe_float(current_params.get("time_ms"), None)
    current_front_vel = _safe_float(current_params.get("closest_obj_vel"), 0.0) or 0.0
    debug_info = {
        "当前前车速度>0.5": current_front_vel > front_start_threshold,
        "窗口内存在前车<=0.2": False,
        "窗口时长<=1s": False,
        "窗口内自车静止": False,
        "窗口内ACC active": False,
        "窗口内有前车": False,
        "最终起点条件": False,
    }

    if current_time_ms is None or current_front_vel <= front_start_threshold:
        return False, debug_info

    window_start_ms = current_time_ms - max_window_seconds * 1000.0
    for low_index in range(current_history_index, -1, -1):
        low_params = params_history[low_index]
        low_time_ms = _safe_float(low_params.get("time_ms"), None)
        if low_time_ms is None:
            continue
        if low_time_ms < window_start_ms:
            break
        if low_time_ms > current_time_ms:
            continue

        low_front_vel = _safe_float(low_params.get("closest_obj_vel"), 0.0) or 0.0
        if low_front_vel > front_stationary_threshold:
            continue

        interval = params_history[low_index:current_history_index + 1]
        ego_stationary_all = all(_is_ego_stationary(item, ego_stationary_threshold) for item in interval)
        acc_active_all = all(_is_acc_active(item) for item in interval)
        has_front_all = all(_has_front_vehicle(item) for item in interval)
        duration_ms = current_time_ms - low_time_ms
        in_window = 0.0 <= duration_ms <= max_window_seconds * 1000.0
        debug_info = {
            "起始帧索引": low_index,
            "结束帧索引": current_history_index,
            "窗口时长ms": round(duration_ms, 3),
            "起始前车速度": low_front_vel,
            "结束前车速度": current_front_vel,
            "当前前车速度>0.5": True,
            "窗口内存在前车<=0.2": True,
            "窗口时长<=1s": in_window,
            "窗口内自车静止": ego_stationary_all,
            "窗口内ACC active": acc_active_all,
            "窗口内有前车": has_front_all,
            "最终起点条件": in_window and ego_stationary_all and acc_active_all and has_front_all,
        }
        if debug_info["最终起点条件"]:
            return True, debug_info

    return False, debug_info

def _find_risk_segments(
    comprehensive_frames: List[Dict]
) -> List[List[Dict]]:
    """
    新规则查找连续风险段落（返回值不变）：
    1. 起点：前车速度在1s内从<=0.2m/s跳到>0.5m/s，且窗口内自车stationary、ACC active、有前车
    2. 段落内每点：f_np_on=true + f_potential_cipv_by_pos=true + has_valid_data=true
    3. 终点：有效起点下，满足段落内条件且本车速度≥2 → 继续搜，直到（速度≥6 或 不满足段落内条件），取上一个点为终点
    4. 有效段落：起点-终点帧数 > C_min_risk_segment_length
    
    Debug 开关：设置为 True 开启调试日志（写入TXT），False 关闭
    """
    # ====================== Debug 配置（仅依赖 os 库）=====================
    DEBUG_MODE = True  # 总开关：True=写入调试日志到TXT，False=关闭Debug日志
    DEBUG_MODE = False  # 总开关：True=写入调试日志到TXT，False=关闭Debug日志
    DEBUG_FILE_NAME = "risk_segment_debug.txt"  # 固定日志文件名（无时间戳，避免依赖datetime）
    DEBUG_FILE_PATH = os.path.join(os.getcwd(), DEBUG_FILE_NAME)  # 当前目录下的日志文件
    debug_file = None  # 日志文件句柄
    
    # 初始化Debug日志文件
    if DEBUG_MODE:
        # 以写入模式打开文件（覆盖已有文件，编码为UTF-8避免中文乱码）
        debug_file = open(DEBUG_FILE_PATH, 'w', encoding='utf-8')
        # 简化日志头（无时间戳，避免依赖datetime）
        debug_file.write("===== 风险段落搜索调试日志 =====\n\n")

    # ====================== 辅助函数：写入Debug日志 ======================
    def write_debug_log(content: str):
        """将Debug信息写入TXT文件，替代控制台打印"""
        if DEBUG_MODE and debug_file:
            debug_file.write(content + '\n')  # 每行末尾加换行符

    # ====================== 原有参数配置 ======================
    risk_segments = []
    current_segment = []  # 存储当前候选段落的unp_para
    prev_params = None    # 存储上一帧的风险参数（用于判断起点）
    is_in_segment = False # 标记是否已进入候选段落（找到有效起点后）
    
    # 配置参数（可调整）
    C_min_risk_segment_length = 5  # 有效段落最小帧数
    VEHICLE_STATIONARY_THRESH = 0.2  # 静止速度阈值（≤此值视为静止）
    FRONT_CAR_STATIONARY_THRESH = 0.2
    FRONT_CAR_START_THRESH = 0.5
    FRONT_CAR_START_WINDOW_SECONDS = 1.0
    END_VEL_LOW_THRESH = 2           # 有效终点最低速度
    END_VEL_HIGH_THRESH = 6          # 终点终止速度
    params_history: List[Dict] = []

    # ====================== 遍历处理每帧 ======================
    try:
        # 初始化curr_params，避免遍历结束后访问None
        curr_params = None
        for idx, unp_para in enumerate(comprehensive_frames):
            # 1. 获取当前帧的风险参数（复用已实现的函数）
            curr_params = _get_frame_risk_params(unp_para)
            set_speed_stable = _is_set_speed_stable(unp_para, comprehensive_frames[idx - 1] if idx > 0 else None)
            
            # -------------------- 关键修复：安全获取所有数值，避免None --------------------
            # 为所有可能为None的数值设置默认值
            velocity = curr_params.get('velocity', 0.0) or 0.0
            accel = curr_params.get('accel', 0.0) or 0.0
            closest_obj_vel = curr_params.get('closest_obj_vel', 0.0) or 0.0
            closest_obj_x = curr_params.get('closest_obj_x', 0.0) or 0.0
            f_stop_dis = curr_params.get('f_stop_dis', 0.0) or 0.0
            params_history.append(curr_params.copy() if isinstance(curr_params, dict) else curr_params)
            current_history_index = len(params_history) - 1
            
            # -------------------- Debug 日志：当前帧基础信息（使用安全值） --------------------
            write_debug_log(f"  - SetSpeed保持不变：{set_speed_stable}")
            write_debug_log(f"\n[DEBUG] 处理第 {idx} 帧：")
            write_debug_log(f"  - 有效数据：{curr_params['has_valid_data']}")
            write_debug_log(f"  - 红绿灯：红灯：{curr_params['f_redlight']}，停止线距离：{f_stop_dis:.6f} m")
            write_debug_log(f"  - 本车状态：静止={curr_params['f_stationary']}，速度={velocity:.6f} m/s，加速度={accel:.6f} m/s2")
            write_debug_log(f"  - 前车状态：速度={closest_obj_vel:.6f} m/s, 纵向位置={closest_obj_x:.6f} m")
            write_debug_log(f"  - 基础条件：f_np_on={curr_params['f_np_on']}，f_potential_cipv_by_pos={curr_params['f_potential_cipv_by_pos']}")
            
            # 2. 基础校验：当前帧无有效数据 → 重置候选段落
            if not curr_params["has_valid_data"]:
                write_debug_log(f"[DEBUG] 第 {idx} 帧：无有效数据 → 重置候选段落")
                current_segment = []
                prev_params = curr_params
                is_in_segment = False
                continue
            
            # 3. 判断是否为有效起点：前车速度在1s内从<=0.2m/s跳到>0.5m/s，且过程中自车静止、ACC active、有前车
            is_valid_start, start_debug_info = _find_front_start_window(
                params_history=params_history,
                current_history_index=current_history_index,
                ego_stationary_threshold=VEHICLE_STATIONARY_THRESH,
                front_stationary_threshold=FRONT_CAR_STATIONARY_THRESH,
                front_start_threshold=FRONT_CAR_START_THRESH,
                max_window_seconds=FRONT_CAR_START_WINDOW_SECONDS,
            )
            redlight_blocked = curr_params["f_redlight"] and f_stop_dis <= 30 and f_stop_dis >= 0.1
            is_valid_start = is_valid_start and not redlight_blocked
            start_debug_info["红灯停止线排除"] = redlight_blocked
            start_debug_info["最终起点条件"] = is_valid_start
            write_debug_log(f"[DEBUG] 第 {idx} 帧 - 起点判断：{start_debug_info}")

            # 4. 处理有效起点：初始化候选段落
            if is_valid_start and _is_primary_sequence_frame(unp_para):
                # 检查当前帧是否满足段落内基础条件
                is_frame_valid = ((curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False and set_speed_stable) and
                                  curr_params["f_potential_cipv_by_pos"] is True and
                                  curr_params["has_valid_data"] is True)
                if is_frame_valid:
                    current_segment = [unp_para]  # 起点帧加入候选
                    is_in_segment = True  # 标记进入候选段落
                    write_debug_log(f"[DEBUG] 第 {idx} 帧 - 找到有效起点 → 初始化候选段落（当前长度：{len(current_segment)}）")
                else:
                    current_segment = []
                    is_in_segment = False
                    write_debug_log(f"[DEBUG] 第 {idx} 帧 - 起点帧不满足基础条件 → 放弃初始化")
            
            # 5. 已进入候选段落：继续收集/判断终点
            elif is_in_segment:
                # 检查当前帧是否满足段落内基础条件
                ego_last_is_move = not prev_params["f_stationary"]

                if ego_last_is_move:
                    is_frame_valid = ((curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False and set_speed_stable) and
                                    curr_params["f_potential_cipv_by_pos"] is True and
                                    curr_params["has_valid_data"] is True and
                                    curr_params["f_stationary"] is False and
                                    (curr_params.get('accel', 0.0) or 0.0) >= 0 and
                                    not (curr_params["f_redlight"] and f_stop_dis <= 15 and f_stop_dis >= 0.1))
                else:
                    is_frame_valid = ((curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False and set_speed_stable) and
                                    curr_params["f_potential_cipv_by_pos"] is True and
                                    curr_params["has_valid_data"] is True and
                                    not (curr_params["f_redlight"] and f_stop_dis <= 15 and f_stop_dis >= 0.1))
                
                write_debug_log(f"[DEBUG] 第 {idx} 帧 - 候选段落内：基础条件满足={is_frame_valid}，当前速度={velocity:.2f}")
                
                if is_frame_valid:
                    # 条件1：当前帧速度≥6 → 终止，当前帧为最后一帧
                    if velocity >= END_VEL_HIGH_THRESH:
                        current_segment.append(unp_para)
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 速度≥{END_VEL_HIGH_THRESH} → 终止候选段落（最终长度：{len(current_segment)}）")
                        # 检查段落长度：有效则加入结果，重置候选
                        if len(current_segment) > C_min_risk_segment_length:
                            risk_segments.append(current_segment.copy())
                            write_debug_log(f"[DEBUG] 第 {idx} 帧 - 段落长度达标（>{C_min_risk_segment_length}）→ 加入风险段落列表")
                        else:
                            write_debug_log(f"[DEBUG] 第 {idx} 帧 - 段落长度不足（≤{C_min_risk_segment_length}）→ 放弃")
                        current_segment = []
                        is_in_segment = False
                    else:
                        current_segment.append(unp_para)
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 本车速度{velocity} → 继续收集（当前长度：{len(current_segment)}）")
                else:
                    # 当前帧不满足基础条件 → 终止，取上一帧为终点
                    write_debug_log(f"[DEBUG] 第 {idx} 帧 - 不满足基础条件 → 终止候选段落（最终长度：{len(current_segment)}）")
                    prev_velocity = prev_params.get('velocity', 0.0) or 0.0
                    if len(current_segment) > C_min_risk_segment_length and prev_velocity >= END_VEL_LOW_THRESH:
                        risk_segments.append(current_segment.copy())
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 段落长度达标 → 加入风险段落列表")
                    else:
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 段落长度不足 → 放弃")
                    current_segment = []
                    is_in_segment = False
            
            # 6. 存储当前帧参数为上一帧（供下一帧判断）
            prev_params = curr_params

        # 7. 遍历结束后：检查是否有未处理的候选段落（增加None校验）
        if (is_in_segment and len(current_segment) > C_min_risk_segment_length and 
            curr_params and (curr_params.get('velocity', 0.0) or 0.0) >= END_VEL_LOW_THRESH):
            risk_segments.append(current_segment.copy())
            write_debug_log(f"\n[DEBUG] 遍历结束 - 发现未处理的候选段落（长度：{len(current_segment)}）→ 加入风险段落列表")
        elif is_in_segment:
            write_debug_log(f"\n[DEBUG] 遍历结束 - 未处理候选段落长度不足 → 放弃")

        # -------------------- Debug 日志：最终结果 --------------------
        write_debug_log(f"\n[DEBUG] 搜索完成 - 共找到 {len(risk_segments)} 个有效风险段落")
        if DEBUG_MODE:
            # 简化日志尾（无时间戳）
            write_debug_log("\n===== 调试日志结束 =====")
            print(f"[提示] Debug日志已写入：{DEBUG_FILE_PATH}")  # 控制台仅提示日志文件路径

    finally:
        # 确保文件句柄正常关闭（无论是否有异常）
        if DEBUG_MODE and debug_file:
            debug_file.close()

    print(f"————————————找到 {len(risk_segments)} 个符合新规则的跟车段落————————————————")
    
    return risk_segments

def _calc_segment_max_jerk(
    segment: List[Dict]  # 适配Dict类型
) -> Optional[Dict]:  # 核心修改：返回类型改为Optional[Dict]
    """计算风险段落中jerk的最小值：匹配unp的chassis基准时刻（适配字典格式）"""
    acc_with_time = []
    end_vel = 40
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
                if i == len(segment)-1 and vel is not None:
                    end_vel = vel

    if not acc_with_time:
        print("警告：当前风险段落无有效加速度数据")
        return None  # 原返回(None, None, None) → 改为返回None

    # 修复17：处理calculate_min_smoothed_jerk_with_time返回None的情况
    max_jerk_item = calculate_max_smoothed_jerk_with_time(acc_with_time)
    if max_jerk_item is None:
        # 原返回(None, None, end_vel) → 封装为字典返回
        return {
            "max_jerk": None,
            "max_jerk_time": None,
            "end_vel": end_vel
        }
    
    max_jerk = max_jerk_item[0]
    max_jerk_time = max_jerk_item[1]

    # 核心修改：将元组返回值封装为字典
    return {
        "max_jerk": max_jerk,
        "max_jerk_time": max_jerk_time,
        "end_vel": end_vel
    }

def calculate_max_smoothed_jerk_with_time(acc_with_time: List[Tuple[Optional[float], Optional[float]]]) -> Optional[Tuple[Optional[float], Optional[float]]]:  # 补充返回值注解
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

    # ========== 步骤4：找到最大jerk值 + 对应的时刻 ==========
    # 按jerk值找最大值（原逻辑是按绝对值，这里保持业务逻辑）
    max_jerk_item = max(smoothed_jerk_time, key=lambda x: x[0])
    max_jerk = max_jerk_item[0]
    max_jerk_time_ms = max_jerk_item[1]

    # 返回(最大jerk值, 对应时刻ms)的元组
    return (max_jerk, max_jerk_time_ms)

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
