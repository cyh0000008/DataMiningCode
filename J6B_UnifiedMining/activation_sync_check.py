from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import h5py
import numpy as np
from PIL import Image, ImageDraw, ImageFont


BUS_LABEL = "总线数据_标志位"
ETHERNET_LABEL = "以太网数据_激活标志位"

BUS_SIGNAL_CANDIDATES = [
    "ch3/CAN_PB/SGM_C1UL_PB_CAN_0227/0x194_ACC_AxlTrqReq_0x194/signals/ILCCC_Act",
    "ch2/CAN_PB/SGM_C1UL_PB_CAN_0227/0x1A3_ACC_CmdExt_0x1A3/signals/ILCCC_Act_1AX",
    "ch2/CAN_CB/SGM_C1UL_CB_CAN_0227/0x0B0_ADSCmd_Ext_0x0B0/signals/ILCCC_Act1_1AX",
    "ch1/CAN_PB/SGM_C1UL_PB_CAN_0227/0x194_ACC_AxlTrqReq_0x194/signals/ILCCC_Act",
    "ch2/CAN_PB/SGM_C1UL_PB_CAN_0227/0x194_ACC_AxlTrqReq_0x194/signals/ILCCC_Act",
    "ch4/CAN_PB/SGM_C1UL_PB_CAN_0227/0x194_ACC_AxlTrqReq_0x194/signals/ILCCC_Act",
]

ETHERNET_SIGNAL_CANDIDATES = [
    "ch4/Eth_Debug_CAN5/PATAC_Acore_Debug_Msg_v0p1p6_Release_0709/0xA84_DAebMPart1Obj21/signals/LCCC_Act",
    "ch4/Eth_Debug_CAN5/PATAC_Rcore_Debug_Msg_0p4p1/0x3D9_MPC_DebugMsg_Reserved_10/signals/Debug_uint_Reserved_11",
    "ch5/Eth_Debug_CAN5/PATAC_Acore_Debug_Msg_v0p1p6_Release_0709/0xA84_DAebMPart1Obj21/signals/LCCC_Act",
]


def log(message: str) -> None:
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


@dataclass
class FileSignals:
    path: Path
    file_time: Optional[dt.datetime]
    bus_time: np.ndarray
    bus_value: np.ndarray
    ethernet_time: np.ndarray
    ethernet_raw_value: np.ndarray


@dataclass
class EdgeMatch:
    file_path: Path
    file_time: Optional[dt.datetime]
    bus_time: float
    ethernet_time: float
    state: int
    lag_s: float

    @property
    def absolute_time(self) -> Optional[dt.datetime]:
        if self.file_time is None:
            return None
        return self.file_time + dt.timedelta(seconds=self.bus_time)


@dataclass
class SyncStats:
    input_dir: Path
    output_path: Path
    total_files: int
    used_files: int
    skipped_files: int
    bus_edge_count: int
    ethernet_edge_count: int
    comparable_points: int
    mismatch_points: int
    start_diffs: List[float]
    end_diffs: List[float]
    matches: List[EdgeMatch]
    near_example: Optional[EdgeMatch]
    delayed_example: Optional[EdgeMatch]
    file_signals: Dict[Path, FileSignals]


def main(argv: Optional[Sequence[str]] = None) -> int:
    start_time = time.perf_counter()
    args = parse_args(argv)
    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"数据文件夹不存在：{input_dir}")

    output_path = resolve_output_path(input_dir, args.output)
    log("开始同步性检查")
    log(f"输入文件夹：{input_dir}")
    log(f"输出图片：{output_path}")
    log(f"边沿匹配时间窗：{args.edge_match_window_s:.1f}秒")
    stats = run_check(input_dir, output_path, edge_match_window_s=args.edge_match_window_s)
    log("开始绘制 PNG")
    draw_png(stats)
    log(f"PNG 绘制完成：{output_path}")
    print_summary(stats)
    log(f"全部完成，用时 {time.perf_counter() - start_time:.1f}秒")
    return 0


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查总线数据_标志位与以太网数据_激活标志位的同步性，并生成中文版 PNG。",
    )
    parser.add_argument("input_dir", type=Path, help="h5 数据文件夹路径，会递归扫描所有 .h5/.hdf5 文件")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="可选，PNG 输出路径；不填时输出到数据文件夹下",
    )
    parser.add_argument(
        "--edge-match-window-s",
        type=float,
        default=10.0,
        help="边沿匹配最大时间窗，单位秒，默认 10 秒",
    )
    return parser.parse_args(argv)


def resolve_output_path(input_dir: Path, output: Optional[Path]) -> Path:
    if output is None:
        return input_dir / "总线数据_以太网数据_同步性检查.png"
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    return output


def run_check(input_dir: Path, output_path: Path, edge_match_window_s: float = 10.0) -> SyncStats:
    log("开始扫描 h5/hdf5 文件")
    h5_files = collect_h5_files(input_dir)
    if not h5_files:
        raise FileNotFoundError(f"未找到 h5 文件：{input_dir}")
    log(f"扫描完成，共找到 {len(h5_files)} 个 h5/hdf5 文件")

    file_signals: Dict[Path, FileSignals] = {}
    skipped_files = 0
    start_diffs: List[float] = []
    end_diffs: List[float] = []
    matches: List[EdgeMatch] = []
    bus_edge_count = 0
    ethernet_edge_count = 0
    comparable_points = 0
    mismatch_points = 0

    for file_index, h5_file in enumerate(h5_files, 1):
        file_start_time = time.perf_counter()
        log(f"[{file_index}/{len(h5_files)}] 读取文件：{h5_file.name}")
        signals = read_file_signals(h5_file)
        if signals is None:
            skipped_files += 1
            log("  跳过：缺少检查所需信号或时间戳")
            continue
        file_signals[h5_file] = signals

        start_diffs.append(float(signals.ethernet_time[0] - signals.bus_time[0]))
        end_diffs.append(float(signals.ethernet_time[-1] - signals.bus_time[-1]))

        bus_edge_time, bus_edge_value = extract_bus_edges(signals.bus_time, signals.bus_value)
        ethernet_edge_time, ethernet_edge_value = extract_ethernet_edges(
            signals.ethernet_time,
            signals.ethernet_raw_value,
        )
        bus_edge_count += len(bus_edge_time)
        ethernet_edge_count += len(ethernet_edge_time)

        before_match_count = len(matches)
        for bus_t, eth_t, state, lag_s in match_edges(
            bus_edge_time,
            bus_edge_value,
            ethernet_edge_time,
            ethernet_edge_value,
            edge_match_window_s,
        ):
            matches.append(
                EdgeMatch(
                    file_path=h5_file,
                    file_time=signals.file_time,
                    bus_time=bus_t,
                    ethernet_time=eth_t,
                    state=state,
                    lag_s=lag_s,
                )
            )

        total, mismatch = compare_on_bus_timeline(
            signals.bus_time,
            signals.bus_value,
            signals.ethernet_time,
            signals.ethernet_raw_value,
        )
        comparable_points += total
        mismatch_points += mismatch
        file_match_count = len(matches) - before_match_count
        log(
            "  完成："
            f"{BUS_LABEL}边沿 {len(bus_edge_time)}，"
            f"{ETHERNET_LABEL}边沿 {len(ethernet_edge_time)}，"
            f"匹配边沿 {file_match_count}，"
            f"采样点不一致 {mismatch}/{total}，"
            f"用时 {time.perf_counter() - file_start_time:.2f}秒"
        )

    if not file_signals:
        raise RuntimeError("所有 h5 都缺少检查所需的两路标志位，无法生成 PNG。")

    log(
        "统计完成："
        f"有效文件 {len(file_signals)}，跳过文件 {skipped_files}，"
        f"匹配边沿 {len(matches)}，采样点不一致比例 "
        f"{safe_percent(mismatch_points, comparable_points):.2f}%"
    )

    if matches:
        near_example = min(matches, key=lambda item: abs(item.lag_s))
        delayed_candidates = [item for item in matches if item.lag_s >= 0]
        delayed_example = max(delayed_candidates or matches, key=lambda item: abs(item.lag_s))
    else:
        near_example = None
        delayed_example = None

    return SyncStats(
        input_dir=input_dir,
        output_path=output_path,
        total_files=len(h5_files),
        used_files=len(file_signals),
        skipped_files=skipped_files,
        bus_edge_count=bus_edge_count,
        ethernet_edge_count=ethernet_edge_count,
        comparable_points=comparable_points,
        mismatch_points=mismatch_points,
        start_diffs=start_diffs,
        end_diffs=end_diffs,
        matches=matches,
        near_example=near_example,
        delayed_example=delayed_example,
        file_signals=file_signals,
    )


def collect_h5_files(input_dir: Path) -> List[Path]:
    files = list(input_dir.rglob("*.h5")) + list(input_dir.rglob("*.hdf5"))
    return sorted(files, key=lambda path: (parse_file_time(path.name) or dt.datetime.min, str(path)))


def read_file_signals(h5_file: Path) -> Optional[FileSignals]:
    with h5py.File(h5_file, "r") as h5:
        bus_path = find_signal_path(
            h5,
            BUS_SIGNAL_CANDIDATES,
            signal_name="ILCCC_Act",
            required_fragments=("0x194", "ACC_AxlTrqReq"),
            preferred_value_sets=({0, 1},),
        )
        ethernet_path = find_signal_path(
            h5,
            ETHERNET_SIGNAL_CANDIDATES,
            signal_name="Debug_uint_Reserved_11",
            required_fragments=("0x3D9", "Reserved_10"),
            preferred_value_sets=({0, 1}, {4, 5}),
        )
        if bus_path is None or ethernet_path is None:
            return None

        bus_time_path = timestamp_path_for_signal(bus_path)
        ethernet_time_path = timestamp_path_for_signal(ethernet_path)
        if bus_time_path not in h5 or ethernet_time_path not in h5:
            return None

        bus_time, bus_value = sort_by_time(h5[bus_time_path][:], h5[bus_path][:])
        ethernet_time, ethernet_raw_value = sort_by_time(h5[ethernet_time_path][:], h5[ethernet_path][:])
        if len(bus_time) == 0 or len(ethernet_time) == 0:
            return None

    return FileSignals(
        path=h5_file,
        file_time=parse_file_time(h5_file.name),
        bus_time=bus_time,
        bus_value=bus_value,
        ethernet_time=ethernet_time,
        ethernet_raw_value=ethernet_raw_value,
    )


def find_signal_path(
    h5: h5py.File,
    candidates: Sequence[str],
    signal_name: str,
    required_fragments: Sequence[str],
    preferred_value_sets: Sequence[Sequence[int]] = (),
) -> Optional[str]:
    matched_candidates: List[str] = []
    for candidate in candidates:
        if candidate in h5:
            matched_candidates.append(candidate)
    if matched_candidates:
        return choose_preferred_signal_path(h5, matched_candidates, preferred_value_sets)

    matches: List[str] = []

    def visitor(name: str, obj: h5py.Dataset) -> None:
        if not isinstance(obj, h5py.Dataset):
            return
        normalized = name.replace("\\", "/")
        if not normalized.endswith("/signals/" + signal_name):
            return
        if all(fragment in normalized for fragment in required_fragments):
            matches.append(normalized)

    h5.visititems(visitor)
    if not matches:
        return None

    def score(path: str) -> Tuple[int, str]:
        for index, candidate in enumerate(candidates):
            if path == candidate:
                return index, path
        if path.startswith("ch3/"):
            return len(candidates), path
        if path.startswith("ch4/"):
            return len(candidates) + 1, path
        return len(candidates) + 2, path

    ordered = sorted(matches, key=score)
    return choose_preferred_signal_path(h5, ordered, preferred_value_sets)


def choose_preferred_signal_path(
    h5: h5py.File,
    ordered_paths: Sequence[str],
    preferred_value_sets: Sequence[Sequence[int]],
) -> str:
    if not preferred_value_sets:
        return ordered_paths[0]

    preferred_scores = [
        (signal_value_score(h5[path], preferred_value_sets), index, path)
        for index, path in enumerate(ordered_paths)
    ]
    return sorted(preferred_scores)[0][2]


def signal_value_score(dataset: h5py.Dataset, preferred_value_sets: Sequence[Sequence[int]]) -> int:
    values = np.asarray(dataset[:])
    if values.size == 0:
        return len(preferred_value_sets) + 2
    if values.size > 50000:
        values = values[:50000]
    unique_values = {int(value) for value in np.unique(values)}
    for index, preferred_values in enumerate(preferred_value_sets):
        preferred_set = set(preferred_values)
        if unique_values and unique_values.issubset(preferred_set):
            return index
    return len(preferred_value_sets) + 1


def timestamp_path_for_signal(signal_path: str) -> str:
    prefix, _separator, _signal_name = signal_path.partition("/signals/")
    return prefix + "/timestamp"


def sort_by_time(time_values: np.ndarray, signal_values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    time_array = np.asarray(time_values, dtype=float)
    value_array = np.asarray(signal_values)
    finite = np.isfinite(time_array)
    time_array = time_array[finite]
    value_array = value_array[finite]
    order = np.argsort(time_array, kind="mergesort")
    return time_array[order], value_array[order]


def parse_file_time(file_name: str) -> Optional[dt.datetime]:
    patterns = [
        r"(20\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})",
        r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})[_-](\d{2})[_-](\d{2})",
        r"(20\d{2})-(\d{2})-(\d{2})[_-](\d{2})[_-](\d{2})[_-](\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, file_name)
        if match:
            values = [int(value) for value in match.groups()]
            try:
                return dt.datetime(*values)
            except ValueError:
                return None
    return None


def extract_bus_edges(time_values: np.ndarray, signal_values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    values = np.asarray(signal_values).astype(int)
    mask = np.isin(values, [0, 1])
    time_values = np.asarray(time_values)[mask]
    values = values[mask]
    if len(values) < 2:
        return np.array([], dtype=float), np.array([], dtype=int)
    edge_index = np.flatnonzero(np.diff(values) != 0) + 1
    return time_values[edge_index], values[edge_index]


def map_ethernet_state(raw_values: np.ndarray) -> np.ndarray:
    raw_values = np.asarray(raw_values).astype(int)
    mapped = np.full(raw_values.shape, -1, dtype=int)
    mapped[raw_values == 0] = 0
    mapped[raw_values == 1] = 1
    mapped[raw_values == 4] = 1
    mapped[raw_values == 5] = 0
    return mapped


def extract_ethernet_edges(time_values: np.ndarray, raw_values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    mapped = map_ethernet_state(raw_values)
    valid = mapped >= 0
    time_values = np.asarray(time_values)[valid]
    mapped = mapped[valid]
    if len(mapped) < 2:
        return np.array([], dtype=float), np.array([], dtype=int)
    edge_index = np.flatnonzero(np.diff(mapped) != 0) + 1
    return time_values[edge_index], mapped[edge_index]


def match_edges(
    bus_time: np.ndarray,
    bus_value: np.ndarray,
    ethernet_time: np.ndarray,
    ethernet_value: np.ndarray,
    max_abs_lag_s: float,
) -> Iterable[Tuple[float, float, int, float]]:
    used = np.zeros(len(ethernet_time), dtype=bool)
    for bus_t, bus_state in zip(bus_time, bus_value):
        candidates = np.where(
            (ethernet_value == bus_state)
            & (~used)
            & (np.abs(ethernet_time - bus_t) <= max_abs_lag_s)
        )[0]
        if len(candidates) == 0:
            continue
        chosen = candidates[np.argmin(np.abs(ethernet_time[candidates] - bus_t))]
        used[chosen] = True
        eth_t = float(ethernet_time[chosen])
        bus_t = float(bus_t)
        yield bus_t, eth_t, int(bus_state), eth_t - bus_t


def compare_on_bus_timeline(
    bus_time: np.ndarray,
    bus_value: np.ndarray,
    ethernet_time: np.ndarray,
    ethernet_raw_value: np.ndarray,
) -> Tuple[int, int]:
    mapped = map_ethernet_state(ethernet_raw_value)
    nearest_previous = np.searchsorted(ethernet_time, bus_time, side="right") - 1
    valid = (nearest_previous >= 0) & (mapped[nearest_previous] >= 0) & np.isin(bus_value.astype(int), [0, 1])
    if not np.any(valid):
        return 0, 0
    compared = int(valid.sum())
    mismatch = int((mapped[nearest_previous[valid]] != bus_value[valid].astype(int)).sum())
    return compared, mismatch


def draw_png(stats: SyncStats) -> None:
    output_path = stats.output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width, height = 2400, 1500
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    fonts = FontSet()
    palette = Palette()

    draw_header(draw, stats, fonts, palette)
    draw_lag_scatter(draw, stats, fonts, palette, 80, 260, 1320, 470)
    draw_lag_histogram(draw, stats, fonts, palette, 1530, 260, 770, 470)
    draw_step_example(
        draw,
        stats,
        stats.near_example,
        fonts,
        palette,
        80,
        850,
        1050,
        460,
        "接近同步示例",
    )
    draw_step_example(
        draw,
        stats,
        stats.delayed_example,
        fonts,
        palette,
        1250,
        850,
        1050,
        460,
        "明显延迟示例",
    )
    draw.text(
        (80, 1438),
        f"图中延迟={ETHERNET_LABEL}切换时刻 - {BUS_LABEL}切换时刻。",
        fill=palette.muted,
        font=fonts.small,
    )
    image.save(output_path, quality=95)


class Palette:
    text = (30, 41, 59)
    muted = (91, 103, 121)
    grid = (218, 224, 231)
    light_grid = (236, 239, 243)
    axis = (128, 139, 153)
    frame = (155, 164, 176)
    bus = (41, 121, 198)
    ethernet = (234, 126, 38)
    dot = (114, 165, 213)
    green = (49, 151, 89)
    red = (225, 65, 69)


class FontSet:
    def __init__(self) -> None:
        regular, bold = find_chinese_fonts()
        self.title = ImageFont.truetype(str(bold), 40)
        self.subtitle = ImageFont.truetype(str(regular), 23)
        self.header = ImageFont.truetype(str(bold), 27)
        self.axis = ImageFont.truetype(str(regular), 20)
        self.small = ImageFont.truetype(str(regular), 18)
        self.tiny = ImageFont.truetype(str(regular), 16)


def find_chinese_fonts() -> Tuple[Path, Path]:
    candidates = [
        (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\msyhbd.ttc")),
        (Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\simhei.ttf")),
        (Path(r"C:\Windows\Fonts\simsun.ttc"), Path(r"C:\Windows\Fonts\simsun.ttc")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            return regular, bold
    raise FileNotFoundError("未找到可渲染中文的字体，请安装微软雅黑、黑体或宋体。")


def draw_header(draw: ImageDraw.ImageDraw, stats: SyncStats, fonts: FontSet, palette: Palette) -> None:
    lags = np.array([match.lag_s for match in stats.matches], dtype=float)
    start_diffs = np.array(stats.start_diffs, dtype=float)
    mismatch_rate = safe_percent(stats.mismatch_points, stats.comparable_points)

    draw.text(
        (72, 44),
        f"{BUS_LABEL} 与 {ETHERNET_LABEL} 同步性检查",
        fill=palette.text,
        font=fonts.title,
    )
    draw.text(
        (72, 98),
        f"{ETHERNET_LABEL} 与 {BUS_LABEL} 均按 1=激活、0=退出展示；旧编码 4/5 会自动转换",
        fill=palette.muted,
        font=fonts.subtitle,
    )
    if len(lags):
        edge_text = (
            f"可比较边沿：{len(stats.matches)}/{stats.bus_edge_count}；"
            f"中位延迟：{np.percentile(lags, 50):.2f}秒；"
            f"95分位延迟：{np.percentile(lags, 95):.2f}秒；"
            f"1秒内同步：{int(np.sum(np.abs(lags) <= 1.0))}/{len(lags)}；"
        )
    else:
        edge_text = f"可比较边沿：0/{stats.bus_edge_count}；未发现激活/退出切换边沿；"
    stats_text = (
        f"文件数：{stats.total_files}；有效文件：{stats.used_files}；"
        f"{edge_text}"
        f"采样点不一致比例：{mismatch_rate:.2f}%"
    )
    draw.text((72, 135), stats_text, fill=palette.text, font=fonts.subtitle)

    if len(start_diffs):
        start_text = (
            "时间轴起点差异（以太网减总线）："
            f"中位{np.percentile(start_diffs, 50) * 1000:.1f}毫秒，"
            f"95分位{np.percentile(start_diffs, 95) * 1000:.1f}毫秒。"
        )
        if len(lags):
            start_text += "说明问题主要是标志位切换延迟，不是整体时间轴起点偏移。"
        else:
            start_text += "本批未发现激活/退出切换边沿，主要看采样点一致性和时间轴差异。"
        draw.text((72, 171), start_text, fill=palette.muted, font=fonts.subtitle)


def draw_lag_scatter(
    draw: ImageDraw.ImageDraw,
    stats: SyncStats,
    fonts: FontSet,
    palette: Palette,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    matches = [item for item in stats.matches if item.absolute_time is not None]
    if not matches:
        draw_panel(draw, x, y, width, height, "边沿延迟随时间变化", fonts, palette)
        draw.text(
            (x + width // 2, y + height // 2),
            "未发现激活/退出切换边沿",
            fill=palette.muted,
            font=fonts.header,
            anchor="mm",
        )
        draw.text(
            (x + width // 2, y + height // 2 + 44),
            "两路标志位在本批文件中没有可计算延迟的状态切换",
            fill=palette.muted,
            font=fonts.axis,
            anchor="mm",
        )
        return
    times = [item.absolute_time for item in matches]
    lags = np.array([item.lag_s for item in matches], dtype=float)
    x_min, x_max = min(times), max(times)
    y_min, y_max = -9.5, 9.8

    draw_panel(draw, x, y, width, height, "边沿延迟随时间变化", fonts, palette)

    def sx(value: dt.datetime) -> int:
        span_s = max((x_max - x_min).total_seconds(), 1.0)
        return x + int((value - x_min).total_seconds() / span_s * width)

    def sy(value: float) -> int:
        return y + height - int((value - y_min) / (y_max - y_min) * height)

    for tick in nice_ticks(y_min, y_max, 7):
        py = sy(tick)
        draw.line((x, py, x + width, py), fill=palette.grid, width=1)
        draw.text((x - 10, py), f"{tick:.0f}", fill=palette.muted, font=fonts.small, anchor="rm")
    for index in range(6):
        tick_time = x_min + (x_max - x_min) * (index / 5)
        px = sx(tick_time)
        draw.line((px, y, px, y + height), fill=palette.light_grid, width=1)
        draw.text((px, y + height + 13), tick_time.strftime("%H:%M"), fill=palette.muted, font=fonts.small, anchor="mt")

    draw.text((x, y - 10), "以太网相对总线延迟（秒）", fill=palette.muted, font=fonts.small)
    draw.text((x + width // 2, y + height + 54), "文件时间", fill=palette.muted, font=fonts.axis, anchor="mt")

    p50 = float(np.percentile(lags, 50))
    p95 = float(np.percentile(lags, 95))
    for value, color, label in [
        (0.0, palette.axis, "0秒"),
        (1.0, palette.green, "1秒"),
        (p50, palette.ethernet, f"中位{p50:.2f}秒"),
        (p95, palette.red, f"95分位{p95:.2f}秒"),
    ]:
        py = sy(value)
        draw.line((x, py, x + width, py), fill=color, width=2)
        draw.text((x + width - 8, py - 4), label, fill=color, font=fonts.tiny, anchor="rs")

    for event_time, lag in zip(times, lags):
        px, py = sx(event_time), sy(float(lag))
        draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=palette.dot)

    ordered = np.argsort([item.timestamp() for item in times])
    rolling_points: List[Tuple[int, int]] = []
    window = 19
    ordered_times = [times[index] for index in ordered]
    ordered_lags = lags[ordered]
    for index, event_time in enumerate(ordered_times):
        left = max(0, index - window // 2)
        right = min(len(ordered_lags), index + window // 2 + 1)
        rolling_points.append((sx(event_time), sy(float(np.median(ordered_lags[left:right])))))
    if len(rolling_points) >= 2:
        draw.line(rolling_points, fill=palette.ethernet, width=3, joint="curve")

    legend_y = y + 22
    draw.ellipse((x + 25, legend_y - 6, x + 37, legend_y + 6), fill=palette.dot)
    draw.text((x + 48, legend_y), "单次边沿", fill=palette.muted, font=fonts.small, anchor="lm")
    draw.line((x + 220, legend_y, x + 280, legend_y), fill=palette.ethernet, width=4)
    draw.text((x + 292, legend_y), "滚动中位", fill=palette.muted, font=fonts.small, anchor="lm")


def draw_lag_histogram(
    draw: ImageDraw.ImageDraw,
    stats: SyncStats,
    fonts: FontSet,
    palette: Palette,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    lags = np.array([item.lag_s for item in stats.matches], dtype=float)
    if len(lags) == 0:
        draw_panel(draw, x, y, width, height, "延迟分布", fonts, palette)
        draw.text(
            (x + width // 2, y + height // 2),
            "无边沿延迟分布",
            fill=palette.muted,
            font=fonts.header,
            anchor="mm",
        )
        draw.text(
            (x + width // 2, y + height // 2 + 44),
            "总线和以太网激活标志位均未切换",
            fill=palette.muted,
            font=fonts.axis,
            anchor="mm",
        )
        return
    hist, bins = np.histogram(lags, bins=30, range=(-9.5, 9.5))
    max_count = max(int(hist.max()), 1)
    p50 = float(np.percentile(lags, 50))
    p95 = float(np.percentile(lags, 95))

    draw_panel(draw, x, y, width, height, "延迟分布", fonts, palette)

    def sx(value: float) -> int:
        return x + int((value + 9.5) / 19.0 * width)

    def sy_count(value: float) -> int:
        return y + height - int(value / max_count * height)

    for tick in nice_ticks(0, max_count, 6):
        py = sy_count(tick)
        draw.line((x, py, x + width, py), fill=palette.grid, width=1)
        draw.text((x - 10, py), f"{int(tick)}", fill=palette.muted, font=fonts.small, anchor="rm")

    for index, count in enumerate(hist):
        left = sx(float(bins[index]))
        right = sx(float(bins[index + 1])) - 1
        top = sy_count(float(count))
        draw.rectangle((left, top, right, y + height), fill=(116, 161, 205), outline=(255, 255, 255))

    for tick in [-9, -5, -1, 2, 6, 10]:
        draw.text((sx(tick), y + height + 13), f"{tick:.0f}", fill=palette.muted, font=fonts.small, anchor="mt")
    draw.text((x, y - 10), "边沿数量", fill=palette.muted, font=fonts.small)
    draw.text((x + width // 2, y + height + 54), "以太网相对总线延迟（秒）", fill=palette.muted, font=fonts.axis, anchor="mt")

    for value, color, label, label_y in [
        (0.0, palette.axis, "0秒", y + 94),
        (1.0, palette.green, "1秒", y + 124),
        (p50, palette.ethernet, f"中位{p50:.2f}秒", y + 94),
        (p95, palette.red, f"95分位{p95:.2f}秒", y + 124),
    ]:
        px = sx(value)
        draw.line((px, y, px, y + height), fill=color, width=3)
        draw.text((px + 8, label_y), label, fill=color, font=fonts.tiny)


def draw_step_example(
    draw: ImageDraw.ImageDraw,
    stats: SyncStats,
    example: Optional[EdgeMatch],
    fonts: FontSet,
    palette: Palette,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
) -> None:
    if example is None:
        draw_panel(draw, x, y, width, height, title, fonts, palette)
        draw.text(
            (x + width // 2, y + height // 2 - 24),
            "本批没有激活/退出切换",
            fill=palette.muted,
            font=fonts.header,
            anchor="mm",
        )
        draw.text(
            (x + width // 2, y + height // 2 + 24),
            f"可比较采样点不一致比例：{safe_percent(stats.mismatch_points, stats.comparable_points):.2f}%",
            fill=palette.muted,
            font=fonts.axis,
            anchor="mm",
        )
        return
    signals = stats.file_signals[example.file_path]
    time_label = format_example_time(example)
    draw_panel(draw, x, y, width, height, f"{title}（{time_label}）", fonts, palette)

    window_start = max(0.0, example.bus_time - 4.0)
    window_end = min(
        max(float(signals.bus_time[-1]), float(signals.ethernet_time[-1])),
        example.ethernet_time + 4.0,
    )
    if window_end <= window_start:
        window_end = window_start + 1.0

    def sx(value: float) -> int:
        return x + int((value - window_start) / (window_end - window_start) * width)

    def sy(value: float) -> int:
        return y + height - int((value + 0.18) / 1.36 * height)

    for value, label in [(0.0, "退出"), (1.0, "激活")]:
        py = sy(value)
        draw.line((x, py, x + width, py), fill=palette.grid, width=1)
        draw.text((x - 10, py), label, fill=palette.muted, font=fonts.small, anchor="rm")

    for index in range(6):
        tick = window_start + (window_end - window_start) * index / 5
        px = sx(tick)
        draw.line((px, y, px, y + height), fill=palette.light_grid, width=1)
        draw.text((px, y + height + 13), f"{tick - window_start:.1f}", fill=palette.muted, font=fonts.small, anchor="mt")

    draw.text((x, y - 10), "状态", fill=palette.muted, font=fonts.small)
    draw.text((x + width // 2, y + height + 54), "窗口内时间（秒）", fill=palette.muted, font=fonts.axis, anchor="mt")

    bus_points = build_step_points(signals.bus_time, signals.bus_value, window_start, window_end, sy)
    ethernet_time, ethernet_state = mapped_ethernet_series(signals.ethernet_time, signals.ethernet_raw_value)
    ethernet_points = build_step_points(ethernet_time, ethernet_state, window_start, window_end, sy)
    bus_points = [(sx(value), point_y) for value, point_y in bus_points]
    ethernet_points = [(sx(value), point_y) for value, point_y in ethernet_points]
    if len(bus_points) >= 2:
        draw.line(bus_points, fill=palette.bus, width=4)
    if len(ethernet_points) >= 2:
        draw.line(ethernet_points, fill=palette.ethernet, width=4)

    bus_edge_x = sx(example.bus_time)
    ethernet_edge_x = sx(example.ethernet_time)
    draw.line((bus_edge_x, y, bus_edge_x, y + height), fill=palette.bus, width=3)
    draw.line((ethernet_edge_x, y, ethernet_edge_x, y + height), fill=palette.ethernet, width=3)

    legend_y = y + 24
    draw.line((x + 25, legend_y, x + 90, legend_y), fill=palette.bus, width=4)
    draw.text((x + 100, legend_y), BUS_LABEL, fill=palette.muted, font=fonts.small, anchor="lm")
    draw.line((x + 365, legend_y, x + 430, legend_y), fill=palette.ethernet, width=4)
    draw.text((x + 440, legend_y), ETHERNET_LABEL, fill=palette.muted, font=fonts.small, anchor="lm")

    draw_label_with_background(
        draw,
        (bus_edge_x + ethernet_edge_x) // 2,
        y + 70,
        f"延迟{example.lag_s:.3f}秒",
        fonts.small,
        palette.text,
    )


def draw_panel(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    fonts: FontSet,
    palette: Palette,
) -> None:
    draw.text((x, y - 42), title, fill=palette.text, font=fonts.header)
    draw.rectangle((x, y, x + width, y + height), outline=palette.frame, width=2)


def draw_label_with_background(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int],
) -> None:
    bbox = draw.textbbox((x, y), label, font=font, anchor="mm")
    padding_x = 6
    padding_y = 3
    draw.rectangle(
        (
            bbox[0] - padding_x,
            bbox[1] - padding_y,
            bbox[2] + padding_x,
            bbox[3] + padding_y,
        ),
        fill="white",
    )
    draw.text((x, y), label, fill=fill, font=font, anchor="mm")


def mapped_ethernet_series(time_values: np.ndarray, raw_values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    mapped = map_ethernet_state(raw_values)
    valid = mapped >= 0
    return np.asarray(time_values)[valid], mapped[valid]


def build_step_points(
    time_values: np.ndarray,
    state_values: np.ndarray,
    window_start: float,
    window_end: float,
    y_mapper,
) -> List[Tuple[float, int]]:
    time_values = np.asarray(time_values, dtype=float)
    state_values = np.asarray(state_values, dtype=float)
    if len(time_values) == 0:
        return []

    in_window = np.where((time_values >= window_start) & (time_values <= window_end))[0]
    before = np.where(time_values < window_start)[0]
    after = np.where(time_values > window_end)[0]
    selected = list(in_window)
    if len(before):
        selected.insert(0, before[-1])
    if len(after):
        selected.append(after[0])
    selected = sorted(set(selected))

    points: List[Tuple[float, int]] = []
    for index, source_index in enumerate(selected):
        x_value = max(window_start, min(window_end, float(time_values[source_index])))
        y_value = y_mapper(float(state_values[source_index]))
        if index == 0:
            points.append((x_value, y_value))
        else:
            previous_x, previous_y = points[-1]
            _ = previous_x
            points.append((x_value, previous_y))
            points.append((x_value, y_value))
    return points


def format_example_time(example: EdgeMatch) -> str:
    absolute = example.absolute_time
    if absolute is not None:
        return absolute.strftime("%m/%d %H:%M")
    parsed = parse_file_time(example.file_path.name)
    if parsed is not None:
        return parsed.strftime("%m/%d %H:%M")
    return example.file_path.stem[:16]


def nice_ticks(value_min: float, value_max: float, count: int = 6) -> List[float]:
    if value_min == value_max:
        return [value_min]
    span = value_max - value_min
    raw_step = span / max(1, count - 1)
    magnitude = 10 ** math.floor(math.log10(abs(raw_step)))
    step = magnitude
    for multiplier in [1, 2, 2.5, 5, 10]:
        step = multiplier * magnitude
        if span / step <= count:
            break
    current = math.floor(value_min / step) * step
    ticks: List[float] = []
    while current <= value_max + step * 0.5:
        if current >= value_min - step * 0.5:
            ticks.append(current)
        current += step
    return ticks


def safe_percent(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return part / total * 100.0


def print_summary(stats: SyncStats) -> None:
    lags = np.array([match.lag_s for match in stats.matches], dtype=float)
    print(f"已生成：{stats.output_path}")
    print(f"文件数：{stats.total_files}，有效文件：{stats.used_files}，跳过文件：{stats.skipped_files}")
    print(f"边沿数量：{BUS_LABEL} {stats.bus_edge_count}，{ETHERNET_LABEL} {stats.ethernet_edge_count}")
    if len(lags):
        print(
            f"可比较边沿：{len(stats.matches)}，"
            f"中位延迟：{np.percentile(lags, 50):.3f}秒，"
            f"95分位延迟：{np.percentile(lags, 95):.3f}秒，"
            f"1秒内同步：{int(np.sum(np.abs(lags) <= 1.0))}/{len(lags)}"
        )
    else:
        print("可比较边沿：0，本批未发现激活/退出切换边沿")
    print(f"采样点不一致比例：{safe_percent(stats.mismatch_points, stats.comparable_points):.2f}%")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
