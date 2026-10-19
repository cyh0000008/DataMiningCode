# acc_override_brake_request_jerkiness 指标说明

## 1. 检查对象

检查 ACC 仍然 active、驾驶员处于油门 override 时的纵向顿挫。

不再限制制动请求加速度，也不要求实际加速度为负；满足场景条件后使用共用顿挫算法及扭矩校验。指标内部名称保留，以兼容历史报告。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于顿挫识别。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员油门 override 状态。
- `PPEI_Engine_General_Status_1_082_M2.IAccActPos`
  油门踏板位置。

此外需要共用算法中的车速、实际驱动/制动扭矩及其有效位。完整依赖以代码里的 `REQUIRED_SIGNALS` 为准，不再依赖制动请求加速度信号。

## 3. 有效样本条件

先取所需信号的公共时间段。每个采样点必须同时满足以下条件，才参与顿挫检测：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == true`
- `IAccActPos > 0`
- `IVehSpdAvgDrvn_1 > 0`

对应到工程含义：

- ACC 没有退出
- 驾驶员处于油门 override
- 油门踏板位置大于 0
- 车辆正在行驶

斜率计算依赖相邻采样点，所以候选点要求当前点和前一个点都满足以上条件。

制动请求为 0、正值或负值均不会因此过滤；缺失该信号也不会导致本指标跳过。

## 4. 顿挫识别

顿挫算法复用 `acc_jerkiness.py` 的判定逻辑，包含三类检测：

- 短时振荡
- 长周期交替加减速
- 减速度短时跳变

任意一类命中，都记为一次顿挫事件。

## 5. 主要参数

- `smoothing_window = 5`
- `deadband = 0.8`
- `min_oscillation_amplitude = 0.75`
- `min_sign_flips = 1`
- `amplitude_window_seconds = 1.00`
- `min_event_duration_seconds = 1.00`
- `max_event_duration_seconds = 4.00`
- `merge_gap_seconds = 0.30`
- `long_pattern_min_phase_seconds = 1.5`
- `long_pattern_max_phase_seconds = 3.5`
- `long_pattern_min_phase_amplitude = 0.30`
- `long_pattern_min_phase_count = 4`
- `long_pattern_max_gap_seconds = 0.60`
- `decel_jump_window_seconds = 0.80`
- `decel_jump_min_drop = 0.50`
- `decel_jump_min_rebound = 0.50`
- 高频加速度峰值 ≥0.4 m/s²，RMS ≥0.10 m/s²。
- 正 jerk 附近驱动扭矩上升速率 ≥1500 Nm/s，负 jerk 附近制动扭矩上升速率 ≥2500 Nm/s。

## 6. 输出字段

事件级输出：

- `detection_type`
- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `amplitude`
- `sign_flips`
- `severity`

汇总字段：

- `All Case Count`：检出的顿挫事件数
- `Bad Case`：检出的顿挫事件数
- `Severity Score`：该文件夹内最严重顿挫事件的严重度

这个指标按事件统计。检出的顿挫事件本身就是 bad case，所以 `All Case Count` 和 `Bad Case` 目前保持一致。

## 7. 边界说明

- 不再按制动请求加速度或实际加速度的正负筛选场景。
- 场景要求 ACC active、override、油门 >0、车速 >0。
- 顿挫口径跟 `acc_jerkiness` 保持一致，后续如果调整基础顿挫算法，这里也应同步确认。
