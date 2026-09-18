# acc_jerkiness 指标说明

## 1. 检查对象

检查 ACC active、车辆行驶且驾驶员没有 override 时的纵向顿挫。

这里不按单个瞬时 jerk 超阈值判定，而是看短时间内纵向加速度的振荡、长周期交替加减速，以及减速度短时跳变。这样比单点阈值更抗噪，也更接近实际体感顿挫。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于顿挫识别。
- `PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1`
  车辆速度，用于过滤静止状态。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员油门 override 状态。

## 3. 有效样本条件

单点有效条件：

- `IAccPdlOvrrdAtv == false`
- `IACCAct376 == true`
- `IVehSpdAvgDrvn_1 > 0`

斜率计算依赖相邻采样点，所以候选点要求当前点和前一个点都满足有效样本条件。

## 4. 基础计算

纵向加速度先做滑动平均：

- `smoothing_window = 5`

再计算平滑后相邻点斜率：

- `slope = delta_acc / delta_t`

如果相邻点 `dt <= 0`，该点斜率按 `0` 处理。

## 5. 短时振荡分支

斜率按死区转换为三态：

- `1`：`slope > deadband`
- `-1`：`slope < -deadband`
- `0`：落在死区内

默认：

- `deadband = 0.8`

在连续有效区间内统计斜率符号换向次数，并用滑动短窗计算加速度峰谷差：

- `amplitude_window_seconds = 1.00`
- `amplitude = 1s 短窗内 max(acc) - min(acc) 的最大值`

短时振荡事件需要满足：

- `sign_flips >= min_sign_flips`
- `amplitude >= min_oscillation_amplitude`
- `min_event_duration_seconds <= duration <= max_event_duration_seconds`

默认：

- `min_sign_flips = 1`
- `min_oscillation_amplitude = 0.75`
- `min_event_duration_seconds = 1.00`
- `max_event_duration_seconds = 4.00`

如果连续振荡段超过 `max_event_duration_seconds`，不会直接丢弃；代码会在长段内滑动搜索局部子窗口，保留严重度最高的那一段。

## 6. 长周期交替分支

该分支补充识别“加速一段、减速一段、再加速、再减速”的低频顿挫。

单个相位需要满足：

- `duration >= long_pattern_min_phase_seconds`
- `duration <= long_pattern_max_phase_seconds`
- `phase_amplitude >= long_pattern_min_phase_amplitude`

相邻相位要求：

- 符号交替
- 相位间隔不超过 `long_pattern_max_gap_seconds`

至少需要 `long_pattern_min_phase_count = 4` 个相位。

默认：

- `long_pattern_min_phase_seconds = 1.5`
- `long_pattern_max_phase_seconds = 3.5`
- `long_pattern_min_phase_amplitude = 0.30`
- `long_pattern_min_phase_count = 4`
- `long_pattern_max_gap_seconds = 0.60`

## 7. 减速度跳变分支

从某个有效采样点开始，向后搜索一个短窗口。如果加速度在窗口内明显下降，并且终点进入明显减速区间，记为减速度跳变顿挫。

判定条件：

- 搜索窗口不超过 `decel_jump_window_seconds`
- 加速度下降量 `drop >= decel_jump_min_drop`
- 终点加速度 `acc <= -decel_jump_min_peak_decel`

默认：

- `decel_jump_window_seconds = 0.80`
- `decel_jump_min_drop = 0.80`
- `decel_jump_min_peak_decel = 0.60`

## 8. 事件合并

三类分支检出的事件统一按时间合并。事件重叠，或间隔不超过以下阈值时，合并为一个最终事件：

- `merge_gap_seconds = 0.30`

合并后重新计算时间范围、持续时间、样本数、幅值、换向次数、严重度和 `detection_type`。

## 9. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `amplitude`
- `sign_flips`
- `severity`
- `detection_type`

## 10. 严重度和统计口径

严重度：

- `amplitude_ratio = amplitude / min_oscillation_amplitude`
- `flip_ratio = sign_flips / min_sign_flips`
- `duration_ratio = duration / 1.0`
- `severity = amplitude_ratio * 50 + flip_ratio * 30 + duration_ratio * 20`

统计字段：

- `All Case Count`：检出的顿挫事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最大单事件严重度

## 11. 输出示例

```text
[Event start=48.682, end=51.452, duration=2.770, sample_count=269, amplitude=0.750, sign_flips=52, severity=985.397]
```

## 12. 边界说明

- ACC 未开启、override 存在或车速为 0 时，不形成有效候选点。
- 该指标关注连续行为，不把单个瞬时尖峰直接当作顿挫。
- 严重度是当前工程评分方式，后续可按主观评价结果重新标定权重。
