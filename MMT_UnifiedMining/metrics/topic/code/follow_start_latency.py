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
        result_values = _calc_segment_start_latency(segment)
        if result_values is not None:
            if _check_start_latency_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                ttc_values.append(f"[Latency Dangerous! front_car_start_time_ms:{result_values['front_car_start_time_ms']} , ego_car_start_time_ms:{result_values['ego_car_start_time_ms']}, latency_sec:{result_values['latency_sec']:.2f} s, end_vel: {result_values['end_vel']:.6f}]")
            else:
                ttc_values.append(f"[front_car_start_time_ms:{result_values['front_car_start_time_ms']} , ego_car_start_time_ms:{result_values['ego_car_start_time_ms']}, latency_sec:{result_values['latency_sec']:.2f} s, end_vel: {result_values['end_vel']:.6f}]")
        else:
            ttc_values.append(f"不存在前车车速>0.5且持续3帧至本车车速>0.5的时段，不考虑")

    return (check_result, ttc_values, case_statistics)

def _check_start_latency_valid(result_values: Optional[Dict]) -> bool:
    C_latency_upper_bound = 2.5

    if result_values["latency_sec"] <= C_latency_upper_bound:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_latency_upper_bound = 2.5

    if result_values["latency_sec"] <= C_latency_upper_bound:
        return 0.0
    else:
        C_cal_base = 3  #严重程度得分100分的越限基准
        return abs((result_values["latency_sec"] - C_latency_upper_bound)/C_cal_base)*100.0

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
    
    # 初始化Debug日志文件（增加异常处理）
    if DEBUG_MODE:
        try:
            # 以写入模式打开文件（覆盖已有文件，编码为UTF-8避免中文乱码）
            debug_file = open(DEBUG_FILE_PATH, 'w', encoding='utf-8')
            # 简化日志头（无时间戳，避免依赖datetime）
            debug_file.write("===== 风险段落搜索调试日志 =====\n\n")
        except Exception as e:
            print(f"警告：无法创建Debug日志文件 - {str(e)}")
            DEBUG_MODE = False  # 关闭Debug模式

    # ====================== 辅助函数：写入Debug日志（增加异常防护）=====================
    def write_debug_log(content: str):
        """将Debug信息写入TXT文件，替代控制台打印"""
        if DEBUG_MODE and debug_file:
            try:
                debug_file.write(content + '\n')  # 每行末尾加换行符
            except Exception as e:
                print(f"警告：写入Debug日志失败 - {str(e)}")

    # ====================== 原有参数配置 ======================
    risk_segments = []
    current_segment = []  # 存储当前候选段落的unp_para
    prev_params = None    # 存储上一帧的风险参数（用于判断起点）
    is_in_segment = False # 标记是否已进入候选段落（找到有效起点后）
    curr_params = None    # 初始化，避免遍历结束后访问None
    
    # 配置参数（可调整）
    C_min_risk_segment_length = 5  # 有效段落最小帧数
    VEHICLE_STATIONARY_THRESH = 0.2  # 静止速度阈值（≤此值视为静止）
    FRONT_CAR_STATIONARY_THRESH = 0.2
    FRONT_CAR_START_THRESH = 0.5
    FRONT_CAR_START_WINDOW_SECONDS = 1.0
    END_VEL_LOW_THRESH = 2           # 有效终点最低速度
    END_VEL_HIGH_THRESH = 6          # 终点终止速度
    params_history: List[Dict] = []

    # ====================== 遍历处理每帧（全面None防护+异常捕获）=====================
    try:
        for idx, unp_para in enumerate(comprehensive_frames):
            # 1. 获取当前帧的风险参数（增加异常捕获）
            try:
                curr_params = _get_frame_risk_params(unp_para)
                set_speed_stable = _is_set_speed_stable(unp_para, comprehensive_frames[idx - 1] if idx > 0 else None)
            except Exception as e:
                print(f"警告：获取第{idx}帧风险参数失败 - {str(e)}")
                curr_params = {}  # 初始化空字典，避免后续报错
                continue
            
            # -------------------- 核心修复：安全获取所有参数，避免None --------------------
            # 数值型参数：双重防护（get默认值 + 类型转换）
            velocity = float(curr_params.get('velocity', 0.0) or 0.0)
            accel = float(curr_params.get('accel', 0.0) or 0.0)
            closest_obj_vel = float(curr_params.get('closest_obj_vel', 0.0) or 0.0)
            closest_obj_x = float(curr_params.get('closest_obj_x', 0.0) or 0.0)
            f_stop_dis = float(curr_params.get('f_stop_dis', 0.0) or 0.0)
            
            # 布尔型参数：设置默认值，避免None
            has_valid_data = curr_params.get('has_valid_data', False)
            f_redlight = curr_params.get('f_redlight', False)
            f_stationary = curr_params.get('f_stationary', False)
            f_np_on = curr_params.get('f_np_on', False)
            is_non_straight_driving = curr_params.get('is_non_straight_driving', False)
            f_potential_cipv_by_pos = curr_params.get('f_potential_cipv_by_pos', False)
            params_history.append(curr_params.copy() if isinstance(curr_params, dict) else curr_params)
            current_history_index = len(params_history) - 1
            
            # -------------------- Debug 日志：当前帧基础信息（使用安全值） --------------------
            write_debug_log(f"  - SetSpeed保持不变：{set_speed_stable}")
            write_debug_log(f"\n[DEBUG] 处理第 {idx} 帧：")
            write_debug_log(f"  - 有效数据：{has_valid_data}")
            write_debug_log(f"  - 红绿灯：红灯：{f_redlight}，停止线距离：{f_stop_dis:.6f} m")
            write_debug_log(f"  - 本车状态：静止={f_stationary}，速度={velocity:.6f} m/s，加速度={accel:.6f} m/s2")
            write_debug_log(f"  - 前车状态：速度={closest_obj_vel:.6f} m/s, 纵向位置={closest_obj_x:.6f} m")
            write_debug_log(f"  - 基础条件：f_np_on={f_np_on}，f_potential_cipv_by_pos={f_potential_cipv_by_pos}")
            
            # 2. 基础校验：当前帧无有效数据 → 重置候选段落
            if not has_valid_data:
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
            redlight_blocked = f_redlight and f_stop_dis <= 30 and f_stop_dis >= 0.1
            is_valid_start = is_valid_start and not redlight_blocked
            start_debug_info["红灯停止线排除"] = redlight_blocked
            start_debug_info["最终起点条件"] = is_valid_start
            write_debug_log(f"[DEBUG] 第 {idx} 帧 - 起点判断：{start_debug_info}")

            # 4. 处理有效起点：初始化候选段落
            if is_valid_start and _is_primary_sequence_frame(unp_para):
                # 检查当前帧是否满足段落内基础条件
                is_frame_valid = ((f_np_on is True and not is_non_straight_driving and set_speed_stable) and
                                  f_potential_cipv_by_pos is True and
                                  has_valid_data is True)
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
                prev_f_stationary = prev_params.get('f_stationary', False) if prev_params else False
                ego_last_is_move = not prev_f_stationary

                if ego_last_is_move:
                    is_frame_valid = ((f_np_on is True and not is_non_straight_driving and set_speed_stable) and
                                    f_potential_cipv_by_pos is True and
                                    has_valid_data is True and
                                    f_stationary is False and
                                    accel >= 0 and
                                    not (f_redlight and f_stop_dis <= 15 and f_stop_dis >= 0.1))
                else:
                    is_frame_valid = ((f_np_on is True and not is_non_straight_driving and set_speed_stable) and
                                    f_potential_cipv_by_pos is True and
                                    has_valid_data is True and
                                    not (f_redlight and f_stop_dis <= 15 and f_stop_dis >= 0.1))
                
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
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 本车速度{velocity:.2f} → 继续收集（当前长度：{len(current_segment)}）")
                else:
                    # 当前帧不满足基础条件 → 终止，取上一帧为终点
                    write_debug_log(f"[DEBUG] 第 {idx} 帧 - 不满足基础条件 → 终止候选段落（最终长度：{len(current_segment)}）")
                    prev_velocity = float(prev_params.get('velocity', 0.0) or 0.0) if prev_params else 0.0
                    if len(current_segment) > C_min_risk_segment_length and prev_velocity >= END_VEL_LOW_THRESH:
                        risk_segments.append(current_segment.copy())
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 段落长度达标 → 加入风险段落列表")
                    else:
                        write_debug_log(f"[DEBUG] 第 {idx} 帧 - 段落长度不足 → 放弃")
                    current_segment = []
                    is_in_segment = False
            
            # 6. 存储当前帧参数为上一帧（供下一帧判断）
            prev_params = curr_params.copy() if isinstance(curr_params, dict) else curr_params

        # 7. 遍历结束后：检查是否有未处理的候选段落（增加None校验）
        final_velocity = float(curr_params.get('velocity', 0.0) or 0.0) if curr_params else 0.0
        if (is_in_segment and len(current_segment) > C_min_risk_segment_length and 
            curr_params and final_velocity >= END_VEL_LOW_THRESH):
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

    # 全局异常捕获，避免函数崩溃
    except Exception as e:
        print(f"错误：处理风险段落时发生异常 - {str(e)}")
        write_debug_log(f"\n[ERROR] 处理过程中发生异常：{str(e)}")
    finally:
        # 确保文件句柄正常关闭（无论是否有异常）
        if DEBUG_MODE and debug_file:
            try:
                debug_file.close()
            except Exception as e:
                print(f"警告：关闭Debug日志文件失败 - {str(e)}")

    print(f"————————————找到 {len(risk_segments)} 个符合新规则的跟车段落————————————————")
    
    return risk_segments

def _calc_segment_start_latency(
    segment: List[Dict]
) -> Optional[Dict]:
    """
    计算单个跟车起步segment的跟车响应时延：
    时延 = 本车车速>0.5的时刻 - 前车车速>0.5且已持续3帧的时刻
    :param segment: 风险段落的unp数据列表
    :param fusion_paragraphs: 解析后的fusion数据（含closest_obj_vel）
    :param chassis_paragraphs: 解析后的chassis数据（含vehicle_speed_average）
    :return: 包含时延和关键时刻的字典，无有效数据返回None
    """
    # 步骤1：收集segment中每帧的时间、前车车速、本车车速
    frame_data_list = []
    for unp_para in segment:
        unp_frame_data = unp_para.get("unp_frame_data", {})
        
        # 获取fusion基准时间，匹配前车车速
        fusion_base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        matched_fusion = unp_para.get("matched_fusion", {})
        front_car_vel = matched_fusion.get("closest_obj_vel", 0.0) if matched_fusion else 0.0
        
        # 获取chassis基准时间，匹配本车车速
        chassis_base_time_ms = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        matched_chassis = unp_para.get("matched_chassis", {})
        ego_car_vel = matched_chassis.get("vehicle_speed_average", 0.0) if matched_chassis else 0.0
        
        # 取fusion时间作为基准时间（也可取chassis，保持一致即可）
        base_time_ms = fusion_base_time_ms if fusion_base_time_ms else chassis_base_time_ms
        if base_time_ms is None:
            continue
        
        frame_data_list.append({
            "time_ms": base_time_ms,          # 帧时刻（ms）
            "front_car_vel": front_car_vel,   # 前车车速
            "ego_car_vel": ego_car_vel        # 本车车速
        })
    
    # 校验：无有效帧数据直接返回None
    if len(frame_data_list) < 3:
        print("警告：当前段落有效帧数据不足3帧，无法计算时延")
        return None
    
    # 步骤2：按时间排序（确保帧顺序正确）
    frame_data_list.sort(key=lambda x: x["time_ms"])
    
    # 步骤3：找前车车速>0.5且持续3帧的起始时刻
    front_car_valid_start_time = None
    consecutive_frames = 0  # 连续满足前车车速>0.5的帧数
    for idx, frame in enumerate(frame_data_list):
        if frame["front_car_vel"] > 0.5:
            consecutive_frames += 1
            # 连续3帧满足条件，记录该段的第一个帧时刻
            if consecutive_frames == 3:
                # 取连续3帧的第一个帧时刻作为前车起步时刻
                front_car_valid_start_time = frame_data_list[idx-2]["time_ms"]
                break
        else:
            consecutive_frames = 0  # 中断，重置计数
    
    # 校验：未找到前车满足条件的时刻
    if front_car_valid_start_time is None:
        print("警告：当前段落未找到前车车速>0.5且持续3帧的时刻")
        return None
    
    # 步骤4：找本车车速>0.5的最早时刻
    ego_car_start_time = None
    for frame in frame_data_list:
        if frame["ego_car_vel"] > 0.5:
            ego_car_start_time = frame["time_ms"]
            break
    
    # 校验：未找到本车满足条件的时刻
    if ego_car_start_time is None:
        print("警告：当前段落未找到本车车速>0.5的时刻")
        return None
    
    # 步骤5：计算时延（转换为秒，每帧间隔0.1s，时间差/1000转秒）
    latency_ms = ego_car_start_time - front_car_valid_start_time
    latency_sec = latency_ms / 1000.0  # 转换为秒
    
    if latency_ms < 0:
        print("警告：当前段落未找到前车车速>0.5且持续3帧至本车车速>0.5的时段")
        return None

    # 返回结果（保持和原函数一致的字典格式，兼容后续逻辑）
    return {
        "latency_sec": latency_sec,          # 跟车响应时延（秒）
        "latency_ms": latency_ms,            # 跟车响应时延（毫秒）
        "front_car_start_time_ms": front_car_valid_start_time,  # 前车满足条件的时刻
        "ego_car_start_time_ms": ego_car_start_time,            # 本车满足条件的时刻
        "max_jerk": None,  # 兼容原逻辑的字段，设为None
        "max_jerk_time": None,  # 兼容原逻辑的字段，设为None
        "end_vel": frame_data_list[-1]["ego_car_vel"]  # 段落最后一帧本车车速
    }
