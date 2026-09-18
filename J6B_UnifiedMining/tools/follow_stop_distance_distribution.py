from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DataMining import file_chronological_sort_key, h5_files_are_continuous, load_config  # noqa: E402
from metric_utils import (  # noqa: E402
    SCENE_REQUIRED_FIELDS,
    Segment,
    filter_segments_for_primary,
    follow_stop_segments,
)
from signal_preprocessor import (  # noqa: E402
    SignalFrame,
    build_signal_frame,
    raw_dependencies_for_field,
    stitch_signal_frames,
)


DEFAULT_DATA_ROOT = Path(r"E:\数据挖掘\DataMiningData\J6B_Data")
DEFAULT_VEHICLES = ("ES27PV011", "TU27PV007")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "follow_stop_distance_distribution"


@dataclass
class FollowStopSample:
    vehicle_id: str
    model: str
    data_folder: str
    h5_file: str
    end_h5_file: str
    start_time_s: float
    end_time_s: float
    duration_s: float
    end_distance_m: float
    min_distance_m: float
    start_ego_speed_kph: float
    end_ego_speed_kph: float
    end_front_speed_kph: float
    cross_h5: bool


@dataclass
class VehicleSummary:
    vehicle_id: str
    model: str
    data_folder_count: int
    h5_file_count: int
    follow_stop_count: int
    distance_min_m: Optional[float]
    distance_p10_m: Optional[float]
    distance_median_m: Optional[float]
    distance_mean_m: Optional[float]
    distance_p90_m: Optional[float]
    distance_max_m: Optional[float]
    in_2_to_5m_count: int
    in_2_to_5m_ratio: Optional[float]
    skipped_file_count: int


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计指定车辆跟车刹停后距离前车的距离，并生成分布图。"
    )
    parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="J6B_Data 根目录。",
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.json"),
        help="用于解析 H5 信号的 config.json。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="输出 PNG/CSV/JSON 的目录。",
    )
    parser.add_argument(
        "--vehicles",
        nargs="+",
        default=list(DEFAULT_VEHICLES),
        help="要统计的车辆编号。",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    data_root = Path(args.data_root)
    config_path = Path(args.config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)
    config = reduced_config_for_fields(config, SCENE_REQUIRED_FIELDS["follow_stop"])
    max_gap_s = float(config.get("defaults", {}).get("h5_stitch_max_gap_s", 5.0))
    stitched_fields = tuple(SCENE_REQUIRED_FIELDS["follow_stop"])

    all_summaries: List[VehicleSummary] = []
    all_samples: List[FollowStopSample] = []
    errors: Dict[str, List[Dict[str, str]]] = {}

    for vehicle_id in args.vehicles:
        vehicle_dirs = find_vehicle_dirs(data_root, vehicle_id)
        h5_files = collect_vehicle_h5_files(vehicle_dirs)
        print(
            "[%s] data folders=%d h5 files=%d"
            % (vehicle_id, len(vehicle_dirs), len(h5_files)),
            flush=True,
        )

        samples, skipped = collect_follow_stop_samples(
            vehicle_id=vehicle_id,
            h5_files=h5_files,
            config=config,
            stitch_max_gap_s=max_gap_s,
            stitched_field_names=stitched_fields,
        )
        all_samples.extend(samples)
        errors[vehicle_id] = skipped

        model = infer_vehicle_model(vehicle_dirs[0]) if vehicle_dirs else ""
        summary = summarize_vehicle(
            vehicle_id=vehicle_id,
            model=model,
            data_folder_count=len(vehicle_dirs),
            h5_file_count=len(h5_files),
            samples=samples,
            skipped_file_count=len(skipped),
        )
        all_summaries.append(summary)
        plot_path = output_dir / ("%s_follow_stop_distance_distribution.png" % vehicle_id)
        draw_distribution_plot(
            vehicle_id=vehicle_id,
            model=model,
            folder_count=len(vehicle_dirs),
            h5_count=len(h5_files),
            summary=summary,
            samples=samples,
            output_path=plot_path,
        )
        print("[%s] follow stops=%d plot=%s" % (vehicle_id, len(samples), plot_path), flush=True)

    samples_csv = output_dir / "follow_stop_distance_samples.csv"
    write_samples_csv(samples_csv, all_samples)
    summary_json = output_dir / "follow_stop_distance_summary.json"
    write_summary_json(summary_json, all_summaries, errors)
    print("samples csv=%s" % samples_csv, flush=True)
    print("summary json=%s" % summary_json, flush=True)
    return 0


def reduced_config_for_fields(config: Mapping[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    needed = {"acceleration", "acceleration_valid"}
    for field_name in fields:
        if field_name in {"time_s", "time_ms"}:
            continue
        needed.update(raw_dependencies_for_field(field_name))

    reduced = dict(config)
    signals = dict(config.get("signals", {}))
    optional_signals = dict(config.get("optional_signals", {}))
    reduced["signals"] = {
        field_name: value
        for field_name, value in signals.items()
        if field_name in needed
    }
    reduced["optional_signals"] = {
        field_name: value
        for field_name, value in optional_signals.items()
        if field_name in needed
    }
    if "acceleration" not in reduced["signals"]:
        raise KeyError("config.json must define acceleration under signals for the time axis")
    return reduced


def find_vehicle_dirs(data_root: Path, vehicle_id: str) -> List[Path]:
    if not data_root.is_dir():
        raise FileNotFoundError("data root not found: %s" % data_root)
    direct = [
        item
        for item in data_root.iterdir()
        if item.is_dir() and vehicle_id.lower() in item.name.lower()
    ]
    direct_with_h5 = [
        folder
        for folder in direct
        if any(folder.glob("*.h5")) or any(folder.glob("*.hdf5"))
    ]
    if direct_with_h5:
        return sorted(direct_with_h5, key=lambda path: path.name)

    # Some data drops add a vehicle directory above one directory per recording.
    # Return the recording directories so collect_vehicle_h5_files can find them.
    if direct:
        nested = [
            path.parent
            for path in data_root.rglob("*.h5")
            if vehicle_id.lower() in str(path).lower()
        ]
        nested.extend(
            path.parent
            for path in data_root.rglob("*.hdf5")
            if vehicle_id.lower() in str(path).lower()
        )
        if nested:
            return sorted(set(nested), key=lambda path: str(path))

    if direct:
        return sorted(direct, key=lambda path: path.name)

    parents = {
        path.parent
        for path in data_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".h5", ".hdf5"}
        if vehicle_id.lower() in str(path).lower()
    }
    return sorted(parents, key=lambda path: str(path))


def collect_vehicle_h5_files(vehicle_dirs: Iterable[Path]) -> List[str]:
    files: List[str] = []
    for folder in vehicle_dirs:
        files.extend(str(path.resolve()) for path in folder.glob("*.h5"))
        files.extend(str(path.resolve()) for path in folder.glob("*.hdf5"))
    return sorted(set(files), key=file_chronological_sort_key)


def collect_follow_stop_samples(
    vehicle_id: str,
    h5_files: Sequence[str],
    config: Mapping[str, Any],
    stitch_max_gap_s: float,
    stitched_field_names: Iterable[str],
) -> Tuple[List[FollowStopSample], List[Dict[str, str]]]:
    samples: List[FollowStopSample] = []
    skipped: List[Dict[str, str]] = []
    frame_cache: Dict[int, SignalFrame] = {}
    continuous_next_flags = [False] * len(h5_files)

    def get_frame(index: int) -> SignalFrame:
        if index not in frame_cache:
            frame_cache[index] = build_signal_frame(h5_files[index], config)
        return frame_cache[index]

    for index, h5_file in enumerate(h5_files):
        try:
            if index == 0 or (index + 1) % 25 == 0 or index + 1 == len(h5_files):
                print(
                    "[%s] preprocessing %d/%d %s"
                    % (vehicle_id, index + 1, len(h5_files), Path(h5_file).name),
                    flush=True,
                )
            frame = get_frame(index)
            has_continuous_previous = index > 0 and continuous_next_flags[index - 1]
            frame.has_continuous_previous = has_continuous_previous

            metric_frame = frame
            if index + 1 < len(h5_files):
                is_continuous, next_offset_s, _boundary_gap_s = h5_files_are_continuous(
                    h5_files[index],
                    h5_files[index + 1],
                    frame,
                    stitch_max_gap_s,
                )
                continuous_next_flags[index] = is_continuous
                if is_continuous:
                    metric_frame = stitch_signal_frames(
                        frame,
                        get_frame(index + 1),
                        next_offset_s,
                        has_continuous_previous=has_continuous_previous,
                        field_names=stitched_field_names,
                    )

            segments = filter_segments_for_primary(
                metric_frame,
                follow_stop_segments(metric_frame),
            )
            for seg in segments:
                sample = build_sample(vehicle_id, metric_frame, seg)
                if sample is not None:
                    samples.append(sample)
        except Exception as exc:
            skipped.append({"h5_file": h5_file, "error": str(exc)})
            print("[WARN] skipped %s: %s" % (h5_file, exc), flush=True)
    return samples, skipped


def build_sample(
    vehicle_id: str,
    frame: SignalFrame,
    seg: Segment,
) -> Optional[FollowStopSample]:
    front_range = frame.col("front_range", np.inf).astype(float)
    ego_speed_mps = frame.col("ego_speed_mps", 0.0).astype(float)
    front_velocity_mps = frame.col("front_velocity_abs", np.nan).astype(float)

    end_distance = finite_value(front_range[seg.end])
    if end_distance is None:
        return None

    window = front_range[seg.start : seg.end + 1]
    finite_window = window[np.isfinite(window)]
    min_distance = float(np.nanmin(finite_window)) if finite_window.size else end_distance

    primary_length = frame.primary_length if frame.primary_length is not None else frame.length
    cross_h5 = seg.end >= primary_length
    source_paths = tuple(frame.stitched_source_paths) if frame.stitched_source_paths else (frame.source_path,)
    start_file = source_paths[0]
    end_file = source_paths[1] if cross_h5 and len(source_paths) > 1 else source_paths[0]
    data_folder = str(Path(start_file).parent)
    model = infer_vehicle_model(Path(data_folder))

    return FollowStopSample(
        vehicle_id=vehicle_id,
        model=model,
        data_folder=data_folder,
        h5_file=start_file,
        end_h5_file=end_file,
        start_time_s=round(float(frame.time_s[seg.start]), 3),
        end_time_s=round(float(frame.time_s[seg.end]), 3),
        duration_s=round(float(frame.time_s[seg.end] - frame.time_s[seg.start]), 3),
        end_distance_m=round(float(end_distance), 3),
        min_distance_m=round(float(min_distance), 3),
        start_ego_speed_kph=round(float(ego_speed_mps[seg.start]) * 3.6, 3),
        end_ego_speed_kph=round(float(ego_speed_mps[seg.end]) * 3.6, 3),
        end_front_speed_kph=round(float(front_velocity_mps[seg.end]) * 3.6, 3),
        cross_h5=cross_h5,
    )


def finite_value(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def infer_vehicle_model(folder: Path) -> str:
    name = folder.name
    parts = name.split("-")
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return ""


def summarize_vehicle(
    vehicle_id: str,
    model: str,
    data_folder_count: int,
    h5_file_count: int,
    samples: Sequence[FollowStopSample],
    skipped_file_count: int,
) -> VehicleSummary:
    distances = np.asarray([sample.end_distance_m for sample in samples], dtype=float)
    if distances.size == 0:
        return VehicleSummary(
            vehicle_id=vehicle_id,
            model=model,
            data_folder_count=data_folder_count,
            h5_file_count=h5_file_count,
            follow_stop_count=0,
            distance_min_m=None,
            distance_p10_m=None,
            distance_median_m=None,
            distance_mean_m=None,
            distance_p90_m=None,
            distance_max_m=None,
            in_2_to_5m_count=0,
            in_2_to_5m_ratio=None,
            skipped_file_count=skipped_file_count,
        )

    in_band = np.logical_and(distances >= 2.0, distances <= 5.0)
    return VehicleSummary(
        vehicle_id=vehicle_id,
        model=model,
        data_folder_count=data_folder_count,
        h5_file_count=h5_file_count,
        follow_stop_count=int(distances.size),
        distance_min_m=round(float(np.nanmin(distances)), 3),
        distance_p10_m=round(float(np.nanpercentile(distances, 10)), 3),
        distance_median_m=round(float(np.nanmedian(distances)), 3),
        distance_mean_m=round(float(np.nanmean(distances)), 3),
        distance_p90_m=round(float(np.nanpercentile(distances, 90)), 3),
        distance_max_m=round(float(np.nanmax(distances)), 3),
        in_2_to_5m_count=int(np.count_nonzero(in_band)),
        in_2_to_5m_ratio=round(float(np.count_nonzero(in_band) / distances.size), 6),
        skipped_file_count=skipped_file_count,
    )


def write_samples_csv(path: Path, samples: Sequence[FollowStopSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(FollowStopSample.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow(asdict(sample))


def write_summary_json(
    path: Path,
    summaries: Sequence[VehicleSummary],
    errors: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    payload = {
        "distance_definition": "front_range at the first sample where ego is stationary and front vehicle speed <= 0.2 m/s within the current follow_stop scene",
        "scene_definition": "metric_utils.follow_stop_segments from current code; follow_mask=ACC active + front_valid + target_present, own vehicle moves then stops, front vehicle also stopped",
        "standard_band_m": [2.0, 5.0],
        "summaries": [asdict(summary) for summary in summaries],
        "errors": errors,
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def draw_distribution_plot(
    vehicle_id: str,
    model: str,
    folder_count: int,
    h5_count: int,
    summary: VehicleSummary,
    samples: Sequence[FollowStopSample],
    output_path: Path,
) -> None:
    width, height = 1280, 780
    img = Image.new("RGB", (width, height), "#f7f8fb")
    draw = ImageDraw.Draw(img)
    font_title = load_font(30, bold=True)
    font_subtitle = load_font(18)
    font_axis = load_font(15)
    font_small = load_font(13)

    draw.text((48, 32), "%s %s 跟车刹停后距离分布" % (model, vehicle_id), fill="#1f2937", font=font_title)
    subtitle = "距离定义：刹停结束点 front_range；参考带：2.0-5.0 m；数据目录 %d 个，H5 %d 个" % (
        folder_count,
        h5_count,
    )
    draw.text((50, 76), subtitle, fill="#4b5563", font=font_subtitle)

    distances = np.asarray([sample.end_distance_m for sample in samples], dtype=float)
    plot_box = (74, 145, 914, 600)
    stats_box = (954, 145, 1228, 600)
    draw.rounded_rectangle(stats_box, radius=8, fill="#ffffff", outline="#d1d5db")

    if distances.size == 0:
        draw.rectangle(plot_box, fill="#ffffff", outline="#d1d5db")
        text = "未识别到跟车刹停样本"
        bbox = draw.textbbox((0, 0), text, font=font_subtitle)
        draw.text(
            ((plot_box[0] + plot_box[2] - (bbox[2] - bbox[0])) / 2, 340),
            text,
            fill="#6b7280",
            font=font_subtitle,
        )
        draw_stats(draw, stats_box, summary, font_subtitle, font_axis, font_small)
        img.save(output_path)
        return

    upper = choose_axis_upper(float(np.nanmax(distances)))
    bin_width = choose_bin_width(upper)
    bins = np.arange(0.0, upper + bin_width, bin_width)
    counts, edges = np.histogram(distances, bins=bins)

    x0, y0, x1, y1 = plot_box
    draw.rectangle(plot_box, fill="#ffffff", outline="#d1d5db")
    chart_left, chart_top = x0 + 60, y0 + 34
    chart_right, chart_bottom = x1 - 26, y1 - 62
    chart_width = chart_right - chart_left
    chart_height = chart_bottom - chart_top

    def x_for(value: float) -> float:
        return chart_left + chart_width * (value / upper)

    # Draw acceptable-distance band before bars so the bars stay readable.
    band_left = max(chart_left, x_for(2.0))
    band_right = min(chart_right, x_for(5.0))
    if band_right > band_left:
        draw.rectangle((band_left, chart_top, band_right, chart_bottom), fill="#dcfce7")
        draw.text((band_left + 6, chart_top + 7), "2-5 m", fill="#166534", font=font_small)

    max_count = max(int(np.max(counts)), 1)
    y_tick_count = min(max_count, 5)
    for idx in range(y_tick_count + 1):
        value = max_count * idx / y_tick_count if y_tick_count else 0
        y = chart_bottom - chart_height * idx / y_tick_count if y_tick_count else chart_bottom
        draw.line((chart_left, y, chart_right, y), fill="#e5e7eb")
        label = str(int(round(value)))
        draw.text((chart_left - 38, y - 8), label, fill="#6b7280", font=font_small)

    bar_gap = 2
    bar_color = "#2563eb"
    bar_outline = "#1d4ed8"
    for count, left_edge, right_edge in zip(counts, edges[:-1], edges[1:]):
        left = x_for(float(left_edge)) + bar_gap / 2
        right = x_for(float(right_edge)) - bar_gap / 2
        if right <= left:
            right = left + 1
        bar_height = 0 if max_count <= 0 else (count / max_count) * chart_height
        top = chart_bottom - bar_height
        draw.rectangle((left, top, right, chart_bottom), fill=bar_color, outline=bar_outline)

    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill="#374151", width=2)
    draw.line((chart_left, chart_top, chart_left, chart_bottom), fill="#374151", width=2)
    for mark in (2.0, 5.0):
        if mark <= upper:
            x = x_for(mark)
            draw.line((x, chart_top, x, chart_bottom), fill="#16a34a", width=2)
            draw.text((x + 4, chart_bottom + 8), "%.0fm" % mark, fill="#166534", font=font_small)

    tick_step = choose_tick_step(upper)
    tick = 0.0
    while tick <= upper + 1e-9:
        x = x_for(tick)
        draw.line((x, chart_bottom, x, chart_bottom + 6), fill="#374151")
        label = format_tick(tick)
        bbox = draw.textbbox((0, 0), label, font=font_small)
        draw.text((x - (bbox[2] - bbox[0]) / 2, chart_bottom + 20), label, fill="#4b5563", font=font_small)
        tick += tick_step

    draw.text((chart_left + chart_width / 2 - 75, y1 - 31), "刹停结束距离 front_range (m)", fill="#374151", font=font_axis)
    draw.text((x0 + 18, chart_top - 26), "case 数", fill="#374151", font=font_axis)
    draw_stats(draw, stats_box, summary, font_subtitle, font_axis, font_small)

    note = "说明：当前图主统计 end_distance_m；CSV 中同时保留 min_distance_m，可与 follow_stop_min_frntobj_dis 口径对齐查看。"
    draw.text((74, 650), note, fill="#4b5563", font=font_axis)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


def draw_stats(
    draw: ImageDraw.ImageDraw,
    stats_box: Tuple[int, int, int, int],
    summary: VehicleSummary,
    font_title: ImageFont.ImageFont,
    font_axis: ImageFont.ImageFont,
    font_small: ImageFont.ImageFont,
) -> None:
    x0, y0, _x1, _y1 = stats_box
    draw.text((x0 + 20, y0 + 22), "统计摘要", fill="#111827", font=font_title)
    rows = [
        ("跟车刹停数", str(summary.follow_stop_count)),
        ("有效带内(2-5m)", "%d%s" % (
            summary.in_2_to_5m_count,
            "" if summary.in_2_to_5m_ratio is None else " / %.1f%%" % (summary.in_2_to_5m_ratio * 100.0),
        )),
        ("最小值", format_optional(summary.distance_min_m)),
        ("P10", format_optional(summary.distance_p10_m)),
        ("中位数", format_optional(summary.distance_median_m)),
        ("平均值", format_optional(summary.distance_mean_m)),
        ("P90", format_optional(summary.distance_p90_m)),
        ("最大值", format_optional(summary.distance_max_m)),
        ("跳过文件", str(summary.skipped_file_count)),
    ]
    y = y0 + 76
    for label, value in rows:
        draw.text((x0 + 22, y), label, fill="#6b7280", font=font_axis)
        draw.text((x0 + 160, y), value, fill="#111827", font=font_axis)
        y += 38
    draw.text((x0 + 20, y + 12), "刹停点要求：自车 stationary，前车 |v| <= 0.2 m/s", fill="#4b5563", font=font_small)


def choose_axis_upper(max_value: float) -> float:
    if not math.isfinite(max_value):
        return 10.0
    return max(8.0, math.ceil((max_value + 1.0) / choose_bin_width(max_value + 1.0)) * choose_bin_width(max_value + 1.0))


def choose_bin_width(axis_upper: float) -> float:
    if axis_upper <= 12:
        return 0.5
    if axis_upper <= 35:
        return 1.0
    if axis_upper <= 80:
        return 2.0
    return 5.0


def choose_tick_step(axis_upper: float) -> float:
    if axis_upper <= 10:
        return 1.0
    if axis_upper <= 20:
        return 2.0
    if axis_upper <= 50:
        return 5.0
    return 10.0


def format_tick(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return "%.1f" % value


def format_optional(value: Optional[float]) -> str:
    return "-" if value is None else "%.3f m" % value


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


if __name__ == "__main__":
    raise SystemExit(main())
