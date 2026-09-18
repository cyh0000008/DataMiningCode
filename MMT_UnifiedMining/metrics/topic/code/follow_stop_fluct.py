import os
import sys
import re
import traceback
import json
import time
import math
from typing import List, Dict, Optional, Tuple, Any, Union  # 新增Any/Union，修复3.8兼容
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
        result_values = _calc_segment_fluct_times(segment)
        if result_values is not None:
            if _check_fluct_times_valid(result_values) is not True:
                case_statistics["Bad Case"] += 1
                case_statistics["Severity Score"] = max(case_statistics["Severity Score"], _cal_severity_score(result_values))
                check_result = True
                ttc_values.append(
                    f"[FluctCount Dangerous! total_count:{result_values['inflection_count']}, "
                    f"first_inflection_time_ms:{result_values['first_inflection_time_ms']:.3f}, "
                    f"last_inflection_time_ms:{result_values['last_inflection_time_ms']:.3f}]"
                )
            else:
                ttc_values.append(
                    f"[total_count:{result_values['inflection_count']}, "
                    f"first_inflection_time_ms:{result_values['first_inflection_time_ms']:.3f}, "
                    f"last_inflection_time_ms:{result_values['last_inflection_time_ms']:.3f}]"
                )
        else:
            ttc_values.append(f"不存在有效的跟车减速抖动，不考虑")

    return (check_result, ttc_values, case_statistics)

def _check_fluct_times_valid(result_values: Optional[Dict]) -> bool:
    C_lower_bound = 3

    if result_values["inflection_count"] <= C_lower_bound:
        return True
    else:
        return False
    
def _cal_severity_score(result_values: Optional[Dict]) -> float:
    C_lower_bound = 3

    if result_values["inflection_count"] <= C_lower_bound:
        return 0.0
    else:
        C_cal_base = 3  #严重程度得分100分的越限基准
        max_diff = abs(result_values["inflection_count"] - C_lower_bound)
        return abs(max_diff/C_cal_base)*100.0

def _calc_segment_fluct_times(
    segment: List[Dict]  # 适配：List[Dict]
) -> Optional[Dict]:
    """
    新功能：统计自车加速度（accleration_on_wheel）的波峰波谷次数
    逻辑：
    1. 提取chassis中的加速度数据并做有效性校验
    2. 滑动平均平滑处理（消除抖动）
    3. 检测平滑后数据的波峰、波谷数量
    """
    print("="*50)
    print("开始执行 _calc_segment_fluct_times 函数")
    print("="*50)
    
    # 可配置参数（根据抖动程度调整）
    WINDOW_SIZE = 7          # 滑动平均窗口大小（越大越平滑）
    CHECK_RANGE = 10          # 极值检测范围（左右各3帧）
    print(f"当前配置参数 - 滑动平均窗口大小：{WINDOW_SIZE}，极值检测范围：{CHECK_RANGE}")

    # ========== 步骤1：收集segment中每个unp对应的chassis加速度数据 ==========
    print("\n【步骤1/5】开始提取并校验chassis加速度数据...")
    chassis_data_list = []
    acc_raw_list = []  # 纯加速度数值列表（用于后续波峰波谷检测）
    
    total_frames = len(segment)
    print(f"待处理的总帧数：{total_frames}")
    
    for idx, comp_frame in enumerate(segment):
        if idx % 50 == 0:  # 每50帧打印一次进度，避免输出过多
            print(f"  正在处理第 {idx}/{total_frames} 帧数据")
            
        unp_frame_data = comp_frame["unp_frame_data"]
        # 获取chassis基准时间
        chassis_base_time = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        
        if not chassis_base_time:
            continue
            
        # 匹配对应的chassis段落
        matched_chassis = comp_frame["matched_chassis"]
        if not matched_chassis:
            continue
        
        # 提取核心数据（注意字段名拼写：accleration_on_wheel 可能是 acceleration_on_wheel）
        acceleration = matched_chassis.get("accleration_on_wheel")
        timestamp = chassis_base_time
        
        # 数据有效性校验（仅关注加速度和时间戳）
        if None in [acceleration, timestamp]:
            print(f"  警告：第{idx}帧chassis数据不完整（加速度/时间戳为空），跳过")
            continue
        
        # 类型转换
        try:
            acceleration = float(acceleration)
            timestamp = float(timestamp)
        except (ValueError, TypeError):
            print(f"  警告：第{idx}帧chassis数据类型错误（非数值类型），跳过")
            continue
        
        # 存储有效数据
        chassis_data_list.append({
            "timestamp_ms": timestamp,
            "acceleration": acceleration,
            "unp_idx": idx
        })
        acc_raw_list.append(acceleration)  # 收集纯加速度数值
    
    print(f"【步骤1/5】数据提取完成 - 原始帧数：{total_frames}，有效加速度数据数：{len(acc_raw_list)}")
    
    # 无有效chassis数据
    if not chassis_data_list or len(acc_raw_list) < 3:
        print("【步骤1/5】警告：无有效chassis加速度数据或数据量不足（至少需要3个点），函数提前返回")
        return None

    # ========== 步骤2：加速度数据平滑（消除抖动） ==========
    print("\n【步骤2/5】开始对加速度数据进行滑动平均平滑处理...")
    acc_smooth_list = moving_average_smooth(acc_raw_list, window_size=WINDOW_SIZE)
    print(f"【步骤2/5】平滑处理完成 - 平滑前数据量：{len(acc_raw_list)}，平滑后数据量：{len(acc_smooth_list)}")
    # print(f"  平滑前前5个数据：{acc_raw_list[:5]}")
    # print(f"  平滑后前5个数据：{acc_smooth_list[:5]}")  # 可选：打印部分数据用于调试

    # ========== 步骤3：检测波峰波谷 ==========
    print("\n【步骤3/5】开始检测加速度数据的波峰波谷...")
    detector = AccelerationInflectionDetector(
        time_interval=0.1,
        smooth_window=0,
        sensitivity=1.0
    )
    print(f"  初始化检测器参数 - 时间间隔：0.1s，平滑窗口：0，灵敏度：1.0")
    detection_result = detector.detect(acc_smooth_list)
    print(f"【步骤3/5】波峰波谷检测完成 - 检测到的总拐点数量：{len(detection_result.get('all_inflections', []))}")

    # 步骤4：组装最终结果（关联原始时间戳）
    print("\n【步骤4/5】开始组装最终拐点结果（关联原始时间戳）...")
    final_inflections = []
    for inf in detection_result["all_inflections"]:
        idx = inf["index"]
        timestamp_ms = chassis_data_list[idx]["timestamp_ms"] if 0 <= idx < len(chassis_data_list) else None
        final_inflections.append({
            "time_s": inf["time_s"],
            "timestamp_ms": timestamp_ms,
            "acceleration": inf["acceleration"],
            "type": inf["type"],
            "strength": inf["strength"],
            "is_core": inf["is_core"],
        })
    
    final_core_inflections = [inf for inf in final_inflections if inf["is_core"]]
    print(f"【步骤4/5】结果组装完成 - 总拐点数：{len(final_inflections)}，核心拐点数：{len(final_core_inflections)}")

    # ========== 步骤5：组装返回结果 ==========
    print("\n【步骤5/5】开始组装最终返回结果...")
    first_inflection_time_ms = float(final_inflections[0]["timestamp_ms"]) if final_inflections and final_inflections[0].get("timestamp_ms") is not None else 0.0
    last_inflection_time_ms = float(final_inflections[-1]["timestamp_ms"]) if final_inflections and final_inflections[-1].get("timestamp_ms") is not None else first_inflection_time_ms
    result = {
        "detection_config": detection_result["config"],
        "statistical_features": detection_result["statistical_features"],
        "all_inflections": final_inflections,
        "core_inflections": final_core_inflections,
        "inflection_count": len(final_inflections),
        "core_inflection_count": len(final_core_inflections),
        "first_inflection_time_ms": first_inflection_time_ms,
        "last_inflection_time_ms": last_inflection_time_ms,
    }

    print(f"【步骤5/5】函数执行完成 - 返回结果包含：总拐点数 {len(final_inflections)}，核心拐点数 {len(final_core_inflections)}")
    print("="*50)
    
    return result

class AccelerationInflectionDetector:
    """
    通用加速度曲线拐点识别器
    适配任意加速度曲线，无需硬编码阈值/时间点
    """
    def __init__(self, time_interval, smooth_window, sensitivity):
        """
        :param time_interval: 数据采样时间间隔（秒），默认0.1s
        :param smooth_window: 滑动平均平滑窗口大小（越大越平滑），默认5
        :param sensitivity: 识别敏感度（1.0为标准，<1更严格，>1更宽松）
        """
        self.time_interval = time_interval
        self.smooth_window = smooth_window
        self.sensitivity = sensitivity

    def _moving_average_smooth(self, data: List[float]) -> List[float]:
        """通用滑动平均平滑（去噪声）"""
        if len(data) <= self.smooth_window or self.smooth_window < 1:
            return data.copy()
        smoothed = []
        for i in range(len(data)):
            start = max(0, i - self.smooth_window // 2)
            end = min(len(data), i + self.smooth_window // 2 + 1)
            smoothed.append(sum(data[start:end]) / (end - start))
        return smoothed

    def _calculate_slopes(self, data: List[float]) -> List[float]:
        """计算相邻点的斜率（加速度变化率）"""
        slopes = []
        for i in range(1, len(data)):
            slope = (data[i] - data[i-1]) / self.time_interval
            slopes.append(slope)
        return slopes

    def _get_statistical_features(self, data: List[float]) -> Dict:
        """提取数据的统计特征（自适应判定依据）"""
        if len(data) < 3:
            return {"mean": 0, "std": 0, "median": 0, "iqr": 0}
        
        # 基础统计量
        mean = sum(data) / len(data)
        std = math.sqrt(sum([(x - mean)**2 for x in data]) / len(data))
        sorted_data = sorted(data)
        median = sorted_data[len(sorted_data)//2]
        
        # 四分位数间距（IQR，衡量数据离散程度）
        q1 = sorted_data[len(sorted_data)//4]
        q3 = sorted_data[3*len(sorted_data)//4]
        iqr = q3 - q1
        
        return {
            "mean": mean,
            "std": std,
            "median": median,
            "iqr": iqr,
            "min": min(data),
            "max": max(data)
        }

    def _detect_candidate_inflections(self, smooth_data: List[float]) -> List[Dict]:
        """检测候选拐点（基于斜率突变+统计特征）"""
        if len(smooth_data) < 5:
            return []
        
        # 步骤1：计算斜率和斜率变化率
        slopes = self._calculate_slopes(smooth_data)
        slope_changes = [abs(slopes[i] - slopes[i-1]) for i in range(1, len(slopes))]
        
        # 步骤2：提取斜率变化率的统计特征（自适应判定阈值）
        slope_features = self._get_statistical_features(slope_changes)
        # 拐点判定阈值：斜率变化率 > （中位数 + 敏感度×标准差）
        inflection_threshold = slope_features["median"] + self.sensitivity * slope_features["std"]
        
        # 步骤3：筛选候选拐点（斜率突变+局部极值）
        candidates = []
        for i in range(1, len(smooth_data)-1):  # 跳过首尾
            # 对应斜率变化率的索引
            sc_idx = i-2 if i-2 >= 0 else 0
            curr_slope_change = slope_changes[sc_idx] if sc_idx < len(slope_changes) else 0
            
            # 条件1：斜率变化率超过自适应阈值
            if curr_slope_change < inflection_threshold:
                continue
            
            # 条件2：是局部极值（左右各2帧内的最大/最小值）
            left_vals = smooth_data[max(0, i-2):i]
            right_vals = smooth_data[i+1:min(len(smooth_data), i+3)]
            is_peak = smooth_data[i] >= max(left_vals + right_vals) if left_vals and right_vals else False
            is_valley = smooth_data[i] <= min(left_vals + right_vals) if left_vals and right_vals else False
            
            if not (is_peak or is_valley):
                continue
            
            # 计算拐点类型和强度
            inflection_type = "peak" if is_peak else "valley"
            # 拐点强度：斜率变化率 / 阈值（越大越重要）
            strength = round(curr_slope_change / inflection_threshold, 2)
            
            candidates.append({
                "index": i,
                "time_s": round(i * self.time_interval, 2),
                "acceleration": round(smooth_data[i], 3),
                "type": inflection_type,
                "slope_change": round(curr_slope_change, 3),
                "strength": strength  # 拐点强度（≥1，越大越显著）
            })
        
        return candidates

    def _deduplicate_inflections(self, candidates: List[Dict], min_time_gap: float = 0.5) -> List[Dict]:
        """去重：合并时间间隔<min_time_gap的相邻拐点，保留强度最大的"""
        if not candidates:
            return []
        
        # 按时间排序
        sorted_candidates = sorted(candidates, key=lambda x: x["time_s"])
        deduplicated = [sorted_candidates[0]]
        
        for curr in sorted_candidates[1:]:
            last = deduplicated[-1]
            # 时间间隔不足，合并
            if curr["time_s"] - last["time_s"] < min_time_gap:
                if curr["strength"] > last["strength"]:
                    deduplicated[-1] = curr
            else:
                deduplicated.append(curr)
        
        return deduplicated

    def _rank_inflections(self, inflections: List[Dict]) -> List[Dict]:
        """按强度排序，标记核心拐点（前20%为核心）"""
        if not inflections:
            return []
        
        # 按强度降序排序
        ranked = sorted(inflections, key=lambda x: x["strength"], reverse=True)
        # 核心拐点阈值：前20%
        core_threshold_idx = max(1, len(ranked) // 5)
        
        for i, inf in enumerate(ranked):
            inf["rank"] = i + 1
            inf["is_core"] = i < core_threshold_idx  # 标记核心拐点
        
        return ranked

    def detect(self, acc_data: List[float]) -> Dict:
        """
        通用拐点识别主函数
        :param acc_data: 原始加速度数据列表
        :return: 包含拐点、统计特征、配置的完整结果
        """
        # 数据校验
        if len(acc_data) < 5:
            return {
                "status": "failed",
                "message": "数据量不足（至少需要5个点）",
                "inflections": [],
                "core_inflections": []
            }
        
        # 步骤1：数据平滑（去噪声）
        smooth_data = self._moving_average_smooth(acc_data)
        
        # 步骤2：提取原始数据的统计特征（用于分析）
        raw_features = self._get_statistical_features(acc_data)
        
        # 步骤3：检测候选拐点
        candidates = self._detect_candidate_inflections(smooth_data)
        
        # 步骤4：去重
        deduplicated = self._deduplicate_inflections(candidates)
        
        # 步骤5：排序并标记核心拐点
        ranked_inflections = self._rank_inflections(deduplicated)
        
        # 筛选核心拐点
        core_inflections = [inf for inf in ranked_inflections if inf["is_core"]]
        
        return {
            "status": "success",
            "config": {
                "time_interval": self.time_interval,
                "smooth_window": self.smooth_window,
                "sensitivity": self.sensitivity
            },
            "statistical_features": raw_features,
            "all_inflections": ranked_inflections,  # 所有拐点（按强度排序）
            "core_inflections": core_inflections,   # 核心拐点（最显著的20%）
            "inflection_count": len(ranked_inflections),
            "core_inflection_count": len(core_inflections)
        }

def moving_average_smooth(acc_data, window_size=5):
    """纯手写滑动平均平滑函数（去抖动）"""
    if window_size < 2 or len(acc_data) < window_size:
        return acc_data.copy()
    
    smoothed = []
    for i in range(len(acc_data)):
        start = max(0, i - window_size // 2)
        end = min(len(acc_data), i + window_size // 2 + 1)
        window_sum = sum(acc_data[start:end])
        window_avg = window_sum / (end - start)
        smoothed.append(window_avg)
    
    return smoothed

def _find_risk_segments(
    comprehensive_frames: List[Dict]
) -> List[List[Dict]]:
    """查找连续风险段落：unp段落匹配的fusion段落crash_risk_ttc < 20（适配字典格式）"""
    risk_segments = []
    current_segment = []
    C_min_risk_segment_length = 5
    C_min_risk_ttc = 30
    C_min_start_vel = 2
    prev_comp_frame = None

    print(f"寻找风险段落")
    for comp_frame in comprehensive_frames:
        unp_frame_data = comp_frame["unp_frame_data"]
        base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        if base_time_ms is None:
            if current_segment:
                current_segment = []
            prev_comp_frame = comp_frame
            continue

        matched_fusion = comp_frame["matched_fusion"]
        if not matched_fusion:
            if current_segment:
                current_segment = []
            prev_comp_frame = comp_frame
            continue

        matched_chassis = comp_frame["matched_chassis"]
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
        is_set_speed_stable = _is_set_speed_stable(comp_frame, prev_comp_frame)
        traffic_light = unp_frame_data.get("TrafficLight", {})
        f_redlight = traffic_light.get("f_RedLightOn", False)
        f_stop_dis = traffic_light.get("stop_distance", 999)

        is_valid_start = not f_stationary and curr_vel > C_min_start_vel
        is_risk = (
            fusion_ttc is not None and
            (f_potential_cipv_by_pos or fusion_ttc < C_min_risk_ttc) and
            (f_np_on is True and not is_non_straight_driving and is_set_speed_stable) and
            f_stationary is False and
            not (f_redlight and f_stop_dis < 10)
        )

        if is_risk:
            if current_segment:
                current_segment.append(comp_frame)
            else:
                if is_valid_start and _is_primary_sequence_frame(comp_frame):
                    current_segment.append(comp_frame)
        else:
            if current_segment:
                if len(current_segment) > C_min_risk_segment_length and f_stationary is True:
                    risk_segments.append(current_segment)
                current_segment = []

        prev_comp_frame = comp_frame

    print(f"————————————找到 {len(risk_segments)} 个跟停连续风险段落————————————————")
    return risk_segments

def _calc_segment_fluct_times(
    segment: List[Dict]  # 适配：List[Dict]
) -> Optional[Dict]:
    """
    新功能：统计自车加速度（accleration_on_wheel）的波峰波谷次数
    逻辑：
    1. 提取chassis中的加速度数据并做有效性校验
    2. 滑动平均平滑处理（消除抖动）
    3. 检测平滑后数据的波峰、波谷数量
    """
    print("="*50)
    print("开始执行 _calc_segment_fluct_times 函数")
    print("="*50)
    
    # 可配置参数（根据抖动程度调整）
    WINDOW_SIZE = 7          # 滑动平均窗口大小（越大越平滑）
    CHECK_RANGE = 10          # 极值检测范围（左右各3帧）
    print(f"当前配置参数 - 滑动平均窗口大小：{WINDOW_SIZE}，极值检测范围：{CHECK_RANGE}")

    # ========== 步骤1：收集segment中每个unp对应的chassis加速度数据 ==========
    print("\n【步骤1/5】开始提取并校验chassis加速度数据...")
    chassis_data_list = []
    acc_raw_list = []  # 纯加速度数值列表（用于后续波峰波谷检测）
    
    total_frames = len(segment)
    print(f"待处理的总帧数：{total_frames}")
    
    for idx, comp_frame in enumerate(segment):
        if idx % 50 == 0:  # 每50帧打印一次进度，避免输出过多
            print(f"  正在处理第 {idx}/{total_frames} 帧数据")
            
        unp_frame_data = comp_frame["unp_frame_data"]
        # 获取chassis基准时间
        chassis_base_time = lonprepro._get_unp_chassis_meta_time(unp_frame_data)
        
        if not chassis_base_time:
            continue
            
        # 匹配对应的chassis段落
        matched_chassis = comp_frame["matched_chassis"]
        if not matched_chassis:
            continue
        
        # 提取核心数据（注意字段名拼写：accleration_on_wheel 可能是 acceleration_on_wheel）
        acceleration = matched_chassis.get("accleration_on_wheel")
        timestamp = chassis_base_time
        
        # 数据有效性校验（仅关注加速度和时间戳）
        if None in [acceleration, timestamp]:
            print(f"  警告：第{idx}帧chassis数据不完整（加速度/时间戳为空），跳过")
            continue
        
        # 类型转换
        try:
            acceleration = float(acceleration)
            timestamp = float(timestamp)
        except (ValueError, TypeError):
            print(f"  警告：第{idx}帧chassis数据类型错误（非数值类型），跳过")
            continue
        
        # 存储有效数据
        chassis_data_list.append({
            "timestamp_ms": timestamp,
            "acceleration": acceleration,
            "unp_idx": idx
        })
        acc_raw_list.append(acceleration)  # 收集纯加速度数值
    
    print(f"【步骤1/5】数据提取完成 - 原始帧数：{total_frames}，有效加速度数据数：{len(acc_raw_list)}")
    
    # 无有效chassis数据
    if not chassis_data_list or len(acc_raw_list) < 3:
        print("【步骤1/5】警告：无有效chassis加速度数据或数据量不足（至少需要3个点），函数提前返回")
        return None

    # ========== 步骤2：加速度数据平滑（消除抖动） ==========
    print("\n【步骤2/5】开始对加速度数据进行滑动平均平滑处理...")
    acc_smooth_list = moving_average_smooth(acc_raw_list, window_size=WINDOW_SIZE)
    print(f"【步骤2/5】平滑处理完成 - 平滑前数据量：{len(acc_raw_list)}，平滑后数据量：{len(acc_smooth_list)}")
    # print(f"  平滑前前5个数据：{acc_raw_list[:5]}")
    # print(f"  平滑后前5个数据：{acc_smooth_list[:5]}")  # 可选：打印部分数据用于调试

    # ========== 步骤3：检测波峰波谷 ==========
    print("\n【步骤3/5】开始检测加速度数据的波峰波谷...")
    detector = AccelerationInflectionDetector(
        time_interval=0.1,
        smooth_window=0,
        sensitivity=1.0
    )
    print(f"  初始化检测器参数 - 时间间隔：0.1s，平滑窗口：0，灵敏度：1.0")
    detection_result = detector.detect(acc_smooth_list)
    print(f"【步骤3/5】波峰波谷检测完成 - 检测到的总拐点数量：{len(detection_result.get('all_inflections', []))}")

    # 步骤4：组装最终结果（关联原始时间戳）
    print("\n【步骤4/5】开始组装最终拐点结果（关联原始时间戳）...")
    final_inflections = []
    for inf in detection_result["all_inflections"]:
        idx = inf["index"]
        timestamp_ms = chassis_data_list[idx]["timestamp_ms"] if 0 <= idx < len(chassis_data_list) else None
        final_inflections.append({
            "time_s": inf["time_s"],
            "timestamp_ms": timestamp_ms,
            "acceleration": inf["acceleration"],
            "type": inf["type"],
            "strength": inf["strength"],
            "is_core": inf["is_core"],
        })
    
    final_core_inflections = [inf for inf in final_inflections if inf["is_core"]]
    print(f"【步骤4/5】结果组装完成 - 总拐点数：{len(final_inflections)}，核心拐点数：{len(final_core_inflections)}")

    # ========== 步骤5：组装返回结果 ==========
    print("\n【步骤5/5】开始组装最终返回结果...")
    first_inflection_time_ms = float(final_inflections[0]["timestamp_ms"]) if final_inflections and final_inflections[0].get("timestamp_ms") is not None else 0.0
    last_inflection_time_ms = float(final_inflections[-1]["timestamp_ms"]) if final_inflections and final_inflections[-1].get("timestamp_ms") is not None else first_inflection_time_ms
    result = {
        "detection_config": detection_result["config"],
        "statistical_features": detection_result["statistical_features"],
        "all_inflections": final_inflections,
        "core_inflections": final_core_inflections,
        "inflection_count": len(final_inflections),
        "core_inflection_count": len(final_core_inflections),
        "first_inflection_time_ms": first_inflection_time_ms,
        "last_inflection_time_ms": last_inflection_time_ms,
    }

    print(f"【步骤5/5】函数执行完成 - 返回结果包含：总拐点数 {len(final_inflections)}，核心拐点数 {len(final_core_inflections)}")
    print("="*50)
    
    return result

class AccelerationInflectionDetector:
    """
    通用加速度曲线拐点识别器
    适配任意加速度曲线，无需硬编码阈值/时间点
    """
    def __init__(self, time_interval, smooth_window, sensitivity):
        """
        :param time_interval: 数据采样时间间隔（秒），默认0.1s
        :param smooth_window: 滑动平均平滑窗口大小（越大越平滑），默认5
        :param sensitivity: 识别敏感度（1.0为标准，<1更严格，>1更宽松）
        """
        self.time_interval = time_interval
        self.smooth_window = smooth_window
        self.sensitivity = sensitivity

    def _moving_average_smooth(self, data: List[float]) -> List[float]:
        """通用滑动平均平滑（去噪声）"""
        if len(data) <= self.smooth_window or self.smooth_window < 1:
            return data.copy()
        smoothed = []
        for i in range(len(data)):
            start = max(0, i - self.smooth_window // 2)
            end = min(len(data), i + self.smooth_window // 2 + 1)
            smoothed.append(sum(data[start:end]) / (end - start))
        return smoothed

    def _calculate_slopes(self, data: List[float]) -> List[float]:
        """计算相邻点的斜率（加速度变化率）"""
        slopes = []
        for i in range(1, len(data)):
            slope = (data[i] - data[i-1]) / self.time_interval
            slopes.append(slope)
        return slopes

    def _get_statistical_features(self, data: List[float]) -> Dict:
        """提取数据的统计特征（自适应判定依据）"""
        if len(data) < 3:
            return {"mean": 0, "std": 0, "median": 0, "iqr": 0}
        
        # 基础统计量
        mean = sum(data) / len(data)
        std = math.sqrt(sum([(x - mean)**2 for x in data]) / len(data))
        sorted_data = sorted(data)
        median = sorted_data[len(sorted_data)//2]
        
        # 四分位数间距（IQR，衡量数据离散程度）
        q1 = sorted_data[len(sorted_data)//4]
        q3 = sorted_data[3*len(sorted_data)//4]
        iqr = q3 - q1
        
        return {
            "mean": mean,
            "std": std,
            "median": median,
            "iqr": iqr,
            "min": min(data),
            "max": max(data)
        }

    def _detect_candidate_inflections(self, smooth_data: List[float]) -> List[Dict]:
        """检测候选拐点（基于斜率突变+统计特征）"""
        if len(smooth_data) < 5:
            return []
        
        # 步骤1：计算斜率和斜率变化率
        slopes = self._calculate_slopes(smooth_data)
        slope_changes = [abs(slopes[i] - slopes[i-1]) for i in range(1, len(slopes))]
        
        # 步骤2：提取斜率变化率的统计特征（自适应判定阈值）
        slope_features = self._get_statistical_features(slope_changes)
        # 拐点判定阈值：斜率变化率 > （中位数 + 敏感度×标准差）
        inflection_threshold = slope_features["median"] + self.sensitivity * slope_features["std"]
        
        # 步骤3：筛选候选拐点（斜率突变+局部极值）
        candidates = []
        for i in range(1, len(smooth_data)-1):  # 跳过首尾
            # 对应斜率变化率的索引
            sc_idx = i-2 if i-2 >= 0 else 0
            curr_slope_change = slope_changes[sc_idx] if sc_idx < len(slope_changes) else 0
            
            # 条件1：斜率变化率超过自适应阈值
            if curr_slope_change < inflection_threshold:
                continue
            
            # 条件2：是局部极值（左右各2帧内的最大/最小值）
            left_vals = smooth_data[max(0, i-2):i]
            right_vals = smooth_data[i+1:min(len(smooth_data), i+3)]
            is_peak = smooth_data[i] >= max(left_vals + right_vals) if left_vals and right_vals else False
            is_valley = smooth_data[i] <= min(left_vals + right_vals) if left_vals and right_vals else False
            
            if not (is_peak or is_valley):
                continue
            
            # 计算拐点类型和强度
            inflection_type = "peak" if is_peak else "valley"
            # 拐点强度：斜率变化率 / 阈值（越大越重要）
            strength = round(curr_slope_change / inflection_threshold, 2)
            
            candidates.append({
                "index": i,
                "time_s": round(i * self.time_interval, 2),
                "acceleration": round(smooth_data[i], 3),
                "type": inflection_type,
                "slope_change": round(curr_slope_change, 3),
                "strength": strength  # 拐点强度（≥1，越大越显著）
            })
        
        return candidates

    def _deduplicate_inflections(self, candidates: List[Dict], min_time_gap: float = 0.5) -> List[Dict]:
        """去重：合并时间间隔<min_time_gap的相邻拐点，保留强度最大的"""
        if not candidates:
            return []
        
        # 按时间排序
        sorted_candidates = sorted(candidates, key=lambda x: x["time_s"])
        deduplicated = [sorted_candidates[0]]
        
        for curr in sorted_candidates[1:]:
            last = deduplicated[-1]
            # 时间间隔不足，合并
            if curr["time_s"] - last["time_s"] < min_time_gap:
                if curr["strength"] > last["strength"]:
                    deduplicated[-1] = curr
            else:
                deduplicated.append(curr)
        
        return deduplicated

    def _rank_inflections(self, inflections: List[Dict]) -> List[Dict]:
        """按强度排序，标记核心拐点（前20%为核心）"""
        if not inflections:
            return []
        
        # 按强度降序排序
        ranked = sorted(inflections, key=lambda x: x["strength"], reverse=True)
        # 核心拐点阈值：前20%
        core_threshold_idx = max(1, len(ranked) // 5)
        
        for i, inf in enumerate(ranked):
            inf["rank"] = i + 1
            inf["is_core"] = i < core_threshold_idx  # 标记核心拐点
        
        return ranked

    def detect(self, acc_data: List[float]) -> Dict:
        """
        通用拐点识别主函数
        :param acc_data: 原始加速度数据列表
        :return: 包含拐点、统计特征、配置的完整结果
        """
        # 数据校验
        if len(acc_data) < 5:
            return {
                "status": "failed",
                "message": "数据量不足（至少需要5个点）",
                "inflections": [],
                "core_inflections": []
            }
        
        # 步骤1：数据平滑（去噪声）
        smooth_data = self._moving_average_smooth(acc_data)
        
        # 步骤2：提取原始数据的统计特征（用于分析）
        raw_features = self._get_statistical_features(acc_data)
        
        # 步骤3：检测候选拐点
        candidates = self._detect_candidate_inflections(smooth_data)
        
        # 步骤4：去重
        deduplicated = self._deduplicate_inflections(candidates)
        
        # 步骤5：排序并标记核心拐点
        ranked_inflections = self._rank_inflections(deduplicated)
        
        # 筛选核心拐点
        core_inflections = [inf for inf in ranked_inflections if inf["is_core"]]
        
        return {
            "status": "success",
            "config": {
                "time_interval": self.time_interval,
                "smooth_window": self.smooth_window,
                "sensitivity": self.sensitivity
            },
            "statistical_features": raw_features,
            "all_inflections": ranked_inflections,  # 所有拐点（按强度排序）
            "core_inflections": core_inflections,   # 核心拐点（最显著的20%）
            "inflection_count": len(ranked_inflections),
            "core_inflection_count": len(core_inflections)
        }

def moving_average_smooth(acc_data, window_size=5):
    """纯手写滑动平均平滑函数（去抖动）"""
    if window_size < 2 or len(acc_data) < window_size:
        return acc_data.copy()
    
    smoothed = []
    for i in range(len(acc_data)):
        start = max(0, i - window_size // 2)
        end = min(len(acc_data), i + window_size // 2 + 1)
        window_sum = sum(acc_data[start:end])
        window_avg = window_sum / (end - start)
        smoothed.append(window_avg)
    
    return smoothed

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
    prev_comp_frame = None
    print(f"寻找风险段落")
    # idx = 0  # 改用while循环，支持帧跳转
    # total_frames = len(comprehensive_frames)  # 【修改2】总帧数改为综合帧长度
        
    for idx, comp_frame in enumerate(comprehensive_frames):
        unp_frame_data = comp_frame["unp_frame_data"]
        base_time_ms = lonprepro._get_unp_fusion_meta_time(unp_frame_data)
        if base_time_ms is None:
            if current_segment:
                current_segment = []
            prev_comp_frame = comp_frame
            continue

        matched_fusion = comp_frame["matched_fusion"]
        if not matched_fusion:
            if current_segment:
                current_segment = []
            prev_comp_frame = comp_frame
            continue

        matched_chassis = comp_frame["matched_chassis"]
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
        is_set_speed_stable = _is_set_speed_stable(comp_frame, prev_comp_frame)
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
                current_segment.append(comp_frame)
            else:
                if is_valid_start and _is_primary_sequence_frame(comp_frame):
                    current_segment.append(comp_frame)
        else:
            if current_segment:
                if len(current_segment) > C_min_risk_segment_length and f_stationary == True:
                    risk_segments.append(current_segment)
                current_segment = []

        prev_comp_frame = comp_frame

    print(f"————————————找到 {len(risk_segments)} 个跟停连续风险段落————————————————")
    return risk_segments
