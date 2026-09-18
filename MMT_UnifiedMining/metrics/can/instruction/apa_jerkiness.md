# apa_jerkiness 指标说明

## 1. 检查对象

检查 APA 场景期间的纵向顿挫。

顿挫判定口径与 `acc_jerkiness` 保持一致，只是有效样本从 ACC 场景切换为 APA 场景。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于顿挫识别。
- `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType`
  APA 类型。
- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth`
  APA 泊车状态。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 3 个信号为准。

当前 APA 场景按以下条件识别：

- `IAPABT_APAType == 1`
- `IAPASP_APAStsAuth == 5 或 6`

## 3. 判定逻辑

先取 3 个信号的公共时间段。

单点有效条件：

- `IAPABT_APAType == 1`
- `IAPASP_APAStsAuth == 5 或 6`

加速度先做滑动平均：

- `smoothing_window = 5`

再基于平滑后的加速度计算相邻点斜率：

- `slope = delta_acc / delta_t`

斜率依赖相邻采样点，所以候选点要求当前点和前一个点都处于 APA 场景。

## 4. 顿挫识别

检测逻辑与 `acc_jerkiness` 同步，三类分支并联：

- 短时振荡顿挫
- 长周期交替加减速
- 减速度短时跳变

不同分支检出的事件会按时间做并集合并，短空洞合并阈值为：

- `merge_gap_seconds = 0.30`

## 5. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `amplitude`
- `sign_flips`
- `severity`
- `detection_type`

严重度与 `acc_jerkiness` 一致，由幅值、换向次数和持续时间加权得到。

## 6. 统计口径

- `All Case Count`：检出的 APA 顿挫事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最严重 APA 顿挫事件的严重度

## 7. 默认参数

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
- `apa_type_values = [1]`
- `apa_status_values = [5, 6]`

## 8. 输出示例

```text
[Event type=oscillation, start=48.682, end=51.452, duration=2.770, sample_count=269, amplitude=0.750, sign_flips=52, severity=985.397]
```

## 9. 边界说明

- 只在 `IAPABT_APAType == 1` 且 `IAPASP_APAStsAuth ∈ {5, 6}` 时段统计。
- 当前实现没有直接 import `acc_jerkiness.py` 运行逻辑，但判定口径与其保持一致。
- 如果 APA 类型或泊车状态枚举值变化，需要同步修改代码和文档中的 `apa_type_values`、`apa_status_values`。
