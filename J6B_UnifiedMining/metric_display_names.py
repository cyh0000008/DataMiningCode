from __future__ import annotations

import warnings


METRIC_DISPLAY_NAMES = {
    "stop2go_max_acc": "起步最大加速度",
    "stop2go_max_jerk": "起步最大jerk",
    "stop2go_start_time": "初段建立时间",
    "stop2go_max_acc_time": "起步加速建立时间",
    "curvature_min_speed": "弯道安全速度",
    "curvature_min_acc": "弯道限速最大减速度",
    "curvature_min_jerk": "弯道限速减速舒适性",
    "slope_no_frnt_vel_fluct": "坡道稳速抖动",
    "slope_no_frnt_acc": "坡道控速稳定性",
    "slope_no_frnt_jerk": "坡道控速舒适性",
    "follow_stop_max_acc": "跟车刹停最大减速度",
    "follow_stop_min_frntobj_dis": "跟车刹停距离",
    "follow_stop_min_jerk": "跟车刹停舒适性",
    "follow_stop_ttc": "跟车刹停反应",
    "follow_stop_min_ttc_frntobj_dec": "跟车刹停风险",
    "follow_start_latency": "跟车起步响应时延",
    "follow_start_jerk": "跟车起步舒适性",
    "acc_rollback_count": "溜车",
    "acc_set_speed_reach": "无前车稳速未达到SetSpeed",
    "acc_unreasonable_low_speed_count": "不合理低速",
    "acc_brake_tap_count": "点刹",
    "acc_heavy_braking_count": "重制动",
    "acc_torque_jerkiness_count": "顿挫",
    "acc_jerkiness_after_override_release": "Override后顿挫",
    "acc_heavy_braking_after_override_release_count": "Override后重制动",
    "acc_lost_arbitration": "行车断握手",
    "acc_override_brake_request_jerkiness": "进Override后顿挫",
    "acc_start_no_override_jerkiness": "起步顿挫",
    "tcs_activation_count": "TCS激活",
    "vse_activation_count": "VSE激活",
}


def is_retired_metric(metric_name: str) -> bool:
    return (metric_name.startswith(("acc_exit_after_", "driving_uninterfered_accel_"))
            or metric_name == "acc_brake_rapid_toggle_decel_jump_count")


def metric_display_name(metric_name: str) -> str:
    if metric_name in METRIC_DISPLAY_NAMES:
        return METRIC_DISPLAY_NAMES[metric_name]
    warnings.warn(f"指标缺少中文名称映射：{metric_name}", UserWarning, stacklevel=2)
    return f"未配置中文名称（{metric_name}）"
