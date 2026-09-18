from __future__ import annotations

import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable

import h5py


SignalRecord = Dict[str, object]
SignalDict = Dict[str, SignalRecord]
TIMESTAMP_DUPLICATE_EPSILON = 1e-9
DEFAULT_SLICE_CACHE_SIZE = 16
DEFAULT_SAMPLE_CACHE_SIZE = 4


@dataclass
class SceneSegment:
    start_index: int
    end_index: int
    start_timestamp: float
    end_timestamp: float
    duration: float
    sample_count: int


class FolderSession:
    def __init__(
        self,
        primary_h5_file: str | Path,
        preprocess_signals: list[str] | None = None,
        additional_h5_files: list[str | Path] | None = None,
        context_end_timestamp: float | None = None,
    ) -> None:
        self.primary_h5_file = str(primary_h5_file)
        self.h5_files = [self.primary_h5_file] + [str(path) for path in additional_h5_files or []]
        self.context_end_timestamp = None if context_end_timestamp is None else float(context_end_timestamp)
        self.preprocess_signals = list(preprocess_signals or [])
        file_end_timestamps = [None] + [
            self.context_end_timestamp for _path in (additional_h5_files or [])
        ]
        self.store = H5SignalStore.from_hdf5_files(
            self.h5_files,
            signal_names=self.preprocess_signals or None,
            file_end_timestamps=file_end_timestamps,
        )
        self._slice_cache: dict[tuple[str, float, float], tuple[list[float], list[object]]] = {}
        self._sample_cache: dict[tuple[object, ...], list[object]] = {}
        self._slice_cache_size = _env_int("SIGNAL_SLICE_CACHE_SIZE", DEFAULT_SLICE_CACHE_SIZE)
        self._sample_cache_size = _env_int("SIGNAL_SAMPLE_CACHE_SIZE", DEFAULT_SAMPLE_CACHE_SIZE)

    def get_store(self) -> "H5SignalStore":
        return self.store

    def has_signal(self, signal_name: str) -> bool:
        return self.store.has_signal(signal_name)

    def missing_signals(self, signal_names: list[str]) -> list[str]:
        return [signal_name for signal_name in signal_names if not self.has_signal(signal_name)]

    def common_time_range(self, signal_names: list[str]) -> tuple[float, float]:
        start_timestamp = max(_timestamps(self.store.get_signal(name))[0] for name in signal_names)
        end_timestamp = min(_timestamps(self.store.get_signal(name))[-1] for name in signal_names)
        if self.context_end_timestamp is not None:
            end_timestamp = min(float(end_timestamp), self.context_end_timestamp)
        if end_timestamp < start_timestamp:
            raise ValueError("No overlapping time range across the requested signals")
        return float(start_timestamp), float(end_timestamp)

    def get_signal_slice(
        self,
        signal_name: str,
        start_timestamp: float,
        end_timestamp: float,
    ) -> tuple[list[float], list[object]]:
        cache_key = (signal_name, float(start_timestamp), float(end_timestamp))
        cached = _cache_get(self._slice_cache, cache_key)
        if cached is not None:
            return cached

        signal = self.store.get_signal(signal_name)
        timestamps = _timestamps(signal)
        values = _values(signal)
        left = bisect_left(timestamps, float(start_timestamp))
        right = bisect_right(timestamps, float(end_timestamp))
        if left == 0 and right == len(timestamps):
            sliced = (timestamps, values)
        else:
            sliced = (timestamps[left:right], values[left:right])
        _cache_set(self._slice_cache, cache_key, sliced, self._slice_cache_size)
        return sliced

    def sample_signal_to_axis(
        self,
        signal_name: str,
        target_timestamps: list[float],
        method: str = "nearest",
        start_timestamp: float | None = None,
        end_timestamp: float | None = None,
    ) -> list[object]:
        axis_key = _axis_cache_key(target_timestamps)
        cache_key = (
            signal_name,
            axis_key,
            method,
            None if start_timestamp is None else float(start_timestamp),
            None if end_timestamp is None else float(end_timestamp),
        )
        cached = _cache_get(self._sample_cache, cache_key)
        if cached is not None:
            return cached

        if start_timestamp is None or end_timestamp is None:
            signal = self.store.get_signal(signal_name)
            source_timestamps = _timestamps(signal)
            source_values = _values(signal)
        else:
            source_timestamps, source_values = self.get_signal_slice(signal_name, start_timestamp, end_timestamp)

        sampled = sample_signal(source_timestamps, source_values, target_timestamps, method=method)
        _cache_set(self._sample_cache, cache_key, sampled, self._sample_cache_size)
        return sampled


    def _preload_configured_signals(self) -> None:
        for signal_name in self.preprocess_signals:
            try:
                if not self.store.has_signal(signal_name):
                    continue
                signal = self.store.get_signal(signal_name)
            except KeyError:
                continue

            timestamps = _timestamps(signal)
            values = _values(signal)
            if not timestamps:
                continue
            self._slice_cache[(signal_name, float(timestamps[0]), float(timestamps[-1]))] = (
                timestamps[:],
                values[:],
            )


class H5SignalStore:
    def __init__(
        self,
        signals: SignalDict,
        source_path: str,
        base_group: str,
        channel_name: str = "Channel1",
        source_paths: list[str] | None = None,
    ) -> None:
        self.signals = signals
        self.source_path = source_path
        self.source_paths = source_paths or [source_path]
        self.base_group = base_group
        self.channel_name = channel_name

    @classmethod
    def from_hdf5(
        cls,
        file_path: str | Path,
        channel_name: str = "Channel1",
        use_message_prefix: bool = True,
        signal_names: list[str] | None = None,
        end_timestamp: float | None = None,
    ) -> "H5SignalStore":
        signals, base_group = load_signal_dict(
            file_path=file_path,
            channel_name=channel_name,
            use_message_prefix=use_message_prefix,
            signal_names=signal_names,
            end_timestamp=end_timestamp,
        )
        return cls(
            signals=signals,
            source_path=str(file_path),
            base_group=base_group,
            channel_name=channel_name,
            source_paths=[str(file_path)],
        )

    @classmethod
    def from_hdf5_files(
        cls,
        file_paths: list[str | Path],
        channel_name: str = "Channel1",
        use_message_prefix: bool = True,
        signal_names: list[str] | None = None,
        file_end_timestamps: list[float | None] | None = None,
    ) -> "H5SignalStore":
        if not file_paths:
            raise ValueError("file_paths must not be empty")

        merged_signals: SignalDict = {}
        source_paths = [str(path) for path in file_paths]
        base_group = ""
        for source_index, file_path in enumerate(file_paths):
            end_timestamp = None
            if file_end_timestamps is not None and source_index < len(file_end_timestamps):
                end_timestamp = file_end_timestamps[source_index]
            signals, current_base_group = load_signal_dict(
                file_path=file_path,
                channel_name=channel_name,
                use_message_prefix=use_message_prefix,
                signal_names=signal_names,
                end_timestamp=end_timestamp,
            )
            if not base_group:
                base_group = current_base_group
            merge_signal_dicts(merged_signals, signals, source_path=str(file_path), source_index=source_index)

        return cls(
            signals=merged_signals,
            source_path=source_paths[0],
            source_paths=source_paths,
            base_group=base_group,
            channel_name=channel_name,
        )

    def list_signals(self) -> list[str]:
        return sorted(self.signals.keys())

    def has_signal(self, signal_name: str) -> bool:
        return signal_name in self.signals

    def get_signal(self, signal_name: str) -> SignalRecord:
        if signal_name not in self.signals:
            raise KeyError(f"Signal not found: {signal_name}")
        return self.signals[signal_name]

    def get_value_at(
        self,
        signal_name: str,
        timestamp: float,
        method: str = "nearest",
    ) -> dict[str, object]:
        signal = self.get_signal(signal_name)
        timestamps = _timestamps(signal)
        values = _values(signal)

        if not timestamps:
            raise ValueError(f"Signal has no samples: {signal_name}")

        index = _locate_index(timestamps, float(timestamp), method)
        return {
            "signal_name": signal_name,
            "query_timestamp": float(timestamp),
            "matched_timestamp": timestamps[index],
            "value": values[index],
            "index": index,
        }

    def get_range(
        self,
        signal_name: str,
        start_timestamp: float,
        end_timestamp: float,
    ) -> dict[str, object]:
        signal = self.get_signal(signal_name)
        timestamps = _timestamps(signal)
        values = _values(signal)

        left = bisect_left(timestamps, float(start_timestamp))
        right = bisect_right(timestamps, float(end_timestamp))
        return {
            "signal_name": signal_name,
            "start_timestamp": float(start_timestamp),
            "end_timestamp": float(end_timestamp),
            "timestamps": timestamps[left:right],
            "values": values[left:right],
            "size": right - left,
        }

    def align_signals(
        self,
        signal_names: list[str],
        start_timestamp: float | None = None,
        end_timestamp: float | None = None,
        step: float = 0.1,
        method: str = "nearest",
    ) -> dict[str, object]:
        if not signal_names:
            raise ValueError("signal_names must not be empty")
        if step <= 0:
            raise ValueError("step must be positive")

        if start_timestamp is None:
            start_timestamp = max(_timestamps(self.get_signal(name))[0] for name in signal_names)
        if end_timestamp is None:
            end_timestamp = min(_timestamps(self.get_signal(name))[-1] for name in signal_names)
        if end_timestamp < start_timestamp:
            raise ValueError("No overlapping time range across the requested signals")

        axis = build_time_axis(float(start_timestamp), float(end_timestamp), step)
        aligned = {"timestamp": axis}
        for name in signal_names:
            signal = self.get_signal(name)
            aligned[name] = sample_signal(
                source_timestamps=_timestamps(signal),
                source_values=_values(signal),
                target_timestamps=axis,
                method=method,
            )
        return aligned

    def find_segments(
        self,
        aligned_data: dict[str, object],
        condition: Callable[[dict[str, object]], Iterable[bool]],
        min_duration: float = 0.0,
    ) -> list[SceneSegment]:
        timestamps = aligned_data.get("timestamp", [])
        mask = [bool(item) for item in condition(aligned_data)]
        if len(mask) != len(timestamps):
            raise ValueError("condition must return a boolean list with the same length as timestamps")

        segments = mask_to_segments(timestamps, mask)
        if min_duration <= 0:
            return segments
        return [segment for segment in segments if segment.duration >= min_duration]


def load_signal_dict(
    file_path: str | Path,
    channel_name: str = "Channel1",
    use_message_prefix: bool = True,
    signal_names: list[str] | None = None,
    end_timestamp: float | None = None,
) -> tuple[SignalDict, str]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"HDF5 file not found: {path}")

    signal_dict: SignalDict = {}
    wanted_by_message, wanted_bare_fields = _split_requested_signals(signal_names, use_message_prefix)
    with h5py.File(path, "r") as h5_file:
        base_group = _first_group_name(h5_file)
        channel_path = f"{base_group}/{channel_name}"
        if channel_path not in h5_file:
            raise KeyError(f"Channel path not found: {channel_path}")

        channel = h5_file[channel_path]
        for message_name in channel.keys():
            wanted_fields = _wanted_fields_for_message(message_name, wanted_by_message, wanted_bare_fields)
            if signal_names and not wanted_fields:
                continue

            dataset = channel[message_name]
            field_names = dataset.dtype.names or ()
            if "timestamp" not in field_names:
                continue
            value_field_names = [
                field_name
                for field_name in field_names
                if field_name != "timestamp" and (wanted_fields is None or field_name in wanted_fields)
            ]
            if not value_field_names:
                continue

            timestamps = [float(item) for item in dataset["timestamp"][:]]
            timestamps_sorted = _is_non_decreasing(timestamps)
            order: list[int] | None = None
            read_limit = len(timestamps)
            if timestamps_sorted:
                if end_timestamp is not None:
                    read_limit = bisect_right(timestamps, float(end_timestamp))
                ordered_timestamps = timestamps[:read_limit]
            else:
                order = sorted(range(len(timestamps)), key=timestamps.__getitem__)
                if end_timestamp is not None:
                    max_timestamp = float(end_timestamp)
                    order = [index for index in order if timestamps[index] <= max_timestamp]
                ordered_timestamps = [timestamps[index] for index in order]
            timestamps_unique = _timestamps_are_unique(ordered_timestamps)

            for field_name in value_field_names:
                if order is None:
                    raw_values = dataset[field_name][:read_limit]
                    ordered_values = [_to_python(value) for value in raw_values]
                else:
                    raw_values = dataset[field_name][:]
                    ordered_values = [_to_python(raw_values[index]) for index in order]
                signal_key = make_signal_key(message_name, field_name, use_message_prefix)
                signal_dict[signal_key] = {
                    "message": message_name,
                    "signal": field_name,
                    "timestamps": ordered_timestamps,
                    "values": ordered_values,
                    "dtype": str(getattr(raw_values, "dtype", type(raw_values).__name__)),
                    "size": len(ordered_values),
                    "timestamps_sorted": True,
                    "timestamps_unique": timestamps_unique,
                }

    return signal_dict, base_group


def merge_signal_dicts(
    target: SignalDict,
    incoming: SignalDict,
    source_path: str,
    source_index: int,
) -> None:
    for signal_key, incoming_signal in incoming.items():
        if signal_key not in target:
            copied_signal = dict(incoming_signal)
            copied_signal["source_paths"] = [source_path]
            copied_signal["source_indexes"] = [source_index]
            target[signal_key] = copied_signal
            continue

        existing_signal = target[signal_key]
        existing_timestamps = _timestamps(existing_signal)
        existing_values = _values(existing_signal)
        incoming_timestamps = _timestamps(incoming_signal)
        incoming_values = _values(incoming_signal)
        existing_sorted = bool(existing_signal.get("timestamps_sorted", False))
        incoming_sorted = bool(incoming_signal.get("timestamps_sorted", False))
        if not existing_sorted:
            existing_sorted = _is_non_decreasing(existing_timestamps)
        if not incoming_sorted:
            incoming_sorted = _is_non_decreasing(incoming_timestamps)

        if existing_sorted and incoming_sorted:
            existing_unique = bool(existing_signal.get("timestamps_unique", False))
            incoming_unique = bool(incoming_signal.get("timestamps_unique", False))
            if not existing_unique:
                existing_unique = _timestamps_are_unique(existing_timestamps)
            if not incoming_unique:
                incoming_unique = _timestamps_are_unique(incoming_timestamps)

            if (
                existing_unique
                and incoming_unique
                and _timestamp_ranges_are_separate(existing_timestamps, incoming_timestamps)
            ):
                if not incoming_timestamps or (
                    existing_timestamps and existing_timestamps[-1] < incoming_timestamps[0]
                ):
                    merged_timestamps = list(existing_timestamps) + list(incoming_timestamps)
                    merged_values = list(existing_values) + list(incoming_values)
                else:
                    merged_timestamps = list(incoming_timestamps) + list(existing_timestamps)
                    merged_values = list(incoming_values) + list(existing_values)
            else:
                merged_timestamps, merged_values = _merge_sorted_signal_samples(
                    existing_timestamps,
                    existing_values,
                    incoming_timestamps,
                    incoming_values,
                )
        else:
            combined = [
                (float(timestamp), value)
                for timestamp, value in zip(existing_timestamps, existing_values)
            ]
            combined.extend(
                (float(timestamp), value)
                for timestamp, value in zip(incoming_timestamps, incoming_values)
            )
            combined.sort(key=lambda item: item[0])

            merged_timestamps = []
            merged_values = []
            for timestamp, value in combined:
                if (
                    merged_timestamps
                    and abs(timestamp - merged_timestamps[-1]) <= TIMESTAMP_DUPLICATE_EPSILON
                ):
                    continue
                merged_timestamps.append(timestamp)
                merged_values.append(value)

        existing_signal["timestamps"] = merged_timestamps
        existing_signal["values"] = merged_values
        existing_signal["size"] = len(merged_values)
        existing_signal["timestamps_sorted"] = True
        existing_signal["timestamps_unique"] = True
        existing_signal["source_paths"] = list(existing_signal.get("source_paths", [])) + [source_path]
        existing_signal["source_indexes"] = list(existing_signal.get("source_indexes", [])) + [source_index]


def build_time_axis(start_timestamp: float, end_timestamp: float, step: float) -> list[float]:
    axis: list[float] = []
    current = float(start_timestamp)
    end_value = float(end_timestamp)
    while current <= end_value + step * 1e-9:
        axis.append(round(current, 9))
        current += step
    return axis


def sample_signal(
    source_timestamps: list[float],
    source_values: list[object],
    target_timestamps: list[float],
    method: str = "nearest",
) -> list[object]:
    if method not in {"nearest", "pad", "backfill"}:
        raise ValueError("method must be one of: nearest, pad, backfill")
    if not source_timestamps:
        raise ValueError("source signal is empty")

    if _is_non_decreasing(target_timestamps):
        return _sample_signal_monotonic(source_timestamps, source_values, target_timestamps, method)

    sampled: list[object] = []
    for timestamp in target_timestamps:
        index = _locate_index(source_timestamps, timestamp, method)
        sampled.append(source_values[index])
    return sampled


def mask_to_segments(timestamps: list[float], mask: list[bool]) -> list[SceneSegment]:
    segments: list[SceneSegment] = []
    start_index: int | None = None

    for index, active in enumerate(mask):
        if active and start_index is None:
            start_index = index
        if not active and start_index is not None:
            end_index = index - 1
            segments.append(_build_segment(timestamps, start_index, end_index))
            start_index = None

    if start_index is not None:
        segments.append(_build_segment(timestamps, start_index, len(mask) - 1))
    return segments


def make_signal_key(message_name: str, signal_name: str, use_message_prefix: bool = True) -> str:
    if use_message_prefix:
        return f"{message_name}.{signal_name}"
    return signal_name


def _split_requested_signals(
    signal_names: list[str] | None,
    use_message_prefix: bool,
) -> tuple[dict[str, set[str]] | None, set[str] | None]:
    if not signal_names:
        return None, None

    by_message: dict[str, set[str]] = {}
    bare_fields: set[str] = set()
    for signal_name in signal_names:
        clean_name = str(signal_name).strip()
        if not clean_name:
            continue
        if use_message_prefix and "." in clean_name:
            message_name, field_name = clean_name.rsplit(".", 1)
            by_message.setdefault(message_name, set()).add(field_name)
        else:
            bare_fields.add(clean_name)
    return by_message, bare_fields


def _wanted_fields_for_message(
    message_name: str,
    wanted_by_message: dict[str, set[str]] | None,
    wanted_bare_fields: set[str] | None,
) -> set[str] | None:
    if wanted_by_message is None and wanted_bare_fields is None:
        return None

    wanted_fields = set(wanted_bare_fields or set())
    wanted_fields.update((wanted_by_message or {}).get(message_name, set()))
    return wanted_fields


def _axis_cache_key(axis: list[float]) -> tuple[int, int, float | None, float | None]:
    if not axis:
        return (id(axis), 0, None, None)
    return (id(axis), len(axis), float(axis[0]), float(axis[-1]))


def _is_non_decreasing(values: list[float]) -> bool:
    return all(values[index] >= values[index - 1] for index in range(1, len(values)))


def _timestamps_are_unique(values: list[float]) -> bool:
    return all(
        abs(values[index] - values[index - 1]) > TIMESTAMP_DUPLICATE_EPSILON
        for index in range(1, len(values))
    )


def _timestamp_ranges_are_separate(first: list[float], second: list[float]) -> bool:
    if not first or not second:
        return True
    return (
        first[-1] < second[0] - TIMESTAMP_DUPLICATE_EPSILON
        or second[-1] < first[0] - TIMESTAMP_DUPLICATE_EPSILON
    )


def _merge_sorted_signal_samples(
    first_timestamps: list[float],
    first_values: list[object],
    second_timestamps: list[float],
    second_values: list[object],
) -> tuple[list[float], list[object]]:
    merged_timestamps: list[float] = []
    merged_values: list[object] = []
    first_index = 0
    second_index = 0

    while first_index < len(first_timestamps) or second_index < len(second_timestamps):
        use_first = second_index >= len(second_timestamps) or (
            first_index < len(first_timestamps)
            and first_timestamps[first_index] <= second_timestamps[second_index]
        )
        if use_first:
            timestamp = float(first_timestamps[first_index])
            value = first_values[first_index]
            first_index += 1
        else:
            timestamp = float(second_timestamps[second_index])
            value = second_values[second_index]
            second_index += 1

        if (
            merged_timestamps
            and abs(timestamp - merged_timestamps[-1]) <= TIMESTAMP_DUPLICATE_EPSILON
        ):
            continue
        merged_timestamps.append(timestamp)
        merged_values.append(value)

    return merged_timestamps, merged_values


def _sample_signal_monotonic(
    source_timestamps: list[float],
    source_values: list[object],
    target_timestamps: list[float],
    method: str,
) -> list[object]:
    sampled: list[object] = []
    insert_pos = 0
    source_count = len(source_timestamps)

    for timestamp in target_timestamps:
        while insert_pos < source_count and source_timestamps[insert_pos] < timestamp:
            insert_pos += 1

        if method == "pad":
            if insert_pos < source_count and source_timestamps[insert_pos] == timestamp:
                sampled.append(source_values[insert_pos])
                continue
            if insert_pos == 0:
                raise ValueError("No earlier sample found for pad lookup")
            sampled.append(source_values[insert_pos - 1])
            continue

        if method == "backfill":
            if insert_pos >= source_count:
                raise ValueError("No later sample found for backfill lookup")
            sampled.append(source_values[insert_pos])
            continue

        if insert_pos == 0:
            sampled.append(source_values[0])
        elif insert_pos >= source_count:
            sampled.append(source_values[-1])
        else:
            prev_index = insert_pos - 1
            next_index = insert_pos
            prev_gap = abs(timestamp - source_timestamps[prev_index])
            next_gap = abs(source_timestamps[next_index] - timestamp)
            sampled.append(source_values[prev_index] if prev_gap <= next_gap else source_values[next_index])

    return sampled


def _cache_get(cache: dict[tuple[object, ...], object], key: tuple[object, ...]) -> object | None:
    if key not in cache:
        return None
    value = cache.pop(key)
    cache[key] = value
    return value


def _cache_set(cache: dict[tuple[object, ...], object], key: tuple[object, ...], value: object, max_size: int) -> None:
    if max_size <= 0:
        return
    if key in cache:
        cache.pop(key)
    cache[key] = value
    while len(cache) > max_size:
        cache.pop(next(iter(cache)))


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except Exception:
        return default


def _build_segment(timestamps: list[float], start_index: int, end_index: int) -> SceneSegment:
    start_timestamp = timestamps[start_index]
    end_timestamp = timestamps[end_index]
    return SceneSegment(
        start_index=start_index,
        end_index=end_index,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        duration=max(0.0, end_timestamp - start_timestamp),
        sample_count=end_index - start_index + 1,
    )


def _first_group_name(h5_file: h5py.File) -> str:
    keys = list(h5_file.keys())
    if not keys:
        raise ValueError("HDF5 file is empty")
    return keys[0]


def _locate_index(timestamps: list[float], timestamp: float, method: str) -> int:
    insert_pos = bisect_left(timestamps, timestamp)

    if method == "pad":
        if insert_pos < len(timestamps) and timestamps[insert_pos] == timestamp:
            return insert_pos
        if insert_pos == 0:
            raise ValueError("No earlier sample found for pad lookup")
        return insert_pos - 1

    if method == "backfill":
        if insert_pos >= len(timestamps):
            raise ValueError("No later sample found for backfill lookup")
        return insert_pos

    if insert_pos == 0:
        return 0
    if insert_pos >= len(timestamps):
        return len(timestamps) - 1

    prev_index = insert_pos - 1
    next_index = insert_pos
    prev_gap = abs(timestamp - timestamps[prev_index])
    next_gap = abs(timestamps[next_index] - timestamp)
    if prev_gap <= next_gap:
        return prev_index
    return next_index


def _timestamps(signal: SignalRecord) -> list[float]:
    return signal["timestamps"]  # type: ignore[return-value]


def _values(signal: SignalRecord) -> list[object]:
    return signal["values"]  # type: ignore[return-value]


def _to_python(value: object) -> object:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value
