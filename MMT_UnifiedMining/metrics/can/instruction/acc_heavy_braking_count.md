# acc_heavy_braking_count 指标说明

## 1. 检查对象

检查 ACC active 且驾驶员没有 override 时的重制动事件。

重制动不是只看纵向加速度为负，而是同时要求减速度足够深、持续时间足够长，并且过程中出现明显负 jerk。这样可以把平缓减速、单点毛刺和高频噪声排除掉。

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

重制动候选点条件：

- 有效样本
- `acceleration < -2.0`

连续候选点合并为候选段。候选段必须满足：

- `duration >= 0.20s`
- 段内 `filtered_jerk < -3.0`

满足后记为 1 个重制动事件。

## 5. 事件合并

相邻重制动事件间隔不超过以下阈值时合并：

- `merge_gap_seconds = 0.50`

合并后重新计算时间范围、持续时间、样本数、最小加速度、最小滤波 jerk 和严重度。

## 6. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `min_acceleration`
- `min_filtered_jerk`
- `severity`

## 7. 严重度和统计口径

严重度按加速度和 jerk 超阈值程度加权：

- `accel_excess = max(abs(min_acceleration) - abs(heavy_brake_accel_threshold), 0)`
- `jerk_excess = max(abs(min_filtered_jerk) - abs(heavy_brake_jerk_threshold), 0)`
- `severity = accel_excess * 60 + jerk_excess * 40`

统计字段：

- `All Case Count`：检出的重制动事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最大单事件严重度

## 8. 默认参数

- `acceleration_smoothing_window = 5`
- `jerk_smoothing_window = 5`
- `heavy_brake_accel_threshold = -2.0`
- `heavy_brake_jerk_threshold = -3.0`
- `min_brake_duration_seconds = 0.20`
- `merge_gap_seconds = 0.50`

## 9. 输出示例

```text
[Event start=35.120, end=35.480, duration=0.360, sample_count=5, min_acceleration=-2.430, min_filtered_jerk=-3.610, severity=43.400]
```

## 10. 边界说明

- jerk 由平滑后的纵向加速度计算，并再次平滑，用来抑制单点尖峰。
- “重制动次数”按事件段统计，不按采样点统计。
- 很近的重制动事件会合并，避免一个完整制动过程被重复计数。
