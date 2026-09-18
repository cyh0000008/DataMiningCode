# acc_start_no_override_jerkiness 指标说明

## 1. 检查对象

检查 ACC 刚开启后的 1 秒窗口。只看 ACC 成功进入 active、且这 1 秒内驾驶员没有 override 的场景。

统计结果关注两件事：

- 有多少个有效的 ACC 开启窗口
- 这些窗口里有多少个出现了纵向顿挫

这里的 bad case 按“窗口”计数，不按顿挫事件计数。一个 1 秒窗口内即使出现多次顿挫，也只记 1 个 bad case。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于顿挫识别。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态，用于找 ACC 开启点，并确认开启后 1 秒内 ACC 没有退出。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员油门 override 状态，用于过滤开启后被驾驶员接管的窗口。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 3 个信号为准。

## 3. 窗口定义

先取 3 个信号的公共时间段。

ACC 开启点定义为 `IACCAct376` 的上升沿：

- 前一个采样点：`false`
- 当前采样点：`true`

以上升沿当前采样点作为窗口起点，向后取完整 1 秒：

- `start_window_seconds = 1.0`

如果数据长度不够 1 秒，这次开启不计入统计。

## 4. 有效窗口条件

窗口内必须全程满足：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`

也就是 ACC 开启后 1 秒内没有退出，也没有驾驶员 override。

当前口径不加车速条件。这样做是为了严格贴合“ACC 开启 1s 内且开启后无 override”这个需求，避免额外过滤导致漏看开启瞬间的问题。

## 5. 顿挫识别

窗口内的顿挫算法复用 `acc_jerkiness.py` 的判定逻辑，包含三类检测：

- 短时振荡
- 长周期交替加减速
- 减速度短时跳变

斜率计算依赖相邻采样点，所以参与顿挫判断的候选点要求当前点和前一个点都在有效窗口内。

## 6. 主要参数

- `start_window_seconds = 1.0`
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
- `decel_jump_min_drop = 0.80`
- `decel_jump_min_peak_decel = 0.60`

## 7. 输出字段

场景级输出：

- `acc_start_timestamp`
- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `event_count`
- `severity`

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

- `All Case Count`：有效 ACC 开启窗口数
- `Bad Case`：1 秒内出现顿挫的窗口数
- `Severity Score`：该文件夹内最严重顿挫事件的严重度

## 8. 边界说明

- 只统计完整 1 秒窗口，数据尾部不足 1 秒的开启点不统计。
- 窗口内只要 ACC 退出或 override 变为 true，该窗口直接丢弃。
- 顿挫口径跟 `acc_jerkiness` 保持一致，后续如果调整基础顿挫算法，这里也应同步确认。
