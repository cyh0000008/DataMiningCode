# acc_heavy_braking_after_override_release_count 指标说明

## 1. 检查对象

检查驾驶员 override 结束后的 5 秒窗口，统计窗口内出现的 ACC 重制动事件。

重制动口径沿用 `acc_heavy_braking_count`，这里只是把分析范围限定到 override 释放后的固定窗口。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于识别重制动和计算 jerk。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态，用于确认窗口内 ACC 没有退出。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员 override 状态，用于定位 override 结束点，并过滤再次 override 的窗口。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 3 个信号为准。

## 3. 窗口定义

override 结束点定义为 `IAccPdlOvrrdAtv` 的下降沿：

- 前一个采样点：`true`
- 当前采样点：`false`

以下降沿当前采样点作为窗口起点，向后取完整 5 秒：

- `post_override_window_seconds = 5.0`

窗口必须完整存在，且窗口内必须全程满足：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`

数据不足 5 秒、ACC 退出、或驾驶员再次 override 的窗口都不参与统计。

## 4. 重制动判定

在有效 5 秒窗口内，重制动事件需要同时满足：

- 纵向加速度 `< -2.0`
- 该状态持续至少 `0.20s`
- 同一候选段内滤波后 jerk 出现 `< -3.0`

相邻重制动事件间隔不超过 `merge_gap_seconds = 0.50` 时会合并。

## 5. 统计口径

- `All Case Count`：override 结束后 5 秒内识别出的重制动事件数
- `Bad Case`：同 `All Case Count`，重制动事件本身就是 bad case
- `Severity Score`：该文件夹内最严重重制动事件的严重度

该指标按事件数统计，不按 override 窗口数统计。一个 5 秒窗口内出现多个重制动事件时会分别计数。

## 6. 输出字段

场景级输出：

- `release_timestamp`
- `start_timestamp`
- `end_timestamp`
- `event_count`
- `severity`

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `min_acceleration`
- `min_filtered_jerk`
- `severity`

## 7. 默认参数

- `post_override_window_seconds = 5.0`
- `acceleration_smoothing_window = 5`
- `jerk_smoothing_window = 5`
- `heavy_brake_accel_threshold = -2.0`
- `heavy_brake_jerk_threshold = -3.0`
- `min_brake_duration_seconds = 0.20`
- `merge_gap_seconds = 0.50`

## 8. 边界说明

- 只统计完整 5 秒窗口。
- 窗口内只要 ACC 退出或 override 重新变为 true，该窗口直接跳过。
- 重制动判定口径应与 `acc_heavy_braking_count` 保持同步。
