from __future__ import annotations

import time
from typing import Any

from metrics.can.acc_exit_correlation import find_correlated_acc_exits


METRIC_CATEGORY = "MPI"
ACC_ACTIVE_SIGNAL = "Adaptive_Cruise_Disp_Stat.IACCAct376"
VSE_ACTIVE_SIGNAL = "PPEI_Chassis_General_Data_3_0AC_M.IVSEAct"
REQUIRED_SIGNALS = [ACC_ACTIVE_SIGNAL, VSE_ACTIVE_SIGNAL]


def __run_haoran_data_run__(folder_context: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    start_time = time.time()
    result_lines: list[str] = []
    stats: dict[str, Any] = {"All Case Count": 0, "Bad Case": 0, "Severity Score": 0.0}
    session = folder_context.get("session")
    if session is None:
        result_lines.append("当前文件夹下未找到可处理的H5文件")
        result_lines.insert(0, f"【运算总耗时】: {time.time() - start_time:.4f} 秒")
        return False, result_lines, stats

    start_timestamp, end_timestamp = session.common_time_range(REQUIRED_SIGNALS)
    axis, acc_values = session.get_signal_slice(ACC_ACTIVE_SIGNAL, start_timestamp, end_timestamp)
    vse_values = session.sample_signal_to_axis(
        VSE_ACTIVE_SIGNAL, axis, method="nearest",
        start_timestamp=start_timestamp, end_timestamp=end_timestamp,
    )
    events = find_correlated_acc_exits(axis, acc_values, vse_values)
    _append_event_results(result_lines, stats, "VSE", events)
    result_lines.insert(0, f"【运算总耗时】: {time.time() - start_time:.4f} 秒")
    return bool(events), result_lines, stats


def _append_event_results(
    result_lines: list[str],
    stats: dict[str, Any],
    feature_name: str,
    events: list[tuple[float, float]],
) -> None:
    delays = [exit_time - trigger_time for trigger_time, exit_time in events]
    stats["All Case Count"] = len(events)
    stats["Bad Case"] = len(events)
    stats["Severity Score"] = round(max(delays, default=0.0), 3)
    result_lines.append(
        f"[ACC Exit After {feature_name}] all_case_count={len(events)}, "
        f"bad_case_count={len(events)}, severity_score={stats['Severity Score']:.3f}"
    )
    for trigger_time, exit_time in events:
        result_lines.append(
            f"[Event trigger_time={trigger_time:.3f}, acc_exit_time={exit_time:.3f}, "
            f"delay={exit_time - trigger_time:.3f}]"
        )
