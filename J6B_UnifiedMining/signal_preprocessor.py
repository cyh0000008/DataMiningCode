from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

import h5py
import numpy as np


GRAVITY_MPS2 = 9.80665
VALUE_VALIDITY_FIELDS = {
    "acceleration": "acceleration_valid",
    "ego_speed": "ego_speed_valid",
    "odometer": "odometer_valid",
    "actual_brake_torque": "actual_brake_torque_valid",
    "actual_drive_torque": "actual_drive_torque_valid",
}


@dataclass(frozen=True)
class SignalSeries:
    name: str
    path: str
    timestamps: np.ndarray
    values: np.ndarray


@dataclass(frozen=True)
class MissingSignal:
    field_name: str
    signal_path: str
    normalized_path: str


@dataclass
class SignalFrame:
    source_path: str
    base_group: str
    paths: Dict[str, str]
    fields: Dict[str, np.ndarray]
    missing_signals: Dict[str, str]
    distance_km: Optional[float] = None
    distance_status: str = ""
    primary_length: Optional[int] = None
    has_continuous_previous: bool = False
    stitched_source_paths: Tuple[str, ...] = ()
    runtime_cache: Dict[Any, Any] = field(default_factory=dict, repr=False)

    def col(self, name: str, default: Optional[float] = None) -> np.ndarray:
        if name in self.fields:
            return self.fields[name]
        if default is None:
            raise KeyError("Field not found in SignalFrame: %s" % name)
        return np.full(self.length, default, dtype=float)

    def has(self, name: str) -> bool:
        return name in self.fields

    def missing_signal_details(self, field_names: Iterable[str]) -> Dict[str, str]:
        details: Dict[str, str] = {}
        for field_name in field_names:
            matched = False
            for raw_field in raw_dependencies_for_field(field_name):
                if raw_field in self.missing_signals:
                    details[raw_field] = self.missing_signals[raw_field]
                    matched = True
            if field_name in self.missing_signals:
                details[field_name] = self.missing_signals[field_name]
                matched = True
            elif field_name not in self.fields and not matched:
                details[field_name] = "<derived field unavailable>"
        return details

    @property
    def length(self) -> int:
        return int(len(self.fields["time_s"]))

    @property
    def time_s(self) -> np.ndarray:
        return self.fields["time_s"]

    @property
    def time_ms(self) -> np.ndarray:
        return self.fields["time_ms"]


class H5SignalReader:
    def __init__(self, file_path: Union[str, Path]) -> None:
        self.file_path = str(file_path)
        self.signals: Dict[str, SignalSeries] = {}
        self._missing_paths: Set[str] = set()
        self._timestamp_cache: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self.base_group = ""
        self._h5 = h5py.File(self.file_path, "r")
        self.base_group = _detect_base_group(self._h5)
        self._root = self._h5[self.base_group] if self.base_group else self._h5

    def close(self) -> None:
        self._h5.close()

    def __enter__(self) -> "H5SignalReader":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def require(self, field_name: str, signal_path: str) -> SignalSeries:
        series = self.get(signal_path)
        if series is None:
            raise KeyError(
                "Configured H5 path not found for field '%s': %s" % (field_name, signal_path)
            )
        return series

    def get(self, signal_path: str) -> Optional[SignalSeries]:
        normalized = _normalize_path(signal_path, self.base_group)
        if normalized in self.signals:
            return self.signals[normalized]
        if normalized in self._missing_paths:
            return None

        series = self._read_signal(normalized)
        if series is None:
            self._missing_paths.add(normalized)
            return None
        self.signals[normalized] = series
        return series

    def get_any(self, signal_paths: Any) -> Optional[SignalSeries]:
        for signal_path in iter_signal_paths(signal_paths):
            series = self.get(signal_path)
            if series is not None:
                return series
        return None

    def has_any(self, signal_paths: Any) -> bool:
        return any(self.has(signal_path) for signal_path in iter_signal_paths(signal_paths))

    def has(self, signal_path: str) -> bool:
        normalized = _normalize_path(signal_path, self.base_group)
        if normalized in self.signals:
            return True
        if normalized in self._missing_paths:
            return False
        exists = self._split_signal_exists(normalized) or self._compound_signal_exists(normalized)
        if not exists:
            self._missing_paths.add(normalized)
        return exists

    def _read_signal(self, normalized_path: str) -> Optional[SignalSeries]:
        series = self._read_split_signal(normalized_path)
        if series is not None:
            return series
        return self._read_compound_signal(normalized_path)

    def _read_split_signal(self, normalized_path: str) -> Optional[SignalSeries]:
        if "/signals/" not in normalized_path:
            return None
        signal_obj = self._get_obj(normalized_path)
        if not isinstance(signal_obj, h5py.Dataset):
            return None

        parent_path, _separator, signal_name = normalized_path.partition("/signals/")
        timestamps, order = self._read_split_timestamp(parent_path)
        if timestamps.size == 0:
            return None

        values = np.asarray(signal_obj[()])
        if values.shape[0:1] != (order.size,):
            return None
        return SignalSeries(
            name=signal_name,
            path=normalized_path,
            timestamps=timestamps,
            values=_normalize_values(values[order]),
        )

    def _read_compound_signal(self, normalized_path: str) -> Optional[SignalSeries]:
        dataset_path, signal_name = _split_compound_signal_path(normalized_path)
        if not dataset_path or not signal_name:
            return None
        dataset = self._get_obj(dataset_path)
        if not isinstance(dataset, h5py.Dataset):
            return None
        names = dataset.dtype.names
        if names is None or "timestamp" not in names or signal_name not in names:
            return None

        data = dataset[()]
        timestamps = np.asarray(data["timestamp"], dtype=float)
        if timestamps.size == 0:
            return None
        order = np.argsort(timestamps, kind="mergesort")
        return SignalSeries(
            name=signal_name,
            path=normalized_path,
            timestamps=timestamps[order],
            values=_normalize_values(np.asarray(data[signal_name])[order]),
        )

    def _split_signal_exists(self, normalized_path: str) -> bool:
        if "/signals/" not in normalized_path:
            return False
        signal_obj = self._get_obj(normalized_path)
        if not isinstance(signal_obj, h5py.Dataset):
            return False
        parent_path = normalized_path.partition("/signals/")[0]
        return isinstance(self._get_obj(parent_path + "/timestamp"), h5py.Dataset)

    def _compound_signal_exists(self, normalized_path: str) -> bool:
        dataset_path, signal_name = _split_compound_signal_path(normalized_path)
        if not dataset_path or not signal_name:
            return False
        dataset = self._get_obj(dataset_path)
        if not isinstance(dataset, h5py.Dataset) or dataset.dtype.names is None:
            return False
        return "timestamp" in dataset.dtype.names and signal_name in dataset.dtype.names

    def _read_split_timestamp(self, parent_path: str) -> Tuple[np.ndarray, np.ndarray]:
        cached = self._timestamp_cache.get(parent_path)
        if cached is not None:
            return cached
        timestamp_obj = self._get_obj(parent_path + "/timestamp")
        if not isinstance(timestamp_obj, h5py.Dataset):
            cached = (np.asarray([], dtype=float), np.asarray([], dtype=int))
            self._timestamp_cache[parent_path] = cached
            return cached
        timestamps = np.asarray(timestamp_obj[()], dtype=float).reshape(-1)
        order = np.argsort(timestamps, kind="mergesort")
        cached = (timestamps[order], order)
        self._timestamp_cache[parent_path] = cached
        return cached

    def _get_obj(self, normalized_path: str) -> Optional[Any]:
        try:
            return self._root[normalized_path]
        except KeyError:
            return None


def build_signal_frame(h5_path: Union[str, Path], config: Mapping[str, Any]) -> SignalFrame:
    signals = dict(config.get("signals", {}))
    optional_signals = dict(config.get("optional_signals", {}))
    if not signals:
        raise ValueError("config.json must contain a non-empty 'signals' mapping")

    axis_name = "acceleration"
    if axis_name not in signals:
        raise KeyError("Default axis signal '%s' is not configured under signals" % axis_name)

    with H5SignalReader(h5_path) as reader:
        missing = find_missing_configured_signals(reader, signals, optional_signals)
        missing_by_field = {item.field_name: item.signal_path for item in missing}
        axis_signal = select_axis_signal(reader, axis_name, signals, optional_signals)
        axis = _unique_time_axis(axis_signal.timestamps) if axis_signal is not None else np.asarray([], dtype=float)

        fields: Dict[str, np.ndarray] = {
            "time_s": axis.astype(float),
            "time_ms": axis.astype(float) * 1000.0,
        }
        resolved_paths: Dict[str, str] = {}
        sample_index_cache: Dict[int, np.ndarray] = {}

        for field_name, signal_path in signals.items():
            if signal_path is None:
                continue
            series = reader.get_any(signal_path)
            if series is None:
                continue
            resolved_paths[field_name] = series.path
            fields[field_name] = sample_nearest(
                series.timestamps,
                series.values,
                axis,
                index_cache=sample_index_cache,
                cache_key=id(series.timestamps),
            )

        for field_name, signal_path in optional_signals.items():
            if signal_path in (None, ""):
                continue
            series = reader.get_any(signal_path)
            if series is None:
                continue
            resolved_paths[field_name] = series.path
            fields[field_name] = sample_nearest(
                series.timestamps,
                series.values,
                axis,
                index_cache=sample_index_cache,
                cache_key=id(series.timestamps),
            )

        base_group = reader.base_group

    defaults = dict(config.get("defaults", {}))
    _apply_validity_checks(fields)
    _add_derived_fields(fields, defaults)
    distance_km, distance_status = _calculate_distance_km(fields, defaults)
    return SignalFrame(
        source_path=str(Path(h5_path).resolve()),
        base_group=base_group,
        paths=resolved_paths,
        fields=fields,
        missing_signals=missing_by_field,
        distance_km=distance_km,
        distance_status=distance_status,
        primary_length=None,
        has_continuous_previous=False,
        stitched_source_paths=(str(Path(h5_path).resolve()),),
    )


def stitch_signal_frames(
    primary: SignalFrame,
    follower: SignalFrame,
    follower_start_offset_s: float,
    has_continuous_previous: bool = False,
    field_names: Optional[Iterable[str]] = None,
) -> SignalFrame:
    primary_time = primary.time_s.astype(float)
    follower_time = follower.time_s.astype(float)
    if primary_time.size == 0 or follower_time.size == 0:
        primary.has_continuous_previous = has_continuous_previous
        return primary

    adjusted_follower_time = (
        float(primary_time[0])
        + float(follower_start_offset_s)
        + (follower_time - float(follower_time[0]))
    )
    keep_follower = adjusted_follower_time > float(primary_time[-1]) + 1e-9
    adjusted_follower_time = adjusted_follower_time[keep_follower]
    follower_keep_length = int(adjusted_follower_time.size)

    fields: Dict[str, np.ndarray] = {
        "time_s": np.concatenate([primary_time, adjusted_follower_time]),
        "time_ms": np.concatenate([primary_time, adjusted_follower_time]) * 1000.0,
    }
    if field_names is None:
        fields_to_stitch = [
            field_name
            for field_name in primary.fields
            if field_name not in ("time_s", "time_ms")
        ]
    else:
        fields_to_stitch = [
            field_name
            for field_name in dict.fromkeys(str(name) for name in field_names)
            if field_name not in ("time_s", "time_ms") and field_name in primary.fields
        ]

    for field_name in fields_to_stitch:
        primary_values = primary.fields[field_name]
        if field_name in ("time_s", "time_ms"):
            continue
        follower_values = follower.fields.get(field_name)
        if follower_values is None:
            follower_values = _default_stitched_values(field_name, primary_values, follower_keep_length)
        else:
            follower_values = np.asarray(follower_values)[keep_follower]
        fields[field_name] = np.concatenate([np.asarray(primary_values), follower_values])

    return SignalFrame(
        source_path=primary.source_path,
        base_group=primary.base_group,
        paths=dict(primary.paths),
        fields=fields,
        missing_signals=dict(primary.missing_signals),
        distance_km=primary.distance_km,
        distance_status=primary.distance_status,
        primary_length=primary.length,
        has_continuous_previous=has_continuous_previous,
        stitched_source_paths=(primary.source_path, follower.source_path),
    )


def _default_stitched_values(field_name: str, primary_values: np.ndarray, length: int) -> np.ndarray:
    values = np.asarray(primary_values)
    if values.dtype.kind == "b":
        return np.zeros(length, dtype=bool)
    if field_name in {"front_ttc", "front_range", "curv_speed_limit", "curvature_radius"}:
        return np.full(length, np.inf, dtype=float)
    return np.full(length, np.nan, dtype=float)


def find_missing_configured_signals(
    reader: H5SignalReader,
    signals: Mapping[str, Any],
    optional_signals: Mapping[str, Any],
) -> List[MissingSignal]:
    missing: List[MissingSignal] = []
    for source in (signals, optional_signals):
        for field_name, signal_path in source.items():
            if signal_path in (None, ""):
                continue
            normalized_paths = [_normalize_path(path, reader.base_group) for path in iter_signal_paths(signal_path)]
            if not reader.has_any(signal_path):
                missing.append(
                    MissingSignal(
                        field_name=str(field_name),
                        signal_path=format_configured_paths(signal_path),
                        normalized_path=" | ".join(normalized_paths),
                    )
                )
    return missing


def select_axis_signal(
    reader: H5SignalReader,
    axis_name: str,
    signals: Mapping[str, Any],
    optional_signals: Mapping[str, Any],
) -> Optional[SignalSeries]:
    preferred_path = signals.get(axis_name)
    if preferred_path not in (None, ""):
        preferred = reader.get_any(preferred_path)
        if preferred is not None:
            return preferred
    for source in (signals, optional_signals):
        for signal_path in source.values():
            if signal_path in (None, ""):
                continue
            series = reader.get_any(signal_path)
            if series is not None:
                return series
    return None


def iter_signal_paths(signal_paths: Any) -> List[str]:
    if signal_paths in (None, ""):
        return []
    if isinstance(signal_paths, (list, tuple)):
        return [str(item) for item in signal_paths if item not in (None, "")]
    return [str(signal_paths)]


def format_configured_paths(signal_paths: Any) -> str:
    return " | ".join(iter_signal_paths(signal_paths))


def format_missing_signals(missing_signals: Iterable[MissingSignal]) -> str:
    return "; ".join(
        "%s=%s" % (item.field_name, item.signal_path)
        for item in missing_signals
    )


def sample_nearest(
    source_t: np.ndarray,
    source_v: np.ndarray,
    target_t: np.ndarray,
    index_cache: Optional[Dict[Any, np.ndarray]] = None,
    cache_key: Optional[Any] = None,
) -> np.ndarray:
    if source_t.size == 0:
        raise ValueError("Cannot sample an empty signal")
    if source_t.size == 1:
        return np.full(target_t.shape, source_v[0], dtype=source_v.dtype)
    index = None
    if index_cache is not None and cache_key is not None:
        index = index_cache.get(cache_key)
    if index is None:
        right = np.searchsorted(source_t, target_t, side="left")
        right = np.clip(right, 0, source_t.size - 1)
        left = np.clip(right - 1, 0, source_t.size - 1)
        choose_right = np.abs(source_t[right] - target_t) < np.abs(target_t - source_t[left])
        index = np.where(choose_right, right, left)
        if index_cache is not None and cache_key is not None:
            index_cache[cache_key] = index
    return source_v[index]


def collect_h5_files(input_path: Union[str, Path, Sequence[Union[str, Path]]]) -> List[str]:
    paths = list(input_path) if isinstance(input_path, (list, tuple)) else [input_path]
    if not paths:
        raise ValueError("input_path must contain at least one folder")

    files: List[str] = []
    for item in paths:
        path = Path(item)
        if not path.is_dir():
            raise FileNotFoundError("input_path must be an existing folder: %s" % path)
        files.extend(str(file_path.resolve()) for file_path in path.glob("*.h5"))
        files.extend(str(file_path.resolve()) for file_path in path.glob("*.hdf5"))
    return sorted(set(files))


DERIVED_DEPENDENCIES: Dict[str, Tuple[str, ...]] = {
    "ego_speed_mps": ("ego_speed", "ego_speed_valid"),
    "slope_acc": ("slope_deg",),
    "acceleration_total": ("acceleration", "acceleration_valid"),
    "stationary_flag": ("stationary_state", "ego_speed", "ego_speed_valid"),
    "moving_flag": ("stationary_state", "ego_speed", "ego_speed_valid"),
    "acc_active_flag": ("acc_active",),
    "pedal_override_flag": ("pedal_override",),
    "abs_active": ("abs_active_primary", "abs_active_ice2"),
    "tcs_active": ("tcs_active_primary", "tcs_active_ice2"),
    "vse_active": ("vse_active_primary", "vse_active_ice2"),
    "curv_enable_flag": ("curv_enable",),
    "front_valid": ("front_target_id", "front_range", "front_lat_range", "flt1_cutin_flag"),
    "front_closing_speed": ("ego_speed", "front_velocity_abs"),
    "front_ttc": ("front_target_id", "front_range", "front_lat_range", "front_velocity_abs", "ego_speed", "flt1_cutin_flag"),
    "cutin_valid": ("front_target_id", "front_range", "flt1_cutin_flag"),
    "cutin_target_id": ("front_target_id", "front_range", "flt1_cutin_flag"),
    "cutin_range_x": ("front_target_id", "front_range", "flt1_cutin_flag"),
    "cutin_velocity_abs": ("front_target_id", "front_range", "front_velocity_abs", "flt1_cutin_flag"),
    "cutin_accel_x": ("front_target_id", "front_range", "front_accel", "flt1_cutin_flag"),
    "cutin_closing_speed": ("front_target_id", "front_range", "front_velocity_abs", "ego_speed", "flt1_cutin_flag"),
    "cutin_ttc_calc": ("front_target_id", "front_range", "front_velocity_abs", "ego_speed", "flt1_cutin_flag"),
    "lane_c2_sum": ("left_lane_c2", "right_lane_c2"),
    "curvature": ("left_lane_c2", "right_lane_c2"),
    "curvature_radius": ("left_lane_c2", "right_lane_c2"),
}


def raw_dependencies_for_field(field_name: str) -> List[str]:
    visited = set()
    result: List[str] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        deps = DERIVED_DEPENDENCIES.get(name)
        if not deps:
            result.append(name)
            return
        for dep in deps:
            visit(dep)

    visit(field_name)
    validator = VALUE_VALIDITY_FIELDS.get(field_name)
    if validator and validator not in result:
        result.append(validator)
    return result


def _apply_validity_checks(fields: Dict[str, np.ndarray]) -> None:
    for value_field, validity_field in VALUE_VALIDITY_FIELDS.items():
        if value_field not in fields:
            continue
        if validity_field not in fields:
            fields["%s_valid_flag" % value_field] = np.ones(len(fields["time_s"]), dtype=bool)
            continue
        valid_flag = np.isclose(_float_col(fields, validity_field, np.full(len(fields["time_s"]), np.nan)), 0.0)
        fields["%s_valid_flag" % value_field] = valid_flag
        values = _float_col(fields, value_field, np.full(len(fields["time_s"]), np.nan))
        fields[value_field] = np.where(valid_flag, values, np.nan)


def _calculate_distance_km(
    fields: Mapping[str, np.ndarray],
    defaults: Mapping[str, Any],
) -> Tuple[Optional[float], str]:
    if "odometer" not in fields:
        return None, "missing odometer"
    odometer = _float_col(fields, "odometer", np.full(len(fields["time_s"]), np.nan))
    valid = np.isfinite(odometer)
    valid_values = odometer[valid]
    if valid_values.size < 2:
        return None, "not enough valid odometer samples"

    start_value = float(valid_values[0])
    end_value = float(valid_values[-1])
    delta = end_value - start_value
    rollover = float(defaults.get("odometer_rollover", 0.0))
    if delta < 0.0 and rollover > 0.0:
        delta = end_value + rollover - start_value
    if delta < 0.0:
        return None, "negative odometer delta"

    scale_to_km = float(defaults.get("odometer_scale_to_km", 1.0))
    return float(delta * scale_to_km), "ok"


def _add_derived_fields(
    fields: Dict[str, np.ndarray],
    defaults: Mapping[str, Any],
) -> None:
    n = len(fields["time_s"])

    if "ego_speed" in fields:
        fields["ego_speed_mps"] = _float_col(fields, "ego_speed", np.zeros(n, dtype=float)) / 3.6
    if "slope_deg" in fields:
        slope_deg = _float_col(fields, "slope_deg", np.zeros(n, dtype=float))
        fields["slope_acc"] = GRAVITY_MPS2 * np.sin(np.deg2rad(slope_deg))
    if "acceleration" in fields:
        fields["acceleration_total"] = _float_col(fields, "acceleration", np.zeros(n, dtype=float))

    stationary_sources = []
    if "ego_speed_mps" in fields:
        stationary_speed_threshold_mps = float(defaults.get("stationary_speed_threshold_mps", 0.01))
        stationary_sources.append(fields["ego_speed_mps"] < stationary_speed_threshold_mps)
    if "stationary_state" in fields:
        stationary_value = float(defaults.get("stationary_value", 1.0))
        stationary_raw = _float_col(fields, "stationary_state", np.zeros(n, dtype=float))
        stationary_sources.append(np.isclose(stationary_raw, stationary_value))
    if stationary_sources:
        fields["stationary_flag"] = np.logical_or.reduce(stationary_sources)
        fields["moving_flag"] = np.logical_not(fields["stationary_flag"])

    for target, primary, secondary in (
        ("abs_active", "abs_active_primary", "abs_active_ice2"),
        ("tcs_active", "tcs_active_primary", "tcs_active_ice2"),
        ("vse_active", "vse_active_primary", "vse_active_ice2"),
    ):
        coalesced = _coalesce_binary_active(fields, primary, secondary)
        if coalesced is not None:
            fields[target] = coalesced

    for source, target in (
        ("acc_active", "acc_active_flag"),
        ("pedal_override", "pedal_override_flag"),
        ("curv_enable", "curv_enable_flag"),
    ):
        if source in fields:
            fields[target] = _boolish(fields[source])

    if "flt1_cutin_flag" not in fields:
        cutin_flag_default = float(defaults.get("flt1_cutin_flag_default", 0.0))
        fields["flt1_cutin_flag"] = np.full(n, cutin_flag_default, dtype=float)

    flt1_core_fields = ("front_target_id", "front_range", "front_lat_range", "front_velocity_abs")
    if any(name not in fields for name in flt1_core_fields):
        fields["front_target_id"] = np.zeros(n, dtype=float)
        fields["front_range"] = np.full(n, np.inf, dtype=float)
        fields["front_lat_range"] = np.full(n, np.inf, dtype=float)
        fields["front_velocity_abs"] = fields.get("ego_speed_mps", np.zeros(n, dtype=float)).astype(float)
        fields["front_accel"] = np.zeros(n, dtype=float)
    elif "front_accel" not in fields:
        fields["front_accel"] = np.zeros(n, dtype=float)

    if all(name in fields for name in ("front_target_id", "front_range", "flt1_cutin_flag")):
        target_id = _float_col(fields, "front_target_id", np.zeros(n, dtype=float))
        front_range = _float_col(fields, "front_range", np.full(n, np.inf, dtype=float))
        front_lat_range = _float_col(fields, "front_lat_range", np.full(n, np.inf, dtype=float))
        flt1_cutin_flag = _float_col(fields, "flt1_cutin_flag", np.zeros(n, dtype=float))
        base_flt1_valid = np.logical_and(target_id != 0.0, np.isfinite(front_range))
        base_flt1_valid = np.logical_and(base_flt1_valid, front_range > 0.0)
        front_lateral_valid = np.logical_and(np.isfinite(front_lat_range), np.abs(front_lat_range) <= 2.0)
        fields["front_valid"] = np.logical_and.reduce(
            [base_flt1_valid, front_lateral_valid, np.isclose(flt1_cutin_flag, 0.0)]
        )
        fields["cutin_valid"] = np.logical_and(base_flt1_valid, np.isclose(flt1_cutin_flag, 1.0))
        fields["cutin_target_id"] = np.where(fields["cutin_valid"], target_id, 0.0)
        fields["cutin_range_x"] = np.where(fields["cutin_valid"], front_range, np.inf)
        if "front_accel" in fields:
            front_accel = _float_col(fields, "front_accel", np.zeros(n, dtype=float))
            fields["cutin_accel_x"] = np.where(fields["cutin_valid"], front_accel, 0.0)

    if "ego_speed_mps" in fields and "front_velocity_abs" in fields:
        front_velocity = _float_col(fields, "front_velocity_abs", np.zeros(n, dtype=float))
        fields["front_closing_speed"] = fields["ego_speed_mps"] - front_velocity
        if "cutin_valid" in fields:
            fields["cutin_velocity_abs"] = np.where(fields["cutin_valid"], front_velocity, 0.0)
            fields["cutin_closing_speed"] = fields["ego_speed_mps"] - fields["cutin_velocity_abs"]

    if all(name in fields for name in ("front_valid", "front_closing_speed", "front_range")):
        front_range = _float_col(fields, "front_range", np.full(n, np.inf, dtype=float))
        fields["front_ttc"] = np.where(
            np.logical_and(fields["front_valid"], fields["front_closing_speed"] > 1e-6),
            front_range / np.maximum(fields["front_closing_speed"], 1e-6),
            np.inf,
        )

    if all(name in fields for name in ("cutin_valid", "cutin_closing_speed", "cutin_range_x")):
        fields["cutin_ttc_calc"] = np.where(
            np.logical_and(fields["cutin_valid"], fields["cutin_closing_speed"] > 1e-6),
            fields["cutin_range_x"] / np.maximum(fields["cutin_closing_speed"], 1e-6),
            np.inf,
        )

    if "left_lane_c2" in fields and "right_lane_c2" in fields:
        left_c2 = _float_col(fields, "left_lane_c2", np.zeros(n, dtype=float))
        right_c2 = _float_col(fields, "right_lane_c2", np.zeros(n, dtype=float))
        c2_sum = left_c2 + right_c2
        abs_c2_sum = np.abs(c2_sum)
        fields["lane_c2_sum"] = c2_sum
        fields["curvature"] = abs_c2_sum
        fields["curvature_radius"] = np.divide(
            1.0,
            abs_c2_sum,
            out=np.full(n, np.inf, dtype=float),
            where=abs_c2_sum > 1e-9,
        )
    else:
        fields["lane_c2_sum"] = np.zeros(n, dtype=float)
        fields["curvature"] = np.zeros(n, dtype=float)
        fields["curvature_radius"] = np.full(n, np.inf, dtype=float)

    if "curv_speed_limit" in fields:
        fields["curv_speed_limit"] = _float_col(fields, "curv_speed_limit", np.full(n, np.inf))
    else:
        fields["curv_speed_limit"] = np.full(n, np.inf, dtype=float)

    if "curv_enable_flag" not in fields:
        fields["curv_enable_flag"] = np.zeros(n, dtype=bool)


def _detect_base_group(h5: h5py.File) -> str:
    keys = list(h5.keys())
    if len(keys) != 1:
        return ""
    item = h5[keys[0]]
    return keys[0] if isinstance(item, h5py.Group) else ""


def _normalize_path(path: str, base_group: str) -> str:
    normalized = _strip_leading_slash(path)
    prefix = _strip_leading_slash(base_group)
    if prefix and normalized.startswith(prefix + "/"):
        return normalized[len(prefix) + 1 :]
    return normalized


def _split_compound_signal_path(path: str) -> Tuple[str, str]:
    dataset_path, separator, signal_name = path.rpartition(".")
    if not separator:
        return "", ""
    return dataset_path, signal_name


def _strip_leading_slash(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("/")


def _normalize_values(values: np.ndarray) -> np.ndarray:
    if values.dtype.kind == "S":
        return values.astype(str)
    return values


def _unique_time_axis(timestamps: np.ndarray) -> np.ndarray:
    if timestamps.size == 0:
        raise ValueError("Axis signal has no samples")
    unique, index = np.unique(timestamps, return_index=True)
    order = np.argsort(index, kind="mergesort")
    axis = unique[order]
    return np.sort(axis.astype(float))


def _float_col(fields: Mapping[str, np.ndarray], name: str, default: np.ndarray) -> np.ndarray:
    if name not in fields:
        return np.asarray(default, dtype=float)
    values = np.asarray(fields[name])
    if values.dtype.kind in {"S", "U", "O"}:
        result = np.empty(values.shape, dtype=float)
        for index, item in enumerate(values):
            try:
                result[index] = float(item)
            except (TypeError, ValueError):
                result[index] = np.nan
        return result
    return values.astype(float, copy=False)


def _coalesce_binary_active(
    fields: Mapping[str, np.ndarray],
    primary_name: str,
    secondary_name: str,
) -> Optional[np.ndarray]:
    n = len(fields["time_s"])
    primary = _float_col(fields, primary_name, np.full(n, np.nan, dtype=float))
    secondary = _float_col(fields, secondary_name, np.full(n, np.nan, dtype=float))
    primary_valid = np.logical_and(np.isfinite(primary), np.isin(primary, [0.0, 1.0]))
    secondary_valid = np.logical_and(np.isfinite(secondary), np.isin(secondary, [0.0, 1.0]))
    any_valid = np.logical_or(primary_valid, secondary_valid)
    if not np.any(any_valid):
        return None
    active = np.logical_or(
        np.logical_and(primary_valid, np.isclose(primary, 1.0)),
        np.logical_and(secondary_valid, np.isclose(secondary, 1.0)),
    )
    return np.where(any_valid, active.astype(float), np.nan)


def _boolish(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values)
    if arr.dtype.kind in {"S", "U", "O"}:
        out = np.zeros(arr.shape, dtype=bool)
        for idx, item in enumerate(arr):
            text = str(item).strip().lower()
            out[idx] = text not in {"", "0", "0.0", "false", "none", "nan"}
        return out
    return arr.astype(float) != 0.0
