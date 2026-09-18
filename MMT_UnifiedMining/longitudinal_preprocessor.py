# frame_processor.py
import os
import re
import traceback
import json
import time
import sys
from typing import List, Dict, Optional, Tuple, Any, Union


def _configure_console_encoding():
    """Avoid UnicodeEncodeError when Windows consoles default to GBK."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_configure_console_encoding()

def is_non_straight_driving_scene(unp_frame_data: Dict) -> bool:
    """Use the non-straight exclusion only when its source signals exist."""
    non_straight_info = unp_frame_data.get("non_straight_driving_info", {})
    source_flags = ("has_signal_direction", "has_map_turn_signal", "has_is_in_uturn")
    if not any(bool(non_straight_info.get(flag, False)) for flag in source_flags):
        return False
    return bool(non_straight_info.get("is_non_straight_scene", False))

# ------------------------------ 获取bag_path下的直接子文件夹列表（仅一级） ------------------------------
def get_immediate_subfolders(path):
    # 修复16：增加路径存在性检查，避免异常
    if not os.path.exists(path):
        print(f"警告：路径 {path} 不存在")
        return []
    return [f for f in os.listdir(path) if os.path.isdir(os.path.join(path, f))]

def _format_json(json_str):
    """简单的 JSON 展开缩进（不使用第三方库）"""
    indent_level = 0
    result = []
    in_quotes = False

    for char in json_str:
        if char == '"':
            in_quotes = not in_quotes
            result.append(char)
        elif char == '{' and not in_quotes:
            indent_level += 1
            result.append(char)
            result.append("\n" + "    " * indent_level)
        elif char == '}' and not in_quotes:
            indent_level -= 1
            result.append("\n" + "    " * indent_level)
            result.append(char)
        elif char == ',' and not in_quotes:
            result.append(char)
            result.append("\n" + "    " * indent_level)
        else:
            result.append(char)

    return ''.join(result)

def _dict_to_log_str(data: Any, indent_level: int = 0) -> str:
    """
    递归将字典/列表/基础类型转换为格式化的日志字符串
    核心修复：用字符串拼接替代f-string花括号转义，彻底解决语法错误
    """
    indent = "  " * indent_level
    next_indent = "  " * (indent_level + 1)
    
    # 处理None值
    if data is None:
        return indent + "None"
    
    # 处理字符串（避免过长内容）
    if isinstance(data, str):
        if len(data) > 500:
            return indent + "'" + data[:500] + "...' (总长度: " + str(len(data)) + " 字符)"
        return indent + "'" + data + "'"
    
    # 处理数字/布尔值
    if isinstance(data, (int, float, bool)):
        return indent + str(data)
    
    # 处理列表
    if isinstance(data, list):
        if not data:
            return indent + "[] (空列表)"
        
        lines = [indent + "[ (共" + str(len(data)) + "个元素)"]
        for idx, item in enumerate(data):
            item_str = _dict_to_log_str(item, indent_level + 2).lstrip()
            lines.append(next_indent + "[" + str(idx) + "]: " + item_str)
        lines.append(indent + "]")
        return "\n".join(lines)
    
    # 处理字典（核心逻辑：用拼接替代f-string，彻底解决花括号问题）
    if isinstance(data, dict):
        if not data:
            return indent + "{} (空字典)"
        
        # 初始行：拼接 { 和统计信息，无转义问题
        lines = [indent + "{"]
        # 按键名排序遍历
        for key in sorted(data.keys()):
            value = data[key]
            if key == "raw_content":  # 跳过原始内容（单独处理）
                continue
            lines.append(next_indent + str(key) + ":")
            lines.append(_dict_to_log_str(value, indent_level + 2))
        # 闭合行：直接写 }，无转义
        lines.append(indent + "}")
        return "\n".join(lines)
    
    # 其他类型
    return indent + str(data) + " (类型: " + type(data).__name__ + ")"

def _generate_frame_data_log(
    comprehensive_frames: List[Dict],
    folder_path: str,
    log_filename: str = "frame_data_log.txt"
) -> None:
    """
    独立的帧数据日志生成函数：自动解析字典内容，无需手动append
    """
    try:
        curr_path = os.getcwd()
        log_filepath = os.path.join(curr_path, log_filename)
        
        # 初始化日志头部
        # 【修改2】调整数据统计逻辑，从comprehensive_frames中提取统计信息
        total_unp_frames = len(comprehensive_frames)
        # 统计匹配到的fusion/chassis/msd_control帧数
        matched_fusion_count = sum(1 for frame in comprehensive_frames if frame["matched_fusion"])
        matched_chassis_count = sum(1 for frame in comprehensive_frames if frame["matched_chassis"])
        matched_msd_count = sum(1 for frame in comprehensive_frames if frame["matched_msd_control"])
        matched_body_count = sum(1 for frame in comprehensive_frames if frame["matched_body"])
        matched_mff_count = sum(1 for frame in comprehensive_frames if frame["matched_mff_info"])

        header_info = {
            "日志生成时间": time.strftime('%Y-%m-%d %H:%M:%S'),
            "数据统计": {
                "UNP总帧数": total_unp_frames,
                "匹配到Fusion总帧数": matched_fusion_count,
                "匹配到Chassis总帧数": matched_chassis_count,
                "匹配到Msd Control总帧数": matched_msd_count,
                "匹配到Body总帧数": matched_body_count,
                "匹配到Mff_Info总帧数": matched_mff_count,
                "日志保存路径": log_filepath
            }
        }
        
        with open(log_filepath, "w", encoding="utf-8") as f:
            f.write("=== 自动生成的帧数据日志 ===\n")
            f.write("="*80 + "\n\n")
            f.write(_dict_to_log_str(header_info) + "\n\n")
            f.write("="*80 + "\n\n")
        
        # 遍历每一帧UNP数据
        frame_count = 0
        for comp_frame in comprehensive_frames:
            frame_count += 1
            unp_frame_data = comp_frame["unp_frame_data"]  # 从综合帧中提取UNP数据
            unp_timestamp_ms = comp_frame["unp_timestamp_ms"]  # 提取UNP时间戳
            
            # 【修改4】直接从综合帧中获取已匹配的数据，无需重新计算基准时间和匹配
            matched_fusion = comp_frame["matched_fusion"]
            matched_chassis = comp_frame["matched_chassis"]
            matched_msd_control = comp_frame["matched_msd_control"]
            matched_body = comp_frame["matched_body"]
            matched_mff_info = comp_frame["matched_mff_info"]
            
            # 1. 获取基准时间（复用已有函数）
            fusion_base_time_ms = _get_unp_fusion_meta_time(unp_frame_data)
            chassis_base_time_ms = _get_unp_chassis_meta_time(unp_frame_data)
            msd_base_time_ms = _get_unp_msd_control_meta_time(unp_frame_data)
            body_base_time_ms = _get_unp_body_meta_time(unp_frame_data)
            mff_base_time_ms = matched_mff_info.get("timestamp_ms",None)
            
            # 3. 构建帧数据字典（自动解析的基础）
            frame_data_dict = {
                "帧基本信息": {
                    "帧序号": frame_count,
                    "UNP原始时间戳": unp_timestamp_ms if unp_timestamp_ms else '未知',
                    "Fusion基准时间(ms)": fusion_base_time_ms,
                    "Chassis基准时间(ms)": chassis_base_time_ms,
                    "Msd基准时间(ms)": msd_base_time_ms,
                    "Body基准时间(ms)": body_base_time_ms,
                    "Mff_Info基准时间(ms)": mff_base_time_ms
                },
                "UNP数据": {
                    "解析后数据": {k: v for k, v in unp_frame_data.items() if k != 'raw_content'},
                    "原始内容预览": (unp_frame_data.get('raw_content', '')[:200] + "...") 
                                    if isinstance(unp_frame_data.get('raw_content'), str) and len(unp_frame_data.get('raw_content', '')) > 200
                                    else unp_frame_data.get('raw_content', '无')
                },
                "匹配的Fusion数据": matched_fusion if matched_fusion else "未匹配到对应数据",
                "匹配的Chassis数据": matched_chassis if matched_chassis else "未匹配到对应数据",
                "匹配的Msd control数据": matched_msd_control if matched_msd_control else "未匹配到对应数据",
                "匹配的Body数据": matched_body if matched_body else "未匹配到对应数据",
                "匹配的Mff Info数据": matched_mff_info if matched_mff_info else "未匹配到对应数据"
            }
            
            # 4. 自动生成日志内容
            log_lines = [
                "\n" + "="*80,
                "第 " + str(frame_count) + " 帧数据",
                "="*80,
                _dict_to_log_str(frame_data_dict)
            ]
            
            # 5. 追加写入日志
            with open(log_filepath, "a", encoding="utf-8") as f:
                f.write("\n".join(log_lines))
        
        # 写入结尾统计
        end_stats = {
            "日志生成完成": {
                "处理总帧数": frame_count,
                "完成时间": time.strftime('%Y-%m-%d %H:%M:%S'),
                "日志文件大小": str(os.path.getsize(log_filepath) / 1024) + " KB" if os.path.exists(log_filepath) else "未知"
            }
        }
        
        with open(log_filepath, "a", encoding="utf-8") as f:
            f.write("\n\n" + "="*80)
            f.write("\n" + _dict_to_log_str(end_stats))
            f.write("\n" + "="*80)
        
        print(f"✅ 日志生成完成：{log_filepath}")
        
    except Exception as e:
        print(f"❌ 日志生成失败：{str(e)}")
        traceback.print_exc()

def _get_unp_fusion_meta_time(unp_frame_data: Dict) -> Optional[float]:
    """从unp段落提取基准时刻：/perception/fusion/object_auto下的metaTimeMs"""
    try:
        input_msg_info = unp_frame_data.get("InputMessageInfo", {}).get("messageInfos", {})
        fusion_obj = input_msg_info.get("/perception/fusion/object_auto", {})
        meta_time_ms = fusion_obj.get("metaTimeMs", None)
        return float(meta_time_ms) if meta_time_ms is not None else None
    except Exception as e:
        print(f"提取fusion基准时刻失败：{str(e)}")
        return None

def _get_unp_chassis_meta_time(unp_frame_data: Dict) -> Optional[float]:
    """从unp段落提取基准时刻：第一个/vehicle/chassis_report下的metaTimeMs"""
    try:
        input_msg_info = unp_frame_data.get("InputMessageInfo", {}).get("messageInfos", {})
        chassis_report = input_msg_info.get("/vehicle/chassis_report", {})
        meta_time_ms = chassis_report.get("metaTimeMs", None)
        return float(meta_time_ms) if meta_time_ms is not None else None
    except Exception as e:
        print(f"提取chassis基准时刻失败：{str(e)}")
        return None

def _get_unp_msd_control_meta_time(unp_frame_data: Dict) -> Optional[float]:
    """从unp段落提取基准时刻：/msd/prediction/prediction_result_auto下的metaTimeMs"""
    try:
        input_msg_info = unp_frame_data.get("InputMessageInfo", {}).get("messageInfos", {})
        msd_obj = input_msg_info.get("/msd/prediction/prediction_result_auto", {})
        if msd_obj is None:
            msd_obj = input_msg_info.get("/vehicle/chassis_report", {})
        meta_time_ms = msd_obj.get("metaTimeMs", None)
        return float(meta_time_ms) if meta_time_ms is not None else None
    except Exception as e:
        print(f"提取msd基准时刻失败：{str(e)}")
        return None

def _get_unp_body_meta_time(unp_frame_data: Dict) -> Optional[float]:
    """从unp段落提取基准时刻：第一个/vehicle/body_report下的metaTimeMs"""
    try:
        input_msg_info = unp_frame_data.get("InputMessageInfo", {}).get("messageInfos", {})
        body_report = input_msg_info.get("/vehicle/body_report", {})
        meta_time_ms = body_report.get("metaTimeMs", None)
        return float(meta_time_ms) if meta_time_ms is not None else None
    except Exception as e:
        print(f"提取body基准时刻失败：{str(e)}")
        return None

def _find_closest_paragraph(base_time_ms: float, paragraphs: List[Dict]) -> Optional[Dict]:
    """通用时间匹配：找到与基准时刻最相近的目标段落（适配字典格式）"""
    if not paragraphs:
        return None

    min_diff = float('inf')
    matched = None
    for para in paragraphs:
        ts = para.get("timestamp_ms", 0.0)
        diff = abs(ts - base_time_ms)
        if diff < min_diff:
            min_diff = diff
            matched = para

    return matched


def _split_timestamped_paragraphs(content: str, marker: str) -> List[Tuple[str, str]]:
    pattern = re.compile(r'(\d{19})\s+' + re.escape(marker) + r':\s*')
    matches = list(pattern.finditer(content))
    paragraphs: List[Tuple[str, str]] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        paragraph = content[match.end():next_start]
        if index + 1 < len(matches):
            paragraph = paragraph.rstrip()
        paragraphs.append((match.group(1), paragraph))
    return paragraphs

# ------------------------------ 预处理函数（分topic） ------------------------------
def _preprocess_unp_planning_info(file_path) -> str:  # 补充注解：返回字符串
    """预处理 _unp_planning_info.txt：过滤空行"""
    _unp_planning_info_filter = []

    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            content = f.read()
        
    output_lines = []

    # ===== 2. 按段落分割（以 19 位数字开头） =====
    paragraphs = re.split(r'(?m)^(\d{19} seq: \d+)', content)
    paragraphs = [p for p in paragraphs if p.strip()]  # 去掉空段落

    # ===== 3. 逐段处理 =====
    for i in range(0, len(paragraphs), 2):
        header = paragraphs[i]
        body = paragraphs[i + 1] if i + 1 < len(paragraphs) else ""

        output_lines.append(header)  # 保留段落头

        # 处理 body 中的 frame_id
        body_lines = body.strip().split('\n')
        frame_id_buffer = None
        for line in body_lines:
            stripped_line = line.strip()
            if stripped_line.startswith("frame_id:"):
                # 提取 frame_id 行内容
                frame_id_part = stripped_line.split("frame_id:", 1)[1].strip()
                # 去掉首尾的引号
                frame_id_part = re.sub(r'^["\']|["\']$', '', frame_id_part)
                # 去掉反斜杠续行符
                frame_id_part = frame_id_part.replace('\\', '')
                frame_id_buffer = frame_id_part
            elif frame_id_buffer is not None:
                # 继续读取多行的 frame_id 内容
                line_content = stripped_line.replace('\\', '')  # 去掉反斜杠
                frame_id_buffer += line_content
                # 如果这一行不是续行（即没有反斜杠结尾），则处理并输出
                if not line.endswith('\\'):
                    output_lines.append(f"frame_id: {frame_id_buffer}")
                    frame_id_buffer = None
            else:
                # 其他行直接保留
                output_lines.append(line.rstrip("\n"))

    _unp_planning_info_filter = "\n".join(output_lines)

    return _unp_planning_info_filter


def _preprocess_perception_fusion_obj_auto(file_path) -> str:  # 补充注解：返回字符串
    """预处理 _perception_fusion_object_auto.txt：按段落拆分"""
    _perception_fusion_obj_auto_filter = ""  # 修复5：初始化为字符串，匹配最终赋值

    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            _perception_fusion_obj_auto_filter = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            _perception_fusion_obj_auto_filter = f.read()
    return _perception_fusion_obj_auto_filter


def _preprocess_vehicle_chassis_report(file_path) -> str:  # 补充注解：返回字符串
    """预处理 _vehicle_chassis_report.txt：提取key-value"""
    _vehicle_chassis_report_filter = ""  # 修复6：初始化为字符串，而非字典（避免类型冲突）

    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            _vehicle_chassis_report_filter = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            _vehicle_chassis_report_filter = f.read()
    return _vehicle_chassis_report_filter

def _preprocess_msd_control_report(file_path) -> str:
    """预处理 _msd_endpoint_control_command.txt：提取key-value"""
    _msd_control_filter = ""  # 修复6：初始化为字符串，而非字典（避免类型冲突）

    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            _msd_control_filter = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            _msd_control_filter = f.read()
    return _msd_control_filter

def _preprocess_vehicle_body_report(file_path) -> str:  # 补充注解：返回字符串
    """预处理 _vehicle_body_report.txt：提取key-value"""
    _vehicle_chassis_body_filter = ""  # 修复6：初始化为字符串，而非字典（避免类型冲突）

    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            _vehicle_chassis_body_filter = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            _vehicle_chassis_body_filter = f.read()
    return _vehicle_chassis_body_filter

def _preprocess_mff_info(file_path) -> str:  # 补充注解：返回字符串
    """预处理 _mff_info.txt：提取key-value"""
    _mff_info_filter = ""  # 修复6：初始化为字符串，而非字典（避免类型冲突）

    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            _mff_info_filter = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            _mff_info_filter = f.read()
    return _mff_info_filter

def _parse_unp_planning(unp_content: str) -> List[Dict]:
    """解析unp_planning_content，提取每个段落的headerTimeMs和完整frame数据"""
    parsed = []
    if not unp_content:
        return parsed

    # 修复11：unp_content已是字符串，无需join
    full_content = unp_content.strip()
    paragraphs = _split_timestamped_paragraphs(full_content, "seq")
    print(f"ℹ️  unp正则匹配到的段落数量：{len(paragraphs)}")

    # 匹配stamp下的secs和nsecs的正则表达式
    stamp_pattern = r'stamp:\s*\n\s*secs:\s*(\d+)\s*\n\s*nsecs:\s*(\d+)'

    for idx, (timestamp_str, para) in enumerate(paragraphs):
        try:
            # 匹配stamp中的secs和nsecs并组合计算header_time_ms
            stamp_match = re.search(stamp_pattern, para, re.DOTALL)
            if stamp_match:
                secs = int(stamp_match.group(1))
                nsecs = int(stamp_match.group(2))
                # 组合成毫秒：秒转毫秒(secs*1000) + 纳秒转毫秒(nsecs/1e6)
                header_time_ms = secs * 1000 + nsecs / 1_000_000
            else:
                # 兼容处理：匹配不到时使用原timestamp_str并打印提示
                print(f"⚠️  段落{idx}未匹配到stamp的secs和nsecs，使用原timestamp_str")
                header_time_ms = float(timestamp_str)

            frame_data = {"raw_content": ""}
            input_msg_match = re.search(r'"InputMessageInfo":\s*{\s*"messageInfos":\s*{([\s\S]*?)}\s*}', para, re.MULTILINE)
            if input_msg_match:
                frame_data["InputMessageInfo"] = {"messageInfos": {}}
                fusion_meta_match = re.search(r'/perception/fusion/object_auto.*?"metaTimeMs":\s*(\d+\.?\d*)', input_msg_match.group(1),re.DOTALL)
                if fusion_meta_match:
                    frame_data["InputMessageInfo"]["messageInfos"]["/perception/fusion/object_auto"] = {
                        "metaTimeMs": fusion_meta_match.group(1)
                    }
                chassis_meta_match = re.search(r'/vehicle/chassis_report.*?"metaTimeMs":\s*(\d+\.?\d*)', input_msg_match.group(1),re.DOTALL)
                if chassis_meta_match:
                    frame_data["InputMessageInfo"]["messageInfos"]["/vehicle/chassis_report"] = {
                        "metaTimeMs": chassis_meta_match.group(1)
                    }
                msd_meta_match = re.search(r'/msd/prediction/prediction_result_auto.*?"metaTimeMs":\s*(\d+\.?\d*)', input_msg_match.group(1),re.DOTALL)
                if msd_meta_match:
                    frame_data["InputMessageInfo"]["messageInfos"]["/msd/prediction/prediction_result_auto"] = {
                        "metaTimeMs": msd_meta_match.group(1)
                    }
                body_meta_match = re.search(r'/vehicle/body_report.*?"metaTimeMs":\s*(\d+\.?\d*)', input_msg_match.group(1),re.DOTALL)
                if body_meta_match:
                    frame_data["InputMessageInfo"]["messageInfos"]["/vehicle/body_report"] = {
                        "metaTimeMs": body_meta_match.group(1)
                    }
            # 匹配Np是否打开开关
            frame_data["NP_State"] = {"f_Np_on": False}
            np_state_match = re.search(r'"FrameInfo"\s*:\s*{[\s\S]*?"function"\s*:\s*"([^"]+)"(?=[\s\S]*?})', para, re.DOTALL)
            if np_state_match:
                function_value = np_state_match.group(1)
                frame_data["NP_State"]["function"] = function_value
                if function_value == "UNP":
                    frame_data["NP_State"]["f_Np_on"] = True
            else:
                print("未在FrameInfo中匹配到NP_function字段")

            # 匹配红绿灯信息 红绿灯状态，停止线距离
            frame_data["TrafficLight"] = {
                "f_RedLightOn": False,
                "stop_distance": 1000.0
            }
            # 匹配traffic_light_decider_info下的stop_flag
            stop_flag_match = re.search(
                r'"traffic_light_decider_info"\s*:\s*{[\s\S]*?"stop_flag"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para, 
                re.DOTALL
            )
            # 匹配traffic_light_decider_info下的stop_distance
            stop_distance_match = re.search(
                r'"traffic_light_decider_info"\s*:\s*{[\s\S]*?"stop_distance"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para, 
                re.DOTALL
            )
            # 处理stop_flag匹配结果
            if stop_flag_match:
                stop_flag_value = stop_flag_match.group(1).strip().lower()
                # 将字符串形式的布尔值转换为实际布尔值
                if stop_flag_value == "true":
                    frame_data["TrafficLight"]["f_RedLightOn"] = True
                elif stop_flag_value == "false":
                    frame_data["TrafficLight"]["f_RedLightOn"] = False
                else:
                    print("stop_flag值格式异常，非true/false")
            else:
                print("未在traffic_light_decider_info中匹配到stop_flag字段")
            # 处理stop_distance匹配结果
            if stop_distance_match:
                try:
                    stop_distance_value = float(stop_distance_match.group(1).strip())
                    frame_data["TrafficLight"]["stop_distance"] = stop_distance_value
                except ValueError:
                    print("stop_distance值无法转换为浮点数")
            else:
                print("未在traffic_light_decider_info中匹配到stop_distance字段")
            
            # 匹配ACC_Info信息
            frame_data["Acc_info"] = {
                "SetSpeed": 0.0
            }
            # 匹配acc_info下的cruise_velocity
            setspeed_match = re.search(
                r'"acc_info"\s*:\s*{[\s\S]*?"cruise_velocity"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para, 
                re.DOTALL
            )
            # 处理cruise_velocity匹配结果
            if setspeed_match:
                try:
                    setspeed_value = float(setspeed_match.group(1).strip())
                    frame_data["Acc_info"]["SetSpeed"] = setspeed_value
                except ValueError:
                    print("cruise_velocity值无法转换为浮点数")
            else:
                print("未在acc_info中匹配到cruise_velocity字段")

            # 匹配curvature_info信息
            frame_data["curvature_info"] = {
                "max_curvature":0.0,
                "curv_velocity_limit":100.0,
                "is_curv_v_limit_enable":False
            }
            # 匹配curvature_info下的max_curvature
            max_curvature_match = re.search(
                r'"curvature_info"\s*:\s*{[\s\S]*?"max_curvature"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para, 
                re.DOTALL
            )
            # 匹配curvature_info下的curv_velocity_limit
            curv_velocity_limit_match = re.search(
                r'"curvature_info"\s*:\s*{[\s\S]*?"curv_velocity_limit"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para, 
                re.DOTALL
            )
            # 匹配curvature_info下的is_curv_v_limit_enable
            is_curv_v_limit_enable_match = re.search(
                r'"curvature_info"\s*:\s*{[\s\S]*?"is_curv_v_limit_enable"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para, 
                re.DOTALL
            )

            if max_curvature_match:
                try:
                    max_curvature_value = float(max_curvature_match.group(1).strip())
                    frame_data["curvature_info"]["max_curvature"] = max_curvature_value
                except ValueError:
                    print("max_curvature值无法转换为浮点数")
            else:
                print("未在curvature_info中匹配到max_curvature字段")
            if curv_velocity_limit_match:
                try:
                    curv_velocity_limit_value = float(curv_velocity_limit_match.group(1).strip())
                    frame_data["curvature_info"]["curv_velocity_limit"] = curv_velocity_limit_value
                except ValueError:
                    print("curv_velocity_limit值无法转换为浮点数")
            else:
                print("未在curvature_info中匹配到curv_velocity_limit字段")
            if is_curv_v_limit_enable_match:
                is_curv_v_limit_enable_value = is_curv_v_limit_enable_match.group(1).strip().lower()
                if is_curv_v_limit_enable_value == "true":
                    frame_data["curvature_info"]["is_curv_v_limit_enable"] = True
                elif is_curv_v_limit_enable_value == "false":
                    frame_data["curvature_info"]["is_curv_v_limit_enable"] = False
                else:
                    print("is_curv_v_limit_enable值格式异常，非true/false")
                    frame_data["curvature_info"]["is_curv_v_limit_enable"] = is_curv_v_limit_enable_value
            else:
                print("未在curvature_info中匹配到is_curv_v_limit_enable字段")

            frame_data["non_straight_driving_info"] = {
                "signal_direction": 0,
                "map_turn_signal": 0,
                "is_in_uturn": False,
                "has_signal_direction": False,
                "has_map_turn_signal": False,
                "has_is_in_uturn": False,
                "is_non_straight_scene": False
            }
            signal_direction_match = re.search(
                r'"turn_signal_info"\s*:\s*{[\s\S]*?"signal_direction"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para,
                re.DOTALL
            )
            map_turn_signal_match = re.search(
                r'"intersection_info"\s*:\s*{[\s\S]*?"map_turn_signal"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para,
                re.DOTALL
            )
            is_in_uturn_match = re.search(
                r'"uturn_info"\s*:\s*{[\s\S]*?"is_in_uturn"\s*:\s*([^,}]+)(?=[\s\S]*?})',
                para,
                re.DOTALL
            )
            if signal_direction_match:
                try:
                    frame_data["non_straight_driving_info"]["signal_direction"] = int(float(signal_direction_match.group(1).strip()))
                    frame_data["non_straight_driving_info"]["has_signal_direction"] = True
                except ValueError:
                    print("signal_direction值无法转换为整数")
            else:
                print("未在turn_signal_info中匹配到signal_direction字段")
            if map_turn_signal_match:
                try:
                    frame_data["non_straight_driving_info"]["map_turn_signal"] = int(float(map_turn_signal_match.group(1).strip()))
                    frame_data["non_straight_driving_info"]["has_map_turn_signal"] = True
                except ValueError:
                    print("map_turn_signal值无法转换为整数")
            else:
                print("未在intersection_info中匹配到map_turn_signal字段")
            if is_in_uturn_match:
                is_in_uturn_value = is_in_uturn_match.group(1).strip().lower()
                if is_in_uturn_value == "true":
                    frame_data["non_straight_driving_info"]["is_in_uturn"] = True
                    frame_data["non_straight_driving_info"]["has_is_in_uturn"] = True
                elif is_in_uturn_value == "false":
                    frame_data["non_straight_driving_info"]["is_in_uturn"] = False
                    frame_data["non_straight_driving_info"]["has_is_in_uturn"] = True
                else:
                    print("is_in_uturn值格式异常，非true/false")
            else:
                print("未在uturn_info中匹配到is_in_uturn字段")

            non_straight_info = frame_data["non_straight_driving_info"]
            signal_direction = non_straight_info["signal_direction"]
            map_turn_signal = non_straight_info["map_turn_signal"]
            is_in_uturn = non_straight_info["is_in_uturn"]
            frame_data["non_straight_driving_info"]["is_non_straight_scene"] = bool(
                (non_straight_info["has_signal_direction"] and signal_direction in (1, 2))
                or (non_straight_info["has_map_turn_signal"] and map_turn_signal in (1, 2))
                or (non_straight_info["has_is_in_uturn"] and is_in_uturn)
            )

            # 修正：返回字典格式，而非元组
            parsed.append({
                "timestamp_ms": header_time_ms,
                "frame_data": frame_data
            })
        except Exception as e:
            print(f"解析unp段落失败：{str(e)}，跳过该段")
            continue

    return parsed

def _parse_fusion_obj_auto(fusion_content: str) -> List[Dict]:
    """解析_perception_fusion_objects_auto，提取每个段落所有track_id的crash_risk_ttc并取最小值"""
    parsed = []
    if not fusion_content:
        print("❌ fusion_content 为空，无数据可解析")
        return parsed

    # 修复13：fusion_content已是字符串，无需join
    full_content = fusion_content.strip()
    print(f"ℹ️  合并后fusion内容长度：{len(full_content)} 字符")

    # 段落正则：捕获 19位数字 + 完整段落内容（直到下一个19位数字或结束）
    all_paragraphs = _split_timestamped_paragraphs(full_content, "header")
    print(f"ℹ️  正则匹配到的段落数量：{len(all_paragraphs)}")

    if len(all_paragraphs) == 0:
        print("⚠️  未匹配到任何fusion段落，检查段落开头格式")
        return parsed

    for idx, (timestamp_str, para_content) in enumerate(all_paragraphs):
        try:
            # 提取 meta 下的 sensor_timestamp_us（转换为ms）
            ts_pattern = r'meta:\s*[\s\S]*?sensor_timestamp_us:\s*(\d+)'
            ts_match = re.search(ts_pattern, para_content)
            if not ts_match:
                print(f"❌ 段落 {idx+1} 未找到 sensor_timestamp_us，跳过")
                continue
            timestamp_ms = int(ts_match.group(1)) / 1000

            # 提取 perception_fusion_objects_data 部分（包含所有目标）
            fusion_data_pattern = r'perception_fusion_objects_data:\s*([\s\S]*?)(?=reserved_infos:|$)'
            fusion_match = re.search(fusion_data_pattern, para_content)
            if not fusion_match:
                print(f"⚠️  段落 {idx+1} 未找到 perception_fusion_objects_data，跳过")
                # 修正：返回字典格式
                parsed.append({
                    "timestamp_ms": timestamp_ms,
                    "min_ttc": None,
                    "f_potential_cipv_by_pos": False,
                    "f_potential_cipv_in_trafficlight": False,
                    "closest_obj_x": None,
                    "closest_obj_y": None,
                    "closest_obj_vel": None,
                    "closest_obj_acc": None,
                    "closest_obj_len": None
                })
                continue
            fusion_data = fusion_match.group(1)

            # 【核心修复：精准匹配每个完整目标（track_id开头 + extra_json结尾）】
            object_pattern = r'(?s)track_id:\s*\d+[\s\S]*?extra_json:\s*".*?"'
            objects = re.findall(object_pattern, fusion_data)
            ttc_list = []
            f_potential_cipv_by_pos = False
            f_potential_cipv_in_trafficlight = False
            # 新增：存储满足位置条件的目标坐标 (x, y)
            valid_objs = []

            for obj in objects:
                # 验证目标是否包含 track_id 和 available: True（双重校验有效目标）
                track_id_match = re.search(r'track_id:\s*(\d+)', obj)
                available_match = re.search(r'available:\s*True', obj)
                if not track_id_match or not available_match:
                    continue  # 跳过无track_id或未激活的目标
                
                # 提取当前目标的 crash_risk_ttc（兼容整数/小数、多空格格式）
                ttc_match = re.search(r'crash_risk_ttc:\s*(\d+\.?\d*)', obj, re.MULTILINE)
                if ttc_match:
                    ttc_value = float(ttc_match.group(1))
                    ttc_list.append(ttc_value)

                # 提取relative_position下的x,y值（兼容正负、整数/小数、多行/多空格）
                pos_x_match = re.search(r'relative_position:\s*[\s\S]*?x:\s*(-?\d+\.?\d*)',obj,re.MULTILINE)
                pos_y_match = re.search(r'relative_position:\s*[\s\S]*?y:\s*(-?\d+\.?\d*)',obj,re.MULTILINE)
                pos_vel_match = re.search(r'velocity_relative_to_ground:\s*[\s\S]*?x:\s*(-?\d+\.?\d*)',obj,re.MULTILINE)
                pos_acc_match = re.search(r'acceleration_relative_to_ground:\s*[\s\S]*?x:\s*(-?\d+\.?\d*)',obj,re.MULTILINE)
                pos_len_match = re.search(r'shape:\s*[\s\S]*?length:\s*(-?\d+\.?\d*)',obj,re.MULTILINE)
                if pos_x_match and pos_y_match and pos_vel_match and pos_acc_match and pos_len_match:
                    pos_x_value = float(pos_x_match.group(1))
                    pos_y_value = float(pos_y_match.group(1))
                    pos_vel_value = float(pos_vel_match.group(1))
                    pos_acc_value = float(pos_acc_match.group(1))
                    pos_len_value = float(pos_len_match.group(1))
                    if pos_x_value >= 1 and pos_x_value <= 50 and pos_y_value >= -1.5 and pos_y_value <= 1.5:
                        f_potential_cipv_by_pos = True
                        # 新增：将满足条件的坐标加入列表
                        valid_objs.append((pos_x_value, pos_y_value, pos_vel_value, pos_acc_value, pos_len_value))
                    if pos_x_value >= 0 and pos_x_value <= 20 and pos_y_value >= -1.5 and pos_y_value <= 1.5:
                        f_potential_cipv_in_trafficlight = True
                else:
                    print(f"⚠️  段落 {idx+1} 未找到 relative_position_x 或 relative_position_y")

            # 计算当前段落的最小 crash_risk_ttc
            min_ttc = min(ttc_list) if ttc_list else None

            # 新增：找到pos_x最小的目标坐标（最接近的obj）
            closest_obj_x = None
            closest_obj_y = None
            closest_obj_vel = None
            closest_obj_acc = None
            closest_obj_len = None
            if valid_objs:
                # 按pos_x升序排序，取第一个（x最小）
                valid_objs_sorted = sorted(valid_objs, key=lambda x: x[0])
                closest_obj_x, closest_obj_y, closest_obj_vel, closest_obj_acc, closest_obj_len = valid_objs_sorted[0]
            
            # 修正：返回字典格式，而非元组
            parsed.append({
                "timestamp_ms": timestamp_ms,
                "min_ttc": min_ttc,
                "f_potential_cipv_by_pos": f_potential_cipv_by_pos,
                "f_potential_cipv_in_trafficlight": f_potential_cipv_in_trafficlight,
                "closest_obj_x": closest_obj_x,
                "closest_obj_y": closest_obj_y,
                "closest_obj_vel": closest_obj_vel,
                "closest_obj_acc": closest_obj_acc,
                "closest_obj_len": closest_obj_len
            })
        except Exception as e:
            print(f"❌ 段落 {idx+1} 解析抛出异常：{str(e)}")
            print(f"❌ 异常段落前500字符：\n{para_content[:500]}...")
            continue

    print(f"📊 解析完成：共解析 {len(parsed)}/{len(all_paragraphs)} 个有效段落\n")
    return parsed

def _parse_chassis_report(chassis_content: str) -> List[Dict]:
    """解析_vehicle_chassis_report：提取meta的timestamp_us/1000（时刻）和brake_info_report_data的accleration_on_wheel"""
    parsed = []
    if not chassis_content:
        print("❌ chassis_content 为空，无数据可解析")
        return parsed

    # 修复15：chassis_content已是字符串，无需join
    full_content = chassis_content.strip()
    print(f"ℹ️  合并后chassis内容长度：{len(full_content)} 字符")

    paragraphs = _split_timestamped_paragraphs(full_content, "header")
    print(f"ℹ️  正则匹配到的chassis段落数量：{len(paragraphs)}")

    if len(paragraphs) == 0:
        print("⚠️  未匹配到任何chassis段落，检查数据格式")
        return parsed

    for idx, (timestamp_str, para_content) in enumerate(paragraphs):
        try:
            # 提取 meta 下的 timestamp_us（唯一标识段落时间）
            ts_pattern = r'(?s)meta:\s*[\s\S]*?timestamp_us:\s*(\d+)'
            ts_match = re.search(ts_pattern, para_content)
            if not ts_match:
                print(f"❌ 段落 {idx+1} 未找到 meta.timestamp_us，跳过")
                continue
            timestamp_us = int(ts_match.group(1))
            timestamp_ms = timestamp_us / 1000  # 转换为ms

            # 提取 brake_info_report_data 下的 accleration_on_wheel
            acc_pattern = r'(?s)brake_info_report_data:\s*[\s\S]*?accleration_on_wheel:\s*(\-?\d+\.?\d*)'
            acc_match = re.search(acc_pattern, para_content)
            accleration = float(acc_match.group(1)) if acc_match else None

            vel_pattern = r'(?s)throttle_info_report_data:\s*[\s\S]*?vehicle_speed_average:\s*(\-?\d+\.?\d*)'
            vel_match = re.search(vel_pattern, para_content)
            velocity = float(vel_match.group(1)) if vel_match else None
            if velocity is None:
                print(f"⚠️  段落 {idx+1} 未找到 vehicle_speed_average，设为 None")

            throttle_pedal_percent_pattern = r'(?s)throttle_info_report_data:\s*[\s\S]*?throttle_pedal_percent:\s*(\-?\d+\.?\d*)'
            throttle_pedal_percent_match = re.search(throttle_pedal_percent_pattern, para_content)
            throttle_pedal_percent = (
                float(throttle_pedal_percent_match.group(1)) if throttle_pedal_percent_match else None
            )
            if throttle_pedal_percent is None:
                print(f"⚠️  段落 {idx+1} 未找到 throttle_pedal_percent，设为 None")

            throttle_report_data_match = re.search(
                r'(?s)throttle_report:\s*[\s\S]*?throttle_report_data:\s*([\s\S]*?)(?=\n\w|$)',
                para_content,
            )
            throttle_report_data_block = throttle_report_data_match.group(1) if throttle_report_data_match else ""
            throttle_override_match = re.search(
                r'(?m)^\s*override:\s*(True|False)\b',
                throttle_report_data_block,
            )
            throttle_override = (
                throttle_override_match.group(1) == "True" if throttle_override_match else None
            )
            if throttle_override is None:
                print(f"⚠️  段落 {idx+1} 未找到 throttle_report_data.override，设为 None")

            stationary_pattern = r'(?s)brake_info_report_data:\s*[\s\S]*?stationary:\s*(True|False)'
            stationary_match = re.search(stationary_pattern, para_content)
            if stationary_match:
                stationary_str = stationary_match.group(1)
                stationary = stationary_str == "True"  # "True"→True，"False"→False
            else:
                stationary = None  # 匹配失败时兜底为None
                print(f"⚠️  段落 {idx+1} 未找到 stationary，设为 None")

            driver_work_type_pattern = r'(?s)brake_report:\s*[\s\S]*?brake_report_data:\s*[\s\S]*?driver_work_type:\s*[\s\S]*?value:\s*(\d+)'
            dwt_match = re.search(driver_work_type_pattern, para_content)
            
            if dwt_match:
                driver_work_type_value = int(dwt_match.group(1))  # 转换为整数
            else:
                driver_work_type_value = None
                print(f"⚠️  段落 {idx+1} 未找到 driver_work_type.value，设为 None")

            # 修正：返回字典格式，而非元组
            parsed.append({
                "timestamp_ms": timestamp_ms,
                "accleration_on_wheel": accleration,
                "vehicle_speed_average": velocity,
                "throttle_pedal_percent": throttle_pedal_percent,
                "throttle_override": throttle_override,
                "stationary": stationary,
                "driver_work_type": driver_work_type_value
            })
        except Exception as e:
            print(f"❌ 段落 {idx+1} 解析失败：{str(e)}，跳过该段")
            continue

    print(f"📊 解析完成：共解析 {len(parsed)}/{len(paragraphs)} 个有效段落\n")
    return parsed

def _parse_msd_control_report(msd_control_content: str) -> List[Dict]:
    parsed = []
    if not msd_control_content:
        print("❌ msd_control_content 为空，无数据可解析")
        return parsed

    full_content = msd_control_content.strip()
    print(f"ℹ️  合并后msd_control内容长度：{len(full_content)} 字符")

    # 匹配段落的正则
    paragraphs = _split_timestamped_paragraphs(full_content, "header")
    print(f"ℹ️  正则匹配到的msd_control段落数量：{len(paragraphs)}")

    if len(paragraphs) == 0:
        print("⚠️  未匹配到任何msd_control段落，检查数据格式")
        return parsed

    for idx, (timestamp_str, para_content) in enumerate(paragraphs):
        try:
            # 提取meta.timestamp_us并转换为ms
            ts_pattern = r'(?s)meta:\s*[\s\S]*?timestamp_us:\s*(\d+)'
            ts_match = re.search(ts_pattern, para_content)
            if not ts_match:
                print(f"❌ 段落 {idx+1} 未找到 meta.timestamp_us，跳过")
                continue
            timestamp_us = int(ts_match.group(1))
            timestamp_ms = timestamp_us / 1000

            # 提取extra节点下的json字段内容
            extra_json_pattern = r'(?s)extra:\s*[\s\S]*?json:\s*\"([\s\S]*?)(?=\"\s*$|\"\s+\w+)'
            extra_json_match = re.search(extra_json_pattern, para_content)
            
            slope_acc = None
            acc_ref = None
            acc_wheel = None
            acc_ego_real = None
            if extra_json_match:
                extra_json_str = extra_json_match.group(1)
                # 最终优化：适配「\"slope_acc\"\\n:-0.1」格式的正则
                slope_acc_pattern = r'(?s)\"[\\\s]*slope_acc[\\\s]*\"[\\\s]*:\s*(\-?\d+\.?\d*|\-?\.\d+)'
                slope_acc_match = re.search(slope_acc_pattern, extra_json_str)
                
                if slope_acc_match:
                    slope_acc = float(slope_acc_match.group(1))

                acc_ref_pattern = r'(?s)\"[\\\s]*acc_ref[\\\s]*\"[\\\s]*:\s*(\-?\d+\.?\d*|\-?\.\d+)'
                acc_ref_match = re.search(acc_ref_pattern, extra_json_str)
                
                if acc_ref_match:
                    acc_ref = float(acc_ref_match.group(1))

                acc_wheel_pattern = r'(?s)\"[\\\s]*acc_wheel[\\\s]*\"[\\\s]*:\s*(\-?\d+\.?\d*|\-?\.\d+)'
                acc_wheel_match = re.search(acc_wheel_pattern, extra_json_str)
                
                if acc_wheel_match:
                    acc_wheel = float(acc_wheel_match.group(1))

                acc_ego_real_pattern = r'(?s)\"[\\\s]*acc_ego_real[\\\s]*\"[\\\s]*:\s*(\-?\d+\.?\d*|\-?\.\d+)'
                acc_ego_real_match = re.search(acc_ego_real_pattern, extra_json_str)
                
                if acc_ego_real_match:
                    acc_ego_real = float(acc_ego_real_match.group(1))
            else:
                print(f"⚠️  段落 {idx+1} 未找到extra节点下的json字段")

            # 收集结果
            parsed.append({
                "timestamp_ms": timestamp_ms,
                "slope_acc": slope_acc,
                "acc_ref": acc_ref,
                "acc_wheel": acc_wheel,
                "acc_ego_real": acc_ego_real
            })
        except Exception as e:
            print(f"❌ 段落 {idx+1} 解析失败：{str(e)}，跳过该段")
            continue

    print(f"📊 解析完成：共解析 {len(parsed)}/{len(paragraphs)} 个有效msd_control段落\n")
    return parsed

def _parse_body_report(body_content: str) -> List[Dict]:
    """解析_vehicle_body_report：提取meta的timestamp_us/1000（时刻）和vehicle_speed_on_dashboard"""
    parsed = []
    if not body_content:
        print("❌ body_content 为空，无数据可解析")
        return parsed

    # 修复15：body_content已是字符串，无需join
    full_content = body_content.strip()
    print(f"ℹ️  合并后body内容长度：{len(full_content)} 字符")

    paragraphs = _split_timestamped_paragraphs(full_content, "header")
    print(f"ℹ️  正则匹配到的body段落数量：{len(paragraphs)}")

    if len(paragraphs) == 0:
        print("⚠️  未匹配到任何body段落，检查数据格式")
        return parsed

    for idx, (timestamp_str, para_content) in enumerate(paragraphs):
        try:
            # 提取 meta 下的 timestamp_us（唯一标识段落时间）
            ts_pattern = r'(?s)meta:\s*[\s\S]*?timestamp_us:\s*(\d+)'
            ts_match = re.search(ts_pattern, para_content)
            if not ts_match:
                print(f"❌ 段落 {idx+1} 未找到 meta.timestamp_us，跳过")
                continue
            timestamp_us = int(ts_match.group(1))
            timestamp_ms = timestamp_us / 1000  # 转换为ms

            # 提取 brake_info_report_data 下的 accleration_on_wheel
            vel_pattern = r'(?s)auxiliary_report:\s*[\s\S]*?auxiliary_report_data:\s*[\s\S]*?vehicle_speed_on_dashboard:\s*(\-?\d+\.?\d*)'
            vel_match = re.search(vel_pattern, para_content)
            velocity = float(vel_match.group(1)) if vel_match else None

            # 修正：返回字典格式，而非元组
            parsed.append({
                "timestamp_ms": timestamp_ms,
                "velocity_on_dashboard": velocity,
            })
        except Exception as e:
            print(f"❌ 段落 {idx+1} 解析失败：{str(e)}，跳过该段")
            continue

    print(f"📊 解析完成：共解析 {len(parsed)}/{len(paragraphs)} 个有效段落\n")
    return parsed

def _parse_mff_info(mff_content: str) -> List[Dict]:
    """Parse _mff_info timestamp and cruise speed from cruise_speed_limit."""
    parsed = []
    if not mff_content:
        print("❌ mff_content 为空，无数据可解析")
        return parsed

    # 修复15：body_content已是字符串，无需join
    full_content = mff_content.strip()
    print(f"ℹ️  合并后mff_info内容长度：{len(full_content)} 字符")

    paragraphs = _split_timestamped_paragraphs(full_content, "seq")
    print(f"ℹ️  正则匹配到的mff_info段落数量：{len(paragraphs)}")

    if len(paragraphs) == 0:
        print("⚠️  未匹配到任何mff_info段落，检查数据格式")
        return parsed

    # 匹配stamp下的secs和nsecs的正则表达式
    stamp_pattern = r'stamp:\s*\n\s*secs:\s*(\d+)\s*\n\s*nsecs:\s*(\d+)'

    for idx, (timestamp_str, para_content) in enumerate(paragraphs):
        try:
            # 匹配stamp中的secs和nsecs并组合计算header_time_ms
            stamp_match = re.search(stamp_pattern, para_content, re.DOTALL)
            if stamp_match:
                secs = int(stamp_match.group(1))
                nsecs = int(stamp_match.group(2))
                # 组合成毫秒：秒转毫秒(secs*1000) + 纳秒转毫秒(nsecs/1e6)
                timestamp_ms = secs * 1000 + nsecs / 1_000_000
            else:
                # 兼容处理：匹配不到时使用原timestamp_str并打印提示
                print(f"⚠️  段落{idx}未匹配到stamp的secs和nsecs，使用原timestamp_str")
                timestamp_ms = float(timestamp_str)

            cruise_velocity_pattern = r'\\"cruise_speed_limit\\"[\\\s]*:\s*(-?\d+(?:\.\d+)?)'
            cruise_velocity_match = re.search(cruise_velocity_pattern, para_content)
            cruise_velocity = float(cruise_velocity_match.group(1)) if cruise_velocity_match else None

            # 修正：返回字典格式，而非元组
            parsed.append({
                "timestamp_ms": timestamp_ms,
                "cruise_velocity_kph": cruise_velocity,
            })
        except Exception as e:
            print(f"❌ 段落 {idx+1} 解析失败：{str(e)}，跳过该段")
            continue

    print(f"📊 解析完成：共解析 {len(parsed)}/{len(paragraphs)} 个有效段落\n")
    return parsed

def _build_comprehensive_frame_dict(
    unp_paragraphs: List[Dict],
    fusion_paragraphs: List[Dict],
    chassis_paragraphs: List[Dict],
    msd_control_paragraphs: List[Dict],
    body_paragraphs: List[Dict],
    mff_paragraphs: List[Dict]
) -> List[Dict]:
    comprehensive_frames = []
    
    for idx, unp_para in enumerate(unp_paragraphs):
        # 1. 基础UNP数据
        frame_dict = {
            "unp_timestamp_ms": unp_para.get("timestamp_ms"),
            "unp_frame_data": unp_para.get("frame_data", {}),
            "matched_fusion": None,
            "matched_chassis": None,
            "matched_msd_control": None,
            "matched_body": None,
            "matched_mff_info": None
        }
        
        unp_frame_data = frame_dict["unp_frame_data"]
        unp_frame_time = unp_para.get("timestamp_ms", None)
        
        # 2. 匹配对应的fusion、chassis、msd_control段落（复用原有匹配逻辑）
        # 获取各模块基准时间
        fusion_base_time_ms = _get_unp_fusion_meta_time(unp_frame_data)
        if fusion_base_time_ms is None: #检查是否只是单帧丢失数据，是的话就用上一帧数据代替
            if idx > 0 and idx < len(unp_paragraphs) - 1:
                if (_get_unp_fusion_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                and _get_unp_fusion_meta_time(unp_paragraphs[idx+1].get("frame_data", {}))):
                    fusion_base_time_ms = _get_unp_fusion_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                elif idx == len(unp_paragraphs) - 1:
                    if _get_unp_fusion_meta_time(unp_paragraphs[idx-1].get("frame_data", {})):
                        fusion_base_time_ms = _get_unp_fusion_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
        chassis_base_time_ms = _get_unp_chassis_meta_time(unp_frame_data)
        if chassis_base_time_ms is None: #检查是否只是单帧丢失数据，是的话就用上一帧数据代替
            if idx > 0 and idx < len(unp_paragraphs) - 1:
                if (_get_unp_chassis_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                and _get_unp_chassis_meta_time(unp_paragraphs[idx+1].get("frame_data", {}))):
                    chassis_base_time_ms = _get_unp_chassis_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                elif idx == len(unp_paragraphs) - 1:
                    if _get_unp_chassis_meta_time(unp_paragraphs[idx-1].get("frame_data", {})):
                        chassis_base_time_ms = _get_unp_chassis_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
        msd_base_time_ms = _get_unp_msd_control_meta_time(unp_frame_data)
        if msd_base_time_ms is None: #检查是否只是单帧丢失数据，是的话就用上一帧数据代替
            if idx > 0 and idx < len(unp_paragraphs) - 1:
                if (_get_unp_msd_control_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                and _get_unp_msd_control_meta_time(unp_paragraphs[idx+1].get("frame_data", {}))):
                    msd_base_time_ms = _get_unp_msd_control_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                elif idx == len(unp_paragraphs) - 1:
                    if _get_unp_msd_control_meta_time(unp_paragraphs[idx-1].get("frame_data", {})):
                        msd_base_time_ms = _get_unp_msd_control_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
        body_base_time_ms = _get_unp_body_meta_time(unp_frame_data)
        if body_base_time_ms is None: #检查是否只是单帧丢失数据，是的话就用上一帧数据代替
            if idx > 0 and idx < len(unp_paragraphs) - 1:
                if (_get_unp_body_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                and _get_unp_body_meta_time(unp_paragraphs[idx+1].get("frame_data", {}))):
                    body_base_time_ms = _get_unp_body_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
                elif idx == len(unp_paragraphs) - 1:
                    if _get_unp_body_meta_time(unp_paragraphs[idx-1].get("frame_data", {})):
                        body_base_time_ms = _get_unp_body_meta_time(unp_paragraphs[idx-1].get("frame_data", {}))
        
        # 匹配最近段落
        frame_dict["matched_fusion"] = _find_closest_paragraph(fusion_base_time_ms, fusion_paragraphs) if fusion_base_time_ms else None
        frame_dict["matched_chassis"] = _find_closest_paragraph(chassis_base_time_ms, chassis_paragraphs) if chassis_base_time_ms else None
        frame_dict["matched_msd_control"] = _find_closest_paragraph(msd_base_time_ms, msd_control_paragraphs) if msd_base_time_ms else None
        frame_dict["matched_body"] = _find_closest_paragraph(body_base_time_ms, body_paragraphs) if body_base_time_ms else None
        frame_dict["matched_mff_info"] = _find_closest_paragraph(unp_frame_time, mff_paragraphs) if unp_frame_time else None
        
        comprehensive_frames.append(frame_dict)
    
    print(f"✅ 构建完成 {len(comprehensive_frames)} 帧综合数据字典")
    return comprehensive_frames

def process_comprehensive_frames(bag_path) -> List[Dict]:
    """
    核心入口函数：处理topic预处理数据，生成并返回comprehensive_frames
    :param topic_filter_map: 包含各topic预处理内容的字典
    :param folder_path: 文件夹路径（用于日志生成）
    :return: comprehensive_frames 综合帧数据列表
    """

    # 需求topic清单
    required_topics = {
        "_unp_planning_info": "文件1",
        "_perception_fusion_object_auto": "文件2",
        "_vehicle_chassis_report": "文件3",
        "_msd_endpoint_control_command": "文件4",
        "_vehicle_body_report": "文件5",
        "_mff_info": "文件6"
    }

    bag_subfolders = get_immediate_subfolders(bag_path)
    is_bag_deepest = len(bag_subfolders) == 0

    if not is_bag_deepest:
        return []
    
    try:
        # 遍历路径：边遍历边处理，找到最深文件夹立即处理
        for root, dirs, files in os.walk(bag_path):
            # 判定是否为最深文件夹（当前文件夹下无子文件夹）
            is_deepest_folder = len(dirs) == 0
            if not is_deepest_folder:
                continue  # 非最深文件夹，跳过
            
            # -------------------------- 找到最深文件夹，立即处理 --------------------------
            folder_result = {
                "folder_path": root,
                "topic_filter_map": {},  # {topic: 预处理内容}
                "core_result": None,
                "error": None
            }

            print(f"\n[遍历目录] {root} | 子目录数：{len(dirs)} | 文件数：{len(files)}")
            try:
                # 1. 收集当前最深文件夹下的所有目标topic文件
                topic_file_map = {}
                exact_mff_file = os.path.join(root, "_mff_info.txt")
                if os.path.exists(exact_mff_file):
                    topic_file_map["_mff_info"] = exact_mff_file
                for file in files:
                    if file.endswith(".txt"):
                        for topic in required_topics.keys():
                            if topic == "_mff_info":
                                continue
                            # ========== 核心修改：匹配逻辑 ==========
                            # 去除topic前缀的下划线，得到基础名称
                            base_topic = topic.lstrip('_')
                            # 检查文件名是否包含基础名称（兼容带/不带下划线的情况）
                            if base_topic in file:
                                topic_file_map[topic] = os.path.join(root, file)
                                break  # 一个文件仅匹配一个topic

                # 校验：当前文件夹是否包含所有topic文件
                missing_topics = set(required_topics.keys()) - set(topic_file_map.keys())
                if missing_topics:
                    print(f"警告：文件夹 {root} 缺少{missing_topics}")

                # 2. 对当前文件夹下的每个topic文件执行预处理（边遍历边处理）
                for topic, file_path in topic_file_map.items():
                    if topic == "_unp_planning_info":
                        filter_content = _preprocess_unp_planning_info(file_path)
                    elif topic == "_perception_fusion_object_auto":
                        filter_content = _preprocess_perception_fusion_obj_auto(file_path)
                    elif topic == "_vehicle_chassis_report":
                        filter_content = _preprocess_vehicle_chassis_report(file_path)
                    elif topic == "_msd_endpoint_control_command":
                        filter_content = _preprocess_msd_control_report(file_path)
                    elif topic == "_vehicle_body_report":
                        filter_content = _preprocess_vehicle_body_report(file_path)
                    elif topic == "_mff_info":
                        filter_content = _preprocess_mff_info(file_path)
                    else:
                        filter_content = None
                        folder_result["error"] = f"无{topic}对应的预处理函数"
                    
                    folder_result["topic_filter_map"][topic] = filter_content
                    print(f"📤 预处理完成：{topic} 内容长度：{len(filter_content) if filter_content else 0} 行")

            except Exception as e:
                print(f"错误：处理文件夹 {root} 时异常 - {str(e)}")

    except Exception as e:
        print(f"错误：路径遍历异常 - {str(e)}")
    
    # 1. 提取6个topic的原始数据
    unp_planning_content = folder_result["topic_filter_map"].get("_unp_planning_info", "")
    fusion_obj_auto_content = folder_result["topic_filter_map"].get("_perception_fusion_object_auto", "")
    chassis_report_content = folder_result["topic_filter_map"].get("_vehicle_chassis_report", "")
    msd_control_content = folder_result["topic_filter_map"].get("_msd_endpoint_control_command", "")
    body_report_content = folder_result["topic_filter_map"].get("_vehicle_body_report", "")
    mff_info_content = folder_result["topic_filter_map"].get("_mff_info", "")

    # 2. 预处理：解析每个topic的段落数据（提取时刻和关键字段）
    unp_paragraphs = _parse_unp_planning(unp_planning_content)
    fusion_paragraphs = _parse_fusion_obj_auto(fusion_obj_auto_content)
    chassis_paragraphs = _parse_chassis_report(chassis_report_content)
    msd_control_paragraphs = _parse_msd_control_report(msd_control_content)
    body_paragraphs = _parse_body_report(body_report_content)
    mff_paragraphs = _parse_mff_info(mff_info_content)

    print(f"解析得到 {len(unp_paragraphs)} 个unp段落")
    print(f"解析得到 {len(fusion_paragraphs)} 个fusion段落")
    print(f"解析得到 {len(chassis_paragraphs)} 个chassis段落")
    print(f"解析得到 {len(msd_control_paragraphs)} 个msd_control段落")
    print(f"解析得到 {len(body_paragraphs)} 个body段落")
    print(f"解析得到 {len(mff_paragraphs)} 个mff_info段落")

    # 构建综合帧数据字典
    comprehensive_frames = _build_comprehensive_frame_dict(
        unp_paragraphs=unp_paragraphs,
        fusion_paragraphs=fusion_paragraphs,
        chassis_paragraphs=chassis_paragraphs,
        msd_control_paragraphs=msd_control_paragraphs,
        body_paragraphs=body_paragraphs,
        mff_paragraphs=mff_paragraphs
    )

    # 可选：生成帧数据日志（保持原有逻辑）
    enable_frame_log = False  # 默认为关闭，可根据需要开启
    if enable_frame_log:
        print("\n📝 开始生成帧数据日志...")
        _generate_frame_data_log(
            comprehensive_frames,
            folder_path=bag_path
        )

    return comprehensive_frames
