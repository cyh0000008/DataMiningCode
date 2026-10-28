import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MMTJerkTests(unittest.TestCase):
    def run_mmt(self, script):
        setup = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "metrics" / "topic" / "code"))
"""
        result = subprocess.run(
            [sys.executable, "-c", setup + script],
            cwd=ROOT / "MMT_UnifiedMining",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_j6b_parity_and_all_topic_cpi_extremes(self):
        self.run_mmt(r'''
import importlib
import importlib.util
import warnings
import numpy as np
from jerk_utils import jerk_from_acceleration, jerk_extreme_with_time, sustained_jerk
import powertrain_utils

sys.path.insert(0, str(Path.cwd().parent / "J6B_UnifiedMining"))
spec = importlib.util.spec_from_file_location("j6b_metric_utils", Path.cwd().parent / "J6B_UnifiedMining" / "metric_utils.py")
j6b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = j6b
spec.loader.exec_module(j6b)
sys.path.pop(0)
rng = np.random.default_rng(77)
local = np.cumsum(rng.uniform(0.005, 0.015, 10000))
cases = [
    local, local + 1_700_000_000,
    np.r_[np.arange(0, 5, 0.01), 1_000_000 + np.arange(0, 5, 0.01)],
    np.array([]), np.array([0.0, 0.01]),
]
for t in cases:
    a = np.sin(t) + 0.01 * np.cos(100 * t)
    if a.size > 200:
        a[100:120] = np.nan
    expected = j6b.jerk_from_acceleration(t, a)
    np.testing.assert_allclose(jerk_from_acceleration(t, a), expected, atol=1e-10, equal_nan=True)
    smooth, jerk = sustained_jerk(t.tolist(), a.tolist())
    assert len(smooth) == len(jerk) == t.size
    np.testing.assert_allclose(jerk, expected, equal_nan=True)

t = np.arange(1, 5.001, 0.01)
a = np.sin(2 * t)
jerk = jerk_from_acceleration(t, a)
pairs = list(zip(a.tolist(), (t * 1000).tolist()))
for name, mode in (
    ("follow_stop_min_jerk", "min"), ("redlight_min_jerk", "min"),
    ("curvature_min_jerk", "min"), ("follow_start_jerk", "max"),
    ("stop2go_max_jerk", "max"), ("slope_no_frnt_jerk", "max"),
    ("traffic_light_stop2go_max_jerk", "max"),
):
    module = importlib.import_module("metrics.topic.code." + name)
    function = getattr(module, "calculate_" + mode + "_smoothed_jerk_with_time")
    value, when_ms = function(pairs)
    finite = np.flatnonzero(np.isfinite(jerk))
    index = finite[(np.argmin if mode == "min" else np.argmax)(jerk[finite])]
    np.testing.assert_allclose(value, jerk[index], atol=1e-8)
    np.testing.assert_allclose(when_ms, t[index] * 1000)
    # Sorting and last duplicate win, while invalid points are not measurements.
    dirty = [(999., pairs[-1][1])] + pairs[::-1] + [(None, 42), (float("nan"), 50)]
    np.testing.assert_allclose(function(dirty), function(pairs), atol=1e-8)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert function([(1, 1000), (2, 1001), (3, 1002)]) is None
        assert any("Insufficient temporal support" in str(w.message) for w in caught)
for windows in ((7,), (5, 3)):
    try:
        sustained_jerk(t, a, *windows)
    except ValueError:
        pass
    else:
        raise AssertionError("Non-default sample windows silently accepted")
''')

    def test_mpi_routes_use_windowed_jerk(self):
        self.run_mmt(r'''
import importlib
from unittest.mock import patch
import numpy as np
from signal_lib import FolderSession
from jerk_utils import sustained_jerk, jerk_from_acceleration, time_window_linear_slope

t = np.arange(0, 10.001, 0.01)
a = -1 + 0.4 * np.sin(3 * t)
class SyntheticSession(FolderSession):
    def __init__(self):
        pass
    def common_time_range(self, signals):
        return float(t[0]), float(t[-1])
    def get_signal_slice(self, signal, start, end):
        if "Gear" in signal or "Shft" in signal:
            return t.tolist(), np.where(t < 2, 12, 14).tolist()
        return t.tolist(), a.tolist()
    def sample_signal_to_axis(self, signal, axis, **kwargs):
        if "IAccPdlOvrrdAtv" in signal:
            return (np.asarray(axis) < 1).tolist()
        if "APASts" in signal:
            return [5] * len(axis)
        if "Gear" in signal or "Shft" in signal:
            return np.where(np.asarray(axis) < 2, 12, 14).tolist()
        return [1] * len(axis)

session = SyntheticSession()
for name, function in (
    ("acc_jerkiness", "count_acc_jerkiness_events"),
    ("apa_jerkiness", "count_apa_jerkiness_events"),
    ("apa_gear_switch_jerkiness", "analyze_apa_gear_switch_jerkiness"),
    ("acc_heavy_braking_count", "count_acc_heavy_braking_events"),
    ("acc_heavy_braking_after_override_release_count", "count_acc_heavy_braking_after_override_release_events"),
):
    module = importlib.import_module("metrics.can.code." + name)
    with patch.object(module, "sustained_jerk", wraps=sustained_jerk) as calculate:
        result = getattr(module, function)(session)
        calculate.assert_called_once()
        np.testing.assert_allclose(calculate.call_args.args[0], t)
        np.testing.assert_allclose(calculate.call_args.args[1], a)
    assert result["parameters"]["jerk_regression_window_s"] == 0.3
    assert result["parameters"]["acceleration_smoothing_window_s"] == 0.3
    if "gear_switch" in name:
        assert len(result["scenes"]) == 1

# Read actual CAN-format H5 through FolderSession, not only a stubbed session.
import h5py
import tempfile
routes = [
    ("acc_jerkiness", "count_acc_jerkiness_events"),
    ("apa_jerkiness", "count_apa_jerkiness_events"),
    ("apa_gear_switch_jerkiness", "analyze_apa_gear_switch_jerkiness"),
    ("acc_heavy_braking_count", "count_acc_heavy_braking_events"),
    ("acc_heavy_braking_after_override_release_count", "count_acc_heavy_braking_after_override_release_events"),
]
modules = [importlib.import_module("metrics.can.code." + name) for name, _ in routes]
signals = {signal for module in modules for signal in module.REQUIRED_SIGNALS}
by_message = {}
for signal in signals:
    message, field = signal.split(".", 1)
    if signal in {module.SIGNAL_KEYS["acceleration"] for module in modules}:
        values = a
    else:
        values = session.sample_signal_to_axis(signal, t.tolist())
    by_message.setdefault(message, {})[field] = values
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "synthetic_can.h5"
    with h5py.File(path, "w") as h5:
        channel = h5.create_group("sample").create_group("Channel1")
        for message, fields in by_message.items():
            data = np.zeros(t.size, dtype=[("timestamp", "f8")] + [(field, "f8") for field in fields])
            data["timestamp"] = t
            for field, values in fields.items():
                data[field] = values
            channel.create_dataset(message, data=data)
    real_session = FolderSession(path)
    for module, (_, function) in zip(modules, routes):
        result = getattr(module, function)(real_session)
        assert result["parameters"]["jerk_regression_window_s"] == 0.3

series = {
    "axis": t.tolist(), "acceleration": a.tolist(),
    "speed": [10] * len(t), "acc_active": [True] * len(t),
    "pedal_override": [False] * len(t), "primary_end_seconds": float(t[-1]),
    "valid_frame_count": len(t), "segment_start_ms": 0.0,
}
for name, function in (
    ("acc_jerkiness", "count_topic_acc_jerkiness_events"),
    ("acc_heavy_braking_count", "count_topic_acc_heavy_braking_events"),
):
    module = importlib.import_module("metrics.topic.code." + name)
    with patch.object(module, "extract_topic_acc_series", return_value=series):
        with patch.object(module, "sustained_jerk", wraps=sustained_jerk) as calculate:
            result = getattr(module, function)([])
            calculate.assert_called_once()
            np.testing.assert_allclose(calculate.call_args.args[1], a)

# Torque confirmation uses raw acceleration through the shared jerk algorithm,
# while its separate high-pass and torque-rate filters stay unchanged.
module = importlib.import_module("metrics.can.code.acc_jerkiness")
import inspect
parameters = inspect.signature(module.count_acc_jerkiness_events).parameters
kwargs = {key: value.default for key, value in parameters.items()
          if key in inspect.signature(module._filter_events_by_torque_match).parameters}
with patch.object(module, "jerk_from_acceleration", wraps=jerk_from_acceleration) as calculate:
    module._filter_events_by_torque_match(
        axis=t.tolist(), base_events=[], acceleration_values=a.tolist(),
        drive_torque_values=[0] * len(t), brake_torque_values=[0] * len(t), **kwargs,
    )
    calculate.assert_called_once()
    np.testing.assert_allclose(calculate.call_args.args[1], a)
''')


if __name__ == "__main__":
    unittest.main()
