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
                jerk_values.append(
                    f"[Max Stop2Go Jerk Dangerous! "
                    f"max_jerk:{result_values['max_jerk_value']:.3f} , "
                    f"max_jerk_time:{result_values['max_jerk_chassis_time_ms']:.3f}, "
                    f"curr_speed:{result_values['max_jerk_vehicle_speed']:.3f}, "
                    f"max_speed:{result_values['segment_max_vehicle_speed']:.3f}]"
                )
            else:
                jerk_values.append(
                    f"max_jerk:{result_values['max_jerk_value']:.3f} , "
                    f"max_jerk_time:{result_values['max_jerk_chassis_time_ms']:.3f}, "
                    f"curr_speed:{result_values['max_jerk_vehicle_speed']:.3f}, "
                    f"max_speed:{result_values['segment_max_vehicle_speed']:.3f}]"
                )
        else:
            jerk_values.append(f"当前段落无有效起步jerk")

    return (check_result, jerk_values, case_statistics)
    
def _check_max_jerk_valid(result_values: Optional[Dict]) -> bool:
    C_max_jerk = 3.5

    max_jerk = result_values["max_jerk_value"]

    if max_jerk <= C_max_jerk:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_max_jerk = 3.5

    max_jerk = result_values["max_jerk_value"]

    if max_jerk <= C_max_jerk:
        return 0.0
    else:
        C_cal_base = 1  #严重程度得分100分的越限基准
        return abs((max_jerk - C_max_jerk)/C_cal_base)*100.0

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
    查找满足"静止起步"条件的连续段落
    条件：
    1. f_np_on 一直为true
    2. not (curr_params["fusion_ttc"] and curr_params["fusion_ttc"] < C_min_ttc)
    3. not (curr_params["f_redlight"] is True and curr_params["f_stop_dis"] < C_min_stop_dis)
    4. f_curvature_control_on = false
    5. f_stationary = false
    6. 起点需要满足上一帧的f_stationary = true
    7. curr_params["accel"]>=0
    8. curr_params["velocity"]<=set_speed
    9. 终点需要满足段落长度大于定值
    """
    # ====================== 配置参数（可根据业务调整）=====================
    DEBUG_MODE = True
    DEBUG_MODE = False
    DEBUG_FILE_NAME = "risk_segment_debug.txt"
    DEBUG_FILE_PATH = os.path.join(os.getcwd(), DEBUG_FILE_NAME)
    debug_file = None

    if DEBUG_MODE:
        debug_file = open(DEBUG_FILE_PATH, 'w', encoding='utf-8')
        debug_file.write("===== 静止起步段落搜索调试日志 =====\n\n")

    def write_debug_log(content: str):
        if DEBUG_MODE and debug_file:
            debug_file.write(content + '\n')

    # 核心配置常量
    risk_segments = []
    current_candidate = []  # 存储当前连续满足条件的帧
    C_min_segment_length = 50  # 段落最小帧数（条件9），可根据需求调整
    C_min_ttc = 3.5  # TTC阈值（条件2）
    C_min_stop_dis = 50.0  # 红灯停止距离阈值（条件3）
    C_min_end_vel = 8.0

    # ====================== 核心遍历逻辑 ======================
    try:
        total_frames = len(comprehensive_frames)
        write_debug_log(f"[DEBUG] 开始遍历，总帧数：{total_frames}")

        # 遍历每帧（从第1帧开始，因为需要访问上一帧数据）
        for idx in range(1, total_frames):
            # 获取当前帧和上一帧的综合数据
            curr_comp_frame = comprehensive_frames[idx]
            prev_comp_frame = comprehensive_frames[idx-1]

            # 提取当前帧和上一帧的判定参数
            curr_params = _get_frame_risk_params(curr_comp_frame)
            prev_params = _get_frame_risk_params(prev_comp_frame)
            set_speed_stable = _is_set_speed_stable(curr_comp_frame, prev_comp_frame)

            # -------------------- 调试日志：当前帧核心参数 --------------------
            write_debug_log(f"\n[DEBUG] 处理第 {idx} 帧（UNP时间戳：{curr_comp_frame.get('unp_timestamp_ms', '未知')}）：")
            write_debug_log(f"  - 基础条件：f_np_on={curr_params['f_np_on']} | f_curvature_control_on={curr_params['f_curvature_control_on']}")
            write_debug_log(f"  - TTC条件：fusion_ttc={curr_params['fusion_ttc']}（需≥{C_min_ttc}或无）")
            write_debug_log(f"  - 红灯条件：f_redlight={curr_params['f_redlight']} | f_stop_dis={curr_params['f_stop_dis']}（需不同时满足红灯+距离<{C_min_stop_dis}）")
            write_debug_log(f"  - 静止条件：当前f_stationary={curr_params['f_stationary']} | 上一帧f_stationary={prev_params['f_stationary']}")
            write_debug_log(f"  - 加速条件：accel={curr_params['accel']}（需≥0） | velocity={curr_params['velocity']} ≤ set_speed={curr_params['set_speed']}")
            write_debug_log(f"  - SetSpeed保持不变：{set_speed_stable}")
            write_debug_log(f"  - 数据有效性：{curr_params['has_valid_data']}")

            # -------------------- 单帧满足的核心条件（条件1-5、7-8） --------------------
            frame_base_condition = (
                set_speed_stable and
                curr_params["has_valid_data"] is True  # 确保数据有效，避免空值判断
                and (curr_params["f_np_on"] is True and curr_params.get("is_non_straight_driving", False) is False)  # 条件1
                and not (curr_params["fusion_ttc"] and curr_params["fusion_ttc"] < C_min_ttc)  # 条件2
                and not (curr_params["f_redlight"] is True and curr_params["f_stop_dis"] < C_min_stop_dis)  # 条件3
                and curr_params["f_curvature_control_on"] is False  # 条件4
                and curr_params["f_stationary"] is False  # 条件5
                and curr_params["accel"] >= 0  # 条件7
                and curr_params["velocity"] <= curr_params["set_speed"]  # 条件8
            )

            # -------------------- 起点判定（条件6）+ 连续段落收集 --------------------
            if frame_base_condition:
                # 情况1：当前候选段落为空，且满足"起步起点"（条件6）→ 初始化候选段落
                if len(current_candidate) == 0 and prev_params["f_stationary"] is True and _is_primary_sequence_frame(curr_comp_frame):
                    current_candidate.append(curr_comp_frame)
                    write_debug_log(f"[DEBUG] 第 {idx} 帧 - 满足起步起点条件 → 初始化候选段落")
                # 情况2：当前候选段落已初始化 → 追加当前帧（保持连续）
                elif len(current_candidate) > 0:
                    current_candidate.append(curr_comp_frame)
                    write_debug_log(f"[DEBUG] 第 {idx} 帧 - 满足连续条件 → 追加候选（当前长度：{len(current_candidate)}）")
            else:
                # 不满足单帧条件 → 检查当前候选段落是否有效（条件9：长度达标）
                write_debug_log(f"[DEBUG] 第 {idx} 帧 - 不满足条件 → 终止候选段落（当前长度：{len(current_candidate)}）")
                if (len(current_candidate) >= C_min_segment_length) and (curr_params["velocity"] >= C_min_end_vel):
                    risk_segments.append(current_candidate.copy())
                    write_debug_log(f"[DEBUG] 候选段落长度≥{C_min_segment_length} → 加入静止起步段落列表")
                else:
                    write_debug_log(f"[DEBUG] 候选段落长度<{C_min_segment_length} → 放弃")
                # 重置候选段落
                current_candidate = []

        # -------------------- 遍历结束后：检查最后一个候选段落 --------------------
        write_debug_log(f"\n[DEBUG] 遍历结束 - 剩余候选段落长度：{len(current_candidate)}")
        if (len(current_candidate) >= C_min_segment_length) and (curr_params["velocity"] >= C_min_end_vel):
            risk_segments.append(current_candidate.copy())
            write_debug_log(f"[DEBUG] 剩余候选段落长度达标 → 加入静止起步段落列表")

        # 调试日志收尾
        if DEBUG_MODE:
            write_debug_log(f"\n[DEBUG] 搜索完成 - 共找到 {len(risk_segments)} 个静止起步段落")
            debug_file.close()
            print(f"[提示] 调试日志已写入：{DEBUG_FILE_PATH}")

    except Exception as e:
        print(f"[错误] 查找静止起步段落时异常：{str(e)}")
        if DEBUG_MODE and debug_file:
            debug_file.write(f"\n[ERROR] 异常信息：{str(e)}")
            debug_file.close()

    print(f"————————————找到 {len(risk_segments)} 个符合条件的静止起步段落—————————————")
    return risk_segments

def _calc_segment_max_jerk(segment: List[Dict]) -> Optional[Dict]:
    """
    新功能：计算segment中最大jerk（jerk由acc计算，acc = accleration_on_wheel + slope_acc）
    直接内联calculate_min_smoothed_jerk_with_time逻辑并修改为计算最大jerk
    返回：包含最大jerk值、对应chassis_time、对应vehicle_speed、segment最大vehicle_speed的字典
    :param segment: 风险段落的帧数据列表
    :return: 包含关键结果的字典，无有效数据时返回None
    """
    # ========== 步骤1：参数校验（强化None/空值/类型防护） ==========
    if segment is None or not isinstance(segment, list) or len(segment) == 0:
        print("警告：输入的segment为空、非列表类型或None，无数据可处理")
        return None

    # ========== 步骤2：遍历帧数据，提取并计算核心参数 ==========
    valid_data: List[Dict] = []  # 存储有效帧的(acc, chassis_time, vehicle_speed)
    all_vehicle_speeds: List[float] = []  # 存储所有有效车速，用于找segment最大车速

    for frame_idx, frame in enumerate(segment):
        # 防护1：帧本身为None或非字典类型
        if frame is None or not isinstance(frame, dict):
            print(f"警告：第{frame_idx}帧 - 帧数据为空或非字典类型，跳过")
            continue

        # 提取各模块数据（多层None防护，空值转空字典）
        matched_chassis = frame.get("matched_chassis", {}) or {}
        matched_msd_control = frame.get("matched_msd_control", {}) or {}

        # 提取核心参数（None兜底，避免KeyError）
        accleration_on_wheel = matched_chassis.get("accleration_on_wheel")
        slope_acc = matched_msd_control.get("slope_acc")
        vehicle_speed = matched_chassis.get("vehicle_speed_average")
        chassis_time = matched_chassis.get("timestamp_ms")

        # ========== 步骤3：数据有效性校验（全量None+类型防护） ==========
        # 1. 过滤核心参数为None的帧
        if any(v is None for v in [accleration_on_wheel, slope_acc, vehicle_speed, chassis_time]):
            print(f"警告：第{frame_idx}帧存在无效数据（轮端加速度/坡度加速度/车速/时间戳为空），跳过")
            continue

        # 2. 强制转换为浮点数，捕获类型错误
        try:
            accleration_on_wheel = float(accleration_on_wheel)
            slope_acc = float(slope_acc)
            vehicle_speed = float(vehicle_speed)
            chassis_time = float(chassis_time)
        except (ValueError, TypeError) as e:
            print(f"警告：第{frame_idx}帧 - 数据类型错误（{str(e)}），无法转换为浮点数，跳过")
            continue

        # ========== 步骤4：计算总加速度acc（数值运算防护） ==========
        acc = accleration_on_wheel + slope_acc

        # ========== 步骤5：存储有效数据（确保所有字段为数值类型） ==========
        valid_frame = {
            "acc": acc,                  # 必为float
            "chassis_time": chassis_time, # 必为float
            "vehicle_speed": vehicle_speed # 必为float
        }
        valid_data.append(valid_frame)
        all_vehicle_speeds.append(vehicle_speed)

    # ========== 步骤6：处理无有效数据的情况（强化空值防护） ==========
    if not valid_data or not isinstance(valid_data, list):
        print("警告：当前segment无有效加速度/车速数据")
        return None
    if not all_vehicle_speeds or not isinstance(all_vehicle_speeds, list):
        print("警告：当前segment无有效车速数据")
        return None

    # ========== 步骤7：直接内联jerk计算逻辑（原calculate_min_smoothed_jerk_with_time） ==========
    # 构造acc_with_time列表（仅取acc和chassis_time）
    acc_with_time = [(item["acc"], item["chassis_time"]) for item in valid_data]
    max_jerk_item = calculate_max_smoothed_jerk_with_time(acc_with_time)

    # 防护：max_jerk_item为None的情况
    if max_jerk_item is None or not isinstance(max_jerk_item, tuple) or len(max_jerk_item) < 2:
        print("警告：无法计算最大jerk值，返回空结果")
        return None

    # 提取jerk值和时间，全量None防护
    max_jerk_value = max_jerk_item[0]
    max_jerk_chassis_time = max_jerk_item[1]
    
    # 强制转换为数值，兜底为0.0
    max_jerk_value = float(max_jerk_value) if isinstance(max_jerk_value, (int, float)) else 0.0
    max_jerk_chassis_time = float(max_jerk_chassis_time) if isinstance(max_jerk_chassis_time, (int, float)) else 0.0

    # ========== 步骤8：匹配最大jerk对应的车速（全量None防护） ==========
    max_jerk_vehicle_speed = None
    try:
        # 精确匹配（处理浮点精度）
        for item in valid_data:
            if isinstance(item, dict) and abs(float(item["chassis_time"]) - max_jerk_chassis_time) < 1e-6:
                max_jerk_vehicle_speed = item["vehicle_speed"]
                break

        # 未找到精确匹配时，取最接近时间的车速
        if max_jerk_vehicle_speed is None:
            closest_item = min(valid_data, 
                              key=lambda x: abs(float(x["chassis_time"]) - max_jerk_chassis_time) 
                              if isinstance(x, dict) else float('inf'))
            max_jerk_vehicle_speed = closest_item["vehicle_speed"] if isinstance(closest_item, dict) else 0.0
            print(f"提示：未找到最大jerk时刻({max_jerk_chassis_time:.3f}ms)的精确车速，使用最接近时刻({closest_item['chassis_time']:.3f}ms)的车速：{max_jerk_vehicle_speed:.3f}")
    except (ValueError, TypeError):
        max_jerk_vehicle_speed = 0.0
        print("警告：无法匹配最大jerk对应的车速，使用默认值0.0")

    # 强制转换车速为数值，兜底为0.0
    max_jerk_vehicle_speed = float(max_jerk_vehicle_speed) if isinstance(max_jerk_vehicle_speed, (int, float)) else 0.0

    # ========== 步骤9：查找segment中的最大vehicle_speed（异常防护） ==========
    try:
        valid_speeds = [float(s) for s in all_vehicle_speeds if isinstance(s, (int, float))]
        max_vehicle_speed = max(valid_speeds) if valid_speeds else 0.0
    except ValueError:
        max_vehicle_speed = 0.0
        print("警告：无法计算segment最大车速，使用默认值0.0")

    # ========== 步骤10：组装返回结果（最终None防护） ==========
    result = {
        "max_jerk_value": round(max_jerk_value, 3),          # 最大jerk值（保留3位小数）
        "max_jerk_chassis_time_ms": round(max_jerk_chassis_time, 3),  # 对应底盘时间（ms）
        "max_jerk_vehicle_speed": round(max_jerk_vehicle_speed, 3),   # 对应车速（保留3位小数）
        "segment_max_vehicle_speed": round(max_vehicle_speed, 3)    # segment最大车速（保留3位小数）
    }

    return result

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
