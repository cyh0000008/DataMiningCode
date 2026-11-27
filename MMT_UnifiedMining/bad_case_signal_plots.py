"""Embedded signal overviews for MMT's metric/folder-level Bad Case rows."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from jerk_utils import jerk_from_acceleration


@dataclass
class PlotSeries:
    label: str
    unit: str
    time_s: np.ndarray
    values: np.ndarray


CAN_SIGNALS = {
    "IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec": ("Acceleration", "m/s2"),
    "PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1": ("Vehicle speed", "km/h"),
    "Adaptive_Cruise_Disp_Stat.IACCDrvrSeltdSpd": ("Set speed", "km/h"),
    "Adaptive_Cruise_Disp_Stat.IACCAct376": ("ACC active", "state"),
    "PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv": ("Pedal override", "state"),
    "PPEI_Engine_Torque_Status_4_08D_M.IActAxleTrq": ("Actual drive torque", "Nm"),
    "PPEI_Chassis_General_Status_2.ICSTBATS_TrqVl": ("Actual brake torque", "Nm"),
}
COLORS = ("#1677c8", "#db463f", "#19865b", "#9d55b5", "#b07716", "#487580")


def excluded_signal(name: str) -> bool:
    return bool(re.search(r"^(?:abs|tcs|vcs|vse)(?:_|$)|\.I?(?:ABS|TCS|VCS|VSE)", name, re.IGNORECASE))


def numeric(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def can_series(session: Any, required_signals: list[str], origin: float,
               unp_gate: Any = None) -> tuple[list[PlotSeries], list[str]]:
    from can_unp_gate import ACC_ACTIVE_SIGNALS

    if session is None:
        raise ValueError("未找到可绘制的 CAN H5")
    series = []
    missing = []
    gate_range = session.common_time_range(required_signals) if unp_gate is not None else None
    for name in dict.fromkeys([*CAN_SIGNALS, *required_signals]):
        if excluded_signal(name):
            continue
        if not session.has_signal(name):
            missing.append(name)
            continue
        record = session.get_store().get_signal(name)
        t = np.asarray(record["timestamps"], dtype=float)
        values = np.asarray([numeric(value) for value in record["values"]], dtype=float)
        label, unit = CAN_SIGNALS.get(name, (name.split(".")[-1], "raw"))
        series.append(PlotSeries(label, unit, t - origin, values))
        if (unp_gate is not None and gate_range is not None
                and name in required_signals and name in ACC_ACTIVE_SIGNALS):
            axis = t[(t >= gate_range[0]) & (t <= gate_range[1])]
            unp = np.asarray(unp_gate.states(axis.tolist()), dtype=float)
            series.append(PlotSeries("Topic UNP on", "state", axis - origin, unp))
        if label == "Acceleration" and len(t) >= 2:
            series.append(PlotSeries("Sustained jerk (0.3s)", "m/s3", t - origin,
                                     jerk_from_acceleration(t, values)))
    return series, missing


def topic_series(frames: list[dict], origin: float, metric_module=None) -> tuple[list[PlotSeries], list[str]]:
    import ast
    import inspect
    keys_used = None
    if metric_module is not None:
        import longitudinal_preprocessor
        sources = [inspect.getsource(metric_module)]
        for name in ("is_front_clear", "is_acc_controlling", "is_driver_override", "is_non_straight_driving_scene"):
            sources.append(inspect.getsource(getattr(longitudinal_preprocessor, name)))
        keys_used = set()
        for source in sources:
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
                    key = node.args[0]
                elif isinstance(node, ast.Subscript):
                    key = node.slice
                else:
                    continue
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys_used.add(key.value)
    definitions = [
        ("Vehicle speed", "m/s", "matched_chassis", ("vehicle_speed_average",)),
        ("Wheel acceleration", "m/s2", "matched_chassis", ("accleration_on_wheel",)),
        ("Slope acceleration", "m/s2", "matched_msd_control", ("slope_acc",)),
        ("Front range", "m", "matched_fusion", ("closest_obj_x",)),
        ("Front TTC", "s", "matched_fusion", ("min_ttc",)),
        ("Front ID", "id", "matched_fusion", ("closest_obj_id",)),
        ("Set speed", "m/s", "unp_frame_data", ("Acc_info", "SetSpeed")),
        ("NP on", "state", "unp_frame_data", ("NP_State", "f_Np_on")),
        ("Pedal override", "state", "matched_chassis", ("throttle_override",)),
        ("Curvature limit enabled", "state", "unp_frame_data", ("curvature_info", "is_curv_v_limit_enable")),
        ("Curvature speed limit", "m/s", "unp_frame_data", ("curvature_info", "curv_velocity_limit")),
    ]
    series = []
    missing = []
    for label, unit, source, keys in definitions:
        samples = {}
        for frame in frames:
            data = frame.get(source) or {}
            timestamp = numeric(data.get("timestamp_ms", frame.get("unp_timestamp_ms")))
            value = data
            for key in keys:
                value = value.get(key) if isinstance(value, dict) else None
            if np.isfinite(timestamp):
                samples[timestamp / 1000.0 - origin] = numeric(value)
        t = np.asarray(sorted(samples), dtype=float)
        values = np.asarray([samples[time] for time in t], dtype=float)
        if np.any(np.isfinite(values)):
            series.append(PlotSeries(label, unit, t, values))
        else:
            missing.append(label)
    samples = {}
    for frame in frames:
        chassis = frame.get("matched_chassis") or {}
        control = frame.get("matched_msd_control") or {}
        timestamp = numeric(chassis.get("timestamp_ms"))
        if np.isfinite(timestamp):
            samples[timestamp / 1000.0 - origin] = (
                numeric(chassis.get("accleration_on_wheel")) + numeric(control.get("slope_acc")))
    t = np.asarray(sorted(samples), dtype=float)
    acceleration = np.asarray([samples[time] for time in t], dtype=float)
    if np.count_nonzero(np.isfinite(acceleration)) >= 2:
        series.append(PlotSeries("Total acceleration (wheel + slope)", "m/s2", t, acceleration))
        series.append(PlotSeries("Total sustained jerk (0.3s)", "m/s3", t,
                                 jerk_from_acceleration(t, acceleration)))
    else:
        missing.append("Total acceleration / jerk")
    # Keep planning and geometric targets separate rather than implying one selected target.
    for index in (0, 1):
        for key, unit in (("x", "m"), ("ttc", "s"), ("id", "id")):
            samples = {}
            for frame in frames:
                fusion = frame.get("matched_fusion") or {}
                targets = fusion.get("following_candidates", [])
                if len(targets) <= index:
                    value = None
                else:
                    value = targets[index].get(key)
                timestamp = numeric(fusion.get("timestamp_ms", frame.get("unp_timestamp_ms")))
                if np.isfinite(timestamp):
                    samples[timestamp / 1000.0 - origin] = numeric(value)
            t = np.asarray(sorted(samples), dtype=float)
            values = np.asarray([samples[time] for time in t], dtype=float)
            if np.any(np.isfinite(values)):
                series.append(PlotSeries(f"Candidate {index + 1} {key}", unit, t, values))
    # All numeric preprocessed fields remain available as evidence, including
    # nested target candidates and the flags used by shared scene predicates.
    def leaves(value, prefix):
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {"timestamp_ms", "raw_content"} and not excluded_signal(str(key)):
                    yield from leaves(child, prefix + "." + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from leaves(child, prefix + f"[{index}]")
        elif isinstance(value, (int, float, bool)):
            key = prefix.rsplit(".", 1)[-1]
            if keys_used is None or key in keys_used:
                yield prefix, numeric(value)

    channels = {}
    for frame in frames:
        for source in ("unp_frame_data", "matched_chassis", "matched_fusion",
                       "matched_msd_control", "matched_body", "matched_mff_info"):
            data = frame.get(source) or {}
            timestamp = numeric(data.get("timestamp_ms", frame.get("unp_timestamp_ms")))
            if not np.isfinite(timestamp):
                continue
            for key, value in leaves(data, source):
                channels.setdefault(key, {})[timestamp / 1000.0 - origin] = value
    for key, samples in sorted(channels.items()):
        axis = np.asarray(sorted(samples), dtype=float)
        series.append(PlotSeries(key, "raw", axis, np.asarray([samples[t] for t in axis])))
    road_samples = dict(sample for frame in frames for sample in frame.get("_road_excitation_samples", []))
    if road_samples:
        axis = np.asarray(sorted(road_samples), dtype=float)
        series.append(PlotSeries("/mla/egopose.acceleration.acceleration_local.az * 9.81", "m/s2",
                                 axis / 1000.0 - origin,
                                 np.asarray([road_samples[t] * 9.81 for t in axis])))
        for label, value in (("Road lower threshold", 8.31), ("Road upper threshold", 11.31)):
            series.append(PlotSeries(label, "m/s2", axis / 1000.0 - origin, np.full(len(axis), value)))
        from road_excitation import exclusion_mask
        axis = np.asarray([frame["unp_timestamp_ms"] for frame in frames], dtype=float)
        series.append(PlotSeries("Road excitation exclusion (+/-2.5s)", "state", axis / 1000.0 - origin,
                                 np.asarray(exclusion_mask(axis, list(road_samples.items())), dtype=float)))
    else:
        missing.append("/mla/egopose.acceleration.acceleration_local.az")
    return series, missing


def event_spans(detail: str) -> list[tuple[float, float]]:
    number = r"([-+]?\d+(?:\.\d+)?)"
    pattern = rf"\[Event\b[^\]]*\bstart\s*=\s*{number}\s*,\s*end\s*=\s*{number}"
    spans = []
    for start, end in re.findall(pattern, detail, flags=re.IGNORECASE):
        left, right = float(start), float(end)
        if np.isfinite(left) and np.isfinite(right) and 0 <= left <= right:
            spans.append((left, right))
    return spans


def plot_font() -> ImageFont.ImageFont:
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.sep).joinpath("usr", "share", "fonts", "opentype", "noto", "NotoSansCJK-Regular.ttc"),
        Path(os.sep).joinpath("usr", "share", "fonts", "truetype", "wqy", "wqy-zenhei.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), 18)
    return ImageFont.load_default()


def draw_overview(series: list[PlotSeries], title: str, spans: list[tuple[float, float]], path: Path) -> None:
    if len(series) > 18:
        for index, start_index in enumerate(range(0, len(series), 18), 1):
            page_path = path if index == 1 else path.with_name(f"{path.stem}_page{index:02d}.png")
            draw_overview(series[start_index:start_index + 18], f"{title} | page {index}", spans, page_path)
        return
    available = [item for item in series if np.count_nonzero(
        np.isfinite(item.time_s) & np.isfinite(item.values)) >= 2]
    if not available:
        raise ValueError("没有至少两个有效采样点的关键信号")
    groups: dict[str, list[PlotSeries]] = {}
    for item in available:
        groups.setdefault(item.unit, []).append(item)
    panels = []
    for unit, items in groups.items():
        count = 1 if unit == "raw" else 3
        panels.extend((unit, items[index:index + count]) for index in range(0, len(items), count))
    width, panel_height = 1500, 260
    image = Image.new("RGB", (width, 110 + len(panels) * panel_height), "#f6f8fc")
    draw = ImageDraw.Draw(image)
    font = plot_font()
    draw.text((20, 15), title, font=font, fill="#202c40")
    draw.text((20, 45), "Folder overview (not an individual event); time relative to primary recording start.",
              font=font, fill="#536176")
    draw.text((20, 72), "Shading: explicit [Event start/end] intervals only; other details are not inferred.",
              font=font, fill="#536176")
    start = min(float(np.min(item.time_s[np.isfinite(item.time_s)])) for item in available)
    end = max(float(np.max(item.time_s[np.isfinite(item.time_s)])) for item in available)
    if end <= start:
        raise ValueError("信号时间范围不足")
    for index, (unit, items) in enumerate(panels):
        left, right, top, bottom = 95, width - 25, 185 + index * panel_height, 325 + index * panel_height
        all_values = np.concatenate([item.values[np.isfinite(item.values)] for item in items])
        low, high = float(np.min(all_values)), float(np.max(all_values))
        margin = max((high - low) * .08, .1)
        low, high = low - margin, high + margin
        x = lambda value: left + (value - start) / (end - start) * (right - left)
        y = lambda value: bottom - (value - low) / (high - low) * (bottom - top)
        draw.rectangle((left, top, right, bottom), fill="white", outline="#ced6e2")
        for a, b in spans:
            if b >= start and a <= end:
                draw.rectangle((x(max(a, start)), top, x(min(b, end)), bottom), fill="#ffe5d4")
        for tick in range(5):
            value = low + (high - low) * tick / 4
            draw.text((5, y(value) - 8), f"{value:.2f}", font=font, fill="#536176")
        for tick in range(6):
            value = start + (end - start) * tick / 5
            draw.text((x(value) - 15, bottom + 5), f"{value:.1f}", font=font, fill="#536176")
        for line_index, item in enumerate(items):
            color = COLORS[line_index % len(COLORS)]
            label = f"{item.label} [{unit}]"
            draw.text((left, top - 68 + line_index * 21), label, font=font, fill=color)
            points = []
            previous = None
            t = item.time_s
            gaps = np.diff(t)
            positive = gaps[gaps > 0]
            gap_limit = max(float(np.median(positive)) * 3, .2) if positive.size else .2
            for time, value in zip(t, item.values):
                if (not np.isfinite(time) or not np.isfinite(value)
                        or (previous is not None and time - previous > gap_limit)):
                    if len(points) > 1:
                        draw.line(points, fill=color, width=2)
                    points = []
                if np.isfinite(time) and np.isfinite(value):
                    points.append((x(time), y(value)))
                previous = time
            if len(points) > 1:
                draw.line(points, fill=color, width=2)
    image.save(path)


def embed_plots(sheet: Any, rows: list[dict], load_series: Callable[[dict], tuple[list[PlotSeries], list[str]]],
                directory: Path) -> None:
    from copy import copy
    from openpyxl.drawing.image import Image as ExcelImage
    from openpyxl.styles import Alignment

    directory.mkdir(parents=True, exist_ok=True)
    sheet.cell(1, 11, "关键信号图")
    sheet.cell(1, 12, "绘图状态")
    for column in (11, 12):
        sheet.cell(1, column).font = copy(sheet.cell(1, 10).font)
        sheet.cell(1, column).fill = copy(sheet.cell(1, 10).fill)
        sheet.cell(1, column).alignment = copy(sheet.cell(1, 10).alignment)
    sheet.column_dimensions["K"].width = 125
    sheet.column_dimensions["L"].width = 60
    for index, row in enumerate(rows, start=2):
        path = directory / f"case_{index - 1:04d}.png"
        try:
            series, missing = load_series(row)
            missing = list(missing) + [
                item.label for item in series
                if np.count_nonzero(np.isfinite(item.time_s) & np.isfinite(item.values)) < 2
            ]
            spans = event_spans(str(row["detail_content"])) if row["pipeline_type"] == "can" else []
            draw_overview(series, f"{row['output_prefix']} | {Path(row['folder_path']).name}", spans, path)
            image = ExcelImage(path)
            scale = min(880 / image.width, 520 / image.height)
            image.width *= scale
            image.height *= scale
            sheet.add_image(image, f"K{index}")
            pages = sorted(directory.glob(path.stem + "_page*.png"))
            if pages:
                book = sheet.parent
                details = book["信号图详情"] if "信号图详情" in book.sheetnames else book.create_sheet("信号图详情")
                details.column_dimensions["A"].width = 55
                details.column_dimensions["B"].width = 125
                for page in [path, *pages]:
                    detail_row = details.max_row + 1
                    cell = details.cell(detail_row, 1, f"Bad Case行{index} / {page.name}")
                    page_image = ExcelImage(page)
                    ratio = min(880 / page_image.width, 520 / page_image.height)
                    page_image.width *= ratio
                    page_image.height *= ratio
                    details.add_image(page_image, f"B{detail_row}")
                    details.row_dimensions[detail_row].height = page_image.height * .75
            sheet.row_dimensions[index].height = image.height * .75
            status = "已生成；文件夹整段概览（非单事件判定图）"
            if pages:
                status += f"；共{len(pages) + 1}页，完整信号见信号图详情"
            if missing:
                status += "；缺少信号或有效采样不足：" + "、".join(missing)
            sheet.cell(index, 12, status)
            print(f"[信号图] {row['output_prefix']} {status}", flush=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            status = f"绘图失败：{exc}"
            sheet.cell(index, 11, status)
            sheet.cell(index, 12, status)
            print(f"[信号图] {row['output_prefix']} {row['folder_path']} {status}", flush=True)
        sheet.cell(index, 12).alignment = Alignment(wrap_text=True, vertical="top")
