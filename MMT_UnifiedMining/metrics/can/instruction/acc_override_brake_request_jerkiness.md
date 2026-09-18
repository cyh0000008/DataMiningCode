# acc_override_brake_request_jerkiness 指标说明

## 1. 检查对象

检查 ACC 仍然 active、驾驶员已经油门 override，但控制侧仍有制动请求的场景。

这个指标主要看一种冲突感比较强的工况：驾驶员踩着油门，ACC 侧还在请求负加速度，车辆实际也在减速。满足这些条件后，再判断这段里有没有纵向顿挫。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度。既用于顿挫识别，也用于确认实车处于减速状态。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员油门 override 状态。
- `Adaptive_Cruise_Command_Ext_1A3_M.IACCBSCE_ACCAccl`
  ACC 制动请求减速度。
- `PPEI_Engine_General_Status_1_082_M2.IAccActPos`
  油门踏板位置。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 5 个信号为准。

## 3. 有效样本条件

先取 5 个信号的公共时间段。每个采样点必须同时满足以下条件，才参与顿挫检测：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == true`
- `IACCBSCE_ACCAccl < 0`
- `IIMULonAccSec < 0`
- `IAccActPos > 0`

对应到工程含义：

- ACC 没有退出
- 驾驶员处于油门 override
- ACC 侧有负向加速度请求
- 车辆实际纵向加速度为负
- 油门踏板位置大于 0

斜率计算依赖相邻采样点，所以候选点要求当前点和前一个点都满足以上条件。

当前口径不额外加车速条件，避免把低速或起步附近的真实冲突工况过滤掉。

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
- `decel_jump_min_drop = 0.80`
- `decel_jump_min_peak_decel = 0.60`

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

- `IACCBSCE_ACCAccl < 0` 按“负值表示减速度请求”处理。如果后续确认该信号用正值表达减速度，需要同步改代码和文档。
- 只按上面的 5 个条件过滤有效样本，不额外使用车速过滤。
- 顿挫口径跟 `acc_jerkiness` 保持一致，后续如果调整基础顿挫算法，这里也应同步确认。
