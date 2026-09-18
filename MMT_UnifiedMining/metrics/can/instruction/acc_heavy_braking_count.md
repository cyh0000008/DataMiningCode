# acc_heavy_braking_count 指标说明

## 1. 检查对象

检查 ACC active 且驾驶员没有 override 时的重制动事件。

重制动不是只看纵向加速度为负，而是同时检查减速度深度、跳变量、变化斜率、持续时间和负 jerk。主规则用于识别典型重制动，深短尖峰和深持续制动补充规则用于避免漏掉明显的异常制动。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于识别重制动并计算 jerk。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员 override 状态。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 3 个信号为准。

## 3. 有效样本条件

先取 3 个信号的公共时间段。

单点有效条件：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`

当前不额外要求车速大于 0，只按 ACC active 和无 override 过滤。

## 4. 重制动判定

加速度先做滑动平均：

- `acceleration_smoothing_window = 5`

然后基于平滑后的加速度计算 jerk：

- `jerk = delta_acc / delta_t`

如果相邻采样点 `dt <= 0`，该点 jerk 按 `0` 处理。jerk 序列再做一次滑动平均：

- `jerk_smoothing_window = 5`

主规则必须同时满足：

- 在 `1.60s` 窗口内，加速度降幅 `>= 3.0m/s2`
- 窗口内减速变化斜率 `>= 3.0m/s3`
- 加速度 `<= -2.0m/s2` 的重制动区间持续 `>= 0.20s`
- 终点滤波 jerk `<= -2.0m/s3`

主规则未命中时，以下补充规则任一命中也记为重制动：

- 深短尖峰：加速度 `<= -2.7m/s2`，降幅 `>= 2.6m/s2`，重制动驻留 `<= 0.12s`，最小滤波 jerk `<= -10.0m/s3`
- 深持续制动：加速度 `<= -2.7m/s2`，降幅 `>= 1.3m/s2`，重制动驻留 `>= 0.35s`，最小滤波 jerk `<= -5.0m/s3`

补充规则中的“重制动驻留”从加速度进入 `<= -1.8m/s2` 区间开始计算。

## 5. 事件合并

相邻重制动事件间隔不超过以下阈值时合并：

- `merge_gap_seconds = 0.50`

合并后重新计算时间范围、持续时间、样本数、最小加速度、减速度降幅、斜率、最小滤波 jerk 和严重度。

## 6. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `start_acceleration`
- `min_acceleration`
- `decel_drop`
- `decel_slope`
- `heavy_brake_dwell_seconds`
- `min_filtered_jerk`
- `detection_type`
- `severity`

## 7. 严重度和统计口径

严重度综合加速度、滤波 jerk、减速度降幅和斜率相对各自阈值的超出程度计算：

```text
severity = accel_excess * 30 + jerk_excess * 20 + drop_excess * 30 + slope_excess * 20
```

统计字段：

- `All Case Count`：检出的重制动事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最大单事件严重度

## 8. 默认参数

- `acceleration_smoothing_window = 5`
- `jerk_smoothing_window = 5`
- `heavy_brake_accel_threshold = -2.0`
- `heavy_brake_jerk_threshold = -2.0`
- `decel_jump_window_seconds = 1.60`
- `decel_jump_min_drop = 3.0`
- `decel_jump_min_slope = 3.0`
- `heavy_brake_min_dwell_seconds = 0.20`
- `deep_spike_accel_threshold = -2.7`
- `deep_spike_min_drop = 2.6`
- `deep_spike_max_dwell_seconds = 0.12`
- `deep_spike_min_negative_jerk = -10.0`
- `deep_sustained_accel_threshold = -2.7`
- `deep_sustained_min_drop = 1.3`
- `deep_sustained_min_dwell_seconds = 0.35`
- `deep_sustained_min_negative_jerk = -5.0`
- `merge_gap_seconds = 0.50`

## 9. 输出示例

```text
[Event start=35.120, end=35.480, duration=0.360, sample_count=5, start_acceleration=0.800, min_acceleration=-2.430, decel_drop=3.230, decel_slope=8.972, heavy_brake_dwell_seconds=0.220, min_filtered_jerk=-3.610, type=primary_decel_jump, severity=43.400]
```

## 10. 边界说明

- jerk 由平滑后的纵向加速度计算，并再次平滑，用来抑制单点尖峰。
- “重制动次数”按事件段统计，不按采样点统计。
- 很近的重制动事件会合并，避免一个完整制动过程被重复计数。
