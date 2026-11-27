"""Exclude road excitation using /mla/egopose acceleration_local.az."""
from bisect import bisect_left
import json
import math
from pathlib import Path
import re
import warnings


def parse_egopose(content):
    samples = []
    records = re.split(r'(?m)^\s*(?=\d{19}\s+)', content)
    for record in records:
        if not record.strip():
            continue
        stamp = re.search(r'stamp:\s*\n\s*secs:\s*(\d+)\s*\n\s*nsecs:\s*(\d+)', record)
        prefix = re.match(r'\s*(\d{19})\s+', record)
        if stamp:
            timestamp = int(stamp[1]) * 1000 + int(stamp[2]) / 1e6
        elif prefix:
            timestamp = int(prefix[1]) / 1e6
        else:
            continue
        # ROS text messages use indentation to identify the exact field path.
        stack = []
        az = None
        for line in record.splitlines():
            match = re.match(r'^(\s*)(\w+):\s*(.*?)\s*$', line)
            if not match:
                continue
            indent, key, value = len(match[1]), match[2], match[3]
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path = [item[1] for item in stack] + [key]
            if path[-3:] == ['acceleration', 'acceleration_local', 'az']:
                try:
                    az = float(value)
                except ValueError:
                    pass
            if not value:
                stack.append((indent, key))
        # Some exports store the message body as JSON.
        if az is None:
            start = record.find('{')
            if start >= 0:
                try:
                    body, _ = json.JSONDecoder().raw_decode(record[start:])
                    az = float(body['acceleration']['acceleration_local']['az'])
                except (ValueError, KeyError, TypeError):
                    pass
        if az is not None and math.isfinite(az):
            samples.append((timestamp, az))
    return samples


def exclusion_mask(timestamps_ms, samples):
    triggers = sorted(t for t, az in samples if az * 9.81 < 8.31 or az * 9.81 > 11.31)
    mask = []
    for timestamp in timestamps_ms:
        index = bisect_left(triggers, timestamp - 2500.0)
        mask.append(index < len(triggers) and triggers[index] <= timestamp + 2500.0)
    return mask


def mark_road_excitation(frames, folder):
    samples = []
    for path in sorted(Path(folder).glob('*egopose*.txt')):
        samples.extend(parse_egopose(path.read_text(encoding='utf-8-sig', errors='replace')))
    if not samples:
        warnings.warn('未读取到 /mla/egopose.acceleration.acceleration_local.az，无法执行路面激励排除。', RuntimeWarning)
        return
    if frames:
        frames[0]['_road_excitation_samples'] = samples
    refresh_road_excitation(frames)


def refresh_road_excitation(frames):
    # Include samples from continuation files so exclusion windows cross files.
    samples = [sample for frame in frames for sample in frame.get('_road_excitation_samples', [])]
    if not samples:
        return
    timestamps = [frame['unp_timestamp_ms'] for frame in frames]
    for frame, excluded in zip(frames, exclusion_mask(timestamps, samples)):
        frame['_road_excitation_excluded'] = excluded
