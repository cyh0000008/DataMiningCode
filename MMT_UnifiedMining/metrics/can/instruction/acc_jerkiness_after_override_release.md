# acc_jerkiness_after_override_release 指标说明

## 1. 检查对象

检查驾驶员油门 override 结束后的 5 秒窗口，判断纵向功能接管回来以后是否出现顿挫。

该指标按窗口统计 bad case：

- 先找有效的 override 结束窗口
- 再看窗口内是否至少出现 1 次顿挫
- 一个窗口内出现多次顿挫，仍只记 1 个 bad case

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于顿挫识别。
- `PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1`
  车辆速度，用于过滤静止状态。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态，用于确认窗口内纵向功能保持开启。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员油门 override 状态，用于定位 override 结束点，并确认窗口内没有再次 override。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 4 个信号为准。

## 3. 窗口定义

先取 4 个信号的公共时间段。

override 结束点定义为 `IAccPdlOvrrdAtv` 的下降沿：

- 前一个采样点：`true`
- 当前采样点：`false`

以下降沿当前采样点作为窗口起点，向后取完整 5 秒：

- `post_override_window_seconds = 5.0`

窗口必须完整存在，且窗口内必须全程满足：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`

数据不足 5 秒、ACC 退出、或驾驶员再次 override 的窗口都不参与统计。

## 4. 有效样本条件

窗口内参与顿挫检测的采样点还需要满足：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`
- `IVehSpdAvgDrvn_1 > 0`

斜率计算依赖相邻采样点，所以候选点要求当前点和前一个点都满足有效样本条件。

## 5. 基础计算

纵向加速度先做滑动平均：

- `smoothing_window = 5`

再计算平滑后相邻点斜率：

- `slope = delta_acc / delta_t`

如果相邻点 `dt <= 0`，该点斜率按 `0` 处理。

## 6. 顿挫识别

顿挫判定口径与 `acc_jerkiness` 保持一致，三类分支并联：

- 短时振荡
- 长周期交替加减速
- 减速度短时跳变

任意一类命中，都输出为顿挫事件。

## 7. 短时振荡分支

斜率按死区转换为三态：

- `1`：`slope > deadband`
- `-1`：`slope < -deadband`
- `0`：落在死区内

默认：

- `deadband = 0.8`

在连续候选区间内统计换向次数，并用 1 秒滑动窗计算加速度峰谷差：

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

如果连续振荡段超过 4 秒，代码会在长段内滑动搜索局部子窗口，保留严重度最高的那一段。

短空洞合并阈值：

- `merge_gap_seconds = 0.30`

## 8. 长周期交替分支

该分支补充识别“加速一段、减速一段、再加速、再减速”的低频顿挫。

单个相位要求：

- `duration >= long_pattern_min_phase_seconds`
- `duration <= long_pattern_max_phase_seconds`
- `phase_amplitude >= long_pattern_min_phase_amplitude`

相邻相位要求：

- 符号交替
- 相位间隔不超过 `long_pattern_max_gap_seconds`

最少相位数：

- `long_pattern_min_phase_count = 4`

默认：

- `long_pattern_min_phase_seconds = 1.5`
- `long_pattern_max_phase_seconds = 3.5`
- `long_pattern_min_phase_amplitude = 0.30`
- `long_pattern_max_gap_seconds = 0.60`

## 9. 减速度跳变分支

从某个有效采样点开始，在短窗口内向后搜索。如果加速度短时间内明显下降，且终点进入明显减速区间，记为减速度跳变顿挫。

判定条件：

- 搜索窗口不超过 `decel_jump_window_seconds`
- 加速度下降量 `drop >= decel_jump_min_drop`
- 终点加速度 `acc <= -decel_jump_min_peak_decel`

默认：

- `decel_jump_window_seconds = 0.80`
- `decel_jump_min_drop = 0.80`
- `decel_jump_min_peak_decel = 0.60`

## 10. 事件合并

三类分支检出的事件统一按时间合并。事件重叠，或间隔不超过以下阈值时，合并为一个最终事件：

- `merge_gap_seconds = 0.30`

合并后重新计算：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `amplitude`
- `sign_flips`
- `severity`
- `detection_type`

`detection_type` 可能是 `oscillation`、`long_cycle`、`decel_jump`，也可能是多个类型组合。

## 11. 输出字段

场景级输出：

- `release_timestamp`
- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `event_count`
- `severity`

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `amplitude`
- `sign_flips`
- `severity`
- `detection_type`

## 12. 严重度和统计口径

单事件严重度：

- `amplitude_ratio = amplitude / min_oscillation_amplitude`
- `flip_ratio = sign_flips / min_sign_flips`
- `duration_ratio = duration / 1.0`
- `severity = amplitude_ratio * 50 + flip_ratio * 30 + duration_ratio * 20`

单窗口严重度取该窗口内最大单事件严重度。

统计字段：

- `All Case Count`：有效 override 结束窗口数
- `Bad Case`：窗口内至少出现 1 次顿挫的窗口数
- `Severity Score`：所有 bad case 窗口中的最大单事件严重度

## 13. 默认参数

- `post_override_window_seconds = 5.0`
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

## 14. 输出示例

```text
[Scene release=102.300, start=102.300, end=107.300, duration=5.000, event_count=1, severity=116.400]
[Event type=decel_jump+oscillation, start=103.100, end=104.600, duration=1.500, sample_count=16, amplitude=1.420, sign_flips=2, severity=116.400]
```

## 15. 边界说明

- 完整 5 秒窗口是硬条件，不足 5 秒不统计。
- 窗口内 ACC 必须一直 active，override 必须一直 false。
- 顿挫判定口径与 `acc_jerkiness` 一致，但当前代码是独立维护的；后续改基础顿挫算法时，需要同步检查这里的三类分支。
