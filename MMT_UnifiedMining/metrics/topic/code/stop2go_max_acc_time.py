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
        result_values = _calc_segment_max_accel_time(segment)
        if result_values:
            if _check_max_accel_time_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                jerk_values.append(
                    f"[Stop2Go Max Acc Build Time Dangerous! "
                    f"max_acc:{result_values['max_acc']:.3f} , "
                    f"acc_build_time:{result_values['acc_build_time']:.1f}, "
                    f"start_acc_time_ms:{result_values['first_acc_02_time_ms']:.3f}, "
                    f"max_acc_time_ms:{result_values['max_acc_time_ms']:.3f}, "
                    f"max_acc_velocity_kph:{result_values['max_acc_velocity']:.1f}, "
                    f"cruise_vel_kph:{result_values['cruise_vel_kph']:.1f}]"
                )
            else:
                jerk_values.append(
                    f"max_acc:{result_values['max_acc']:.3f} , "
                    f"acc_build_time:{result_values['acc_build_time']:.1f}, "
                    f"start_acc_time_ms:{result_values['first_acc_02_time_ms']:.3f}, "
                    f"max_acc_time_ms:{result_values['max_acc_time_ms']:.3f}, "
                    f"max_acc_velocity_kph:{result_values['max_acc_velocity']:.1f}, "
                    f"cruise_vel_kph:{result_values['cruise_vel_kph']:.1f}]"
                )
        else:
            jerk_values.append(f"当前段落无有效最大加速度")

    return (check_result, jerk_values, case_statistics)
    
def _check_max_accel_time_valid(result_values: Optional[Dict]) -> bool:
    C_min_time = 1.0
    C_max_time = 4.0

    acc_build_time = result_values["acc_build_time"]

    if acc_build_time <= C_max_time and acc_build_time >= C_min_time:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_min_time = 1.0
    C_max_time = 4.0

    acc_build_time = result_values["acc_build_time"]

    if acc_build_time <= C_max_time and acc_build_time >= C_min_time:
        return 0.0
    else:
        C_cal_base = 2  #严重程度得分100分的越限基准
        if acc_build_time < C_min_time:
            exceed_amount = C_min_time - acc_build_time
        else:
            exceed_amount = acc_build_time - C_max_time
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

def _calc_segment_max_accel_time(segment: List[Dict]) -> Optional[Dict]:
    """
    新功能：计算segment中的合成加速度acc=accleration_on_wheel+slope_acc，提取关键指标
    :param segment: 风险段落的帧数据列表
    :return: 包含以下字段的字典；无有效数据返回None
             字典结构：{
                 "max_acc": 最大合成加速度（acc）,
                 "max_acc_velocity": 最大加速度对应的表显速度（km/h）,
                 "acc_build_time": 最大加速度建立时间（t2-t1，s，保留3位小数）,
                 "cruise_vel_kph": 设定车速（km/h）,
             }
    """
    # ========== 步骤1：参数校验（强化None/空值防护） ==========
    if segment is None or not isinstance(segment, list) or len(segment) == 0:
        print("警告：输入的segment为空或非列表类型，无数据可处理")
        return None

    # ========== 步骤2：初始化变量（全量None防护，默认值更安全） ==========
    valid_acc_frames: List[Dict] = []  # 存储所有有效acc的帧数据
    max_acc: float = -float('inf')     # 最大合成加速度
    max_acc_frame: Optional[Dict] = None  # 最大加速度对应的帧
    first_acc_02_frame: Optional[Dict] = None  # 首次acc>0.2对应的帧

    # ========== 步骤3：遍历帧，提取并计算有效数据 ==========
    for frame_idx, frame in enumerate(segment):
        # 核心防护1：帧本身为None的情况
        if frame is None or not isinstance(frame, dict):
            print(f"警告：第{frame_idx}帧 - 帧数据为空或非字典类型，跳过")
            continue

        # 提取核心数据（多层None防护，避免KeyError + 空字典兜底）
        matched_chassis = frame.get("matched_chassis", {}) or {}  # 空值转空字典
        matched_msd_control = frame.get("matched_msd_control", {}) or {}
        matched_body = frame.get("matched_body", {}) or {}
        matched_mff_info = frame.get("matched_mff_info", {}) or {}

        # 1. 提取加速度相关数据（None兜底 + 类型预判）
        accleration_on_wheel = matched_chassis.get("accleration_on_wheel")  # 注意拼写：accleration
        slope_acc = matched_msd_control.get("slope_acc")
        # 2. 提取速度/设定车速/时间戳（None兜底）
        velocity_on_dashboard = matched_body.get("velocity_on_dashboard")
        cruise_vel_kph = matched_mff_info.get("cruise_velocity_kph")
        # 优先使用UNP时间戳，无则用chassis的时间戳（双层None防护）
        frame_time_ms = frame.get("unp_timestamp_ms") or matched_chassis.get("timestamp_ms")

        # ========== 数据有效性校验（全量None+类型防护） ==========
        # 过滤关键参数为空的帧（核心参数必须非None）
        if any(v is None for v in [accleration_on_wheel, slope_acc, frame_time_ms]):
            print(f"警告：第{frame_idx}帧 - 加速度/时间戳为空，跳过")
            continue

        # 转换为浮点数（完整异常捕获，避免类型错误）
        try:
            accleration_on_wheel = float(accleration_on_wheel)
            slope_acc = float(slope_acc)
            frame_time_ms = float(frame_time_ms)
            # 非核心参数（表显速度/设定车速）转换失败则设为None
            velocity_on_dashboard = float(velocity_on_dashboard) if velocity_on_dashboard is not None else None
            cruise_vel_kph = float(cruise_vel_kph) if cruise_vel_kph is not None else None
        except (ValueError, TypeError) as e:
            print(f"警告：第{frame_idx}帧 - 数据类型错误（{str(e)}），跳过")
            continue

        # ========== 计算合成加速度acc（数值运算防护） ==========
        acc = accleration_on_wheel + slope_acc

        # 存储有效帧数据（所有字段明确类型，避免隐式None）
        valid_frame = {
            "time_ms": frame_time_ms,          # 必为float
            "acc": acc,                        # 必为float
            "velocity": velocity_on_dashboard, # 可为float/None
            "cruise_vel": cruise_vel_kph       # 可为float/None
        }
        valid_acc_frames.append(valid_frame)

        # ========== 记录最大加速度帧（None防护） ==========
        if isinstance(acc, (int, float)) and acc > max_acc:
            max_acc = acc
            max_acc_frame = valid_frame.copy()  # 深拷贝避免后续修改影响

        # ========== 记录首次acc>0.2的帧（仅第一次匹配，强化None防护） ==========
        if (first_acc_02_frame is None and 
            isinstance(acc, (int, float)) and 
            acc > 0.2):
            first_acc_02_frame = valid_frame.copy()
            print(f"提示：第{frame_idx}帧 - 首次检测到acc>0.2，值为{acc:.3f}，时间戳{frame_time_ms:.3f}ms")

    # ========== 步骤4：边界情况处理（强化None/空值防护） ==========
    if not valid_acc_frames:
        print("警告：当前segment无有效加速度数据")
        return None
    if max_acc_frame is None:
        print("警告：未找到有效最大加速度帧")
        return None

    # ========== 步骤5：计算加速度建立时间（全量None+数值防护） ==========
    # 核心防护2：t2/t1的None防护 + 数值类型校验
    t2 = max_acc_frame.get("time_ms", 0.0)  # 兜底为0.0
    t1 = first_acc_02_frame.get("time_ms", t2) if (first_acc_02_frame and isinstance(first_acc_02_frame, dict)) else t2
    
    # 确保t1/t2为数值类型
    t2 = float(t2) if isinstance(t2, (int, float)) else 0.0
    t1 = float(t1) if isinstance(t1, (int, float)) else t2
    
    # 计算建立时间，避免负数（时间差不可能为负）
    acc_build_time = round(max((t2 - t1)/1000.0, 0.0), 3)

    # ========== 步骤6：组装返回结果（最终None防护，所有字段确保非None） ==========
    # 提取最大加速度帧的属性，全量None兜底为0.0
    max_acc_velocity = max_acc_frame.get("velocity", 0.0)
    max_acc_velocity = float(max_acc_velocity) if isinstance(max_acc_velocity, (int, float)) else 0.0
    
    cruise_vel = max_acc_frame.get("cruise_vel", 0.0)
    cruise_vel = float(cruise_vel) if isinstance(cruise_vel, (int, float)) else 0.0
    
    result = {
        "max_acc": round(float(max_acc), 3) if isinstance(max_acc, (int, float)) else 0.0,  # 最大合成加速度
        "max_acc_velocity": round(max_acc_velocity, 3),  # 对应表显速度（保留3位小数）
        "acc_build_time": acc_build_time,  # 加速度建立时间（s）
        "first_acc_02_time_ms": round(t1, 3),
        "max_acc_time_ms": round(t2, 3),
        "cruise_vel_kph": round(cruise_vel, 3),  # 设定车速（保留3位小数）
    }

    return result
