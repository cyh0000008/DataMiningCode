# acc_brake_rapid_toggle_decel_jump_count 指标说明

## 1. 检查对象

检查 ACC 自动制动类型 `ACCBSCE_AutBrkTp` 是否出现快速 `2 -> 1 -> 2` 跳变，并确认跳变后 1 秒内纵向加速度是否出现明显跃迁。

当前 case 需要同时满足两步：

- `ACCBSCE_AutBrkTp` 出现 `2 -> 1 -> 2`，中间 `1` 持续不超过 `100ms`
- 从 `1 -> 2` 返回时刻开始，后续 1 秒内纵向加速度 `max - min > 2.0`

## 2. 使用信号

- `Adaptive_Cruise_Command_Ext_1A3_M.IACCBSCE_AutBrkTp`
  ACC 自动制动类型，用于识别 `2 -> 1 -> 2` 快速跳变。
- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于判断跳变后的减速度跃迁。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员 override 状态。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这些信号为准。

## 3. 有效条件

先取 4 个信号的公共时间段。

快速跳变识别段和后续 1 秒观察窗都必须满足：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`

也就是 ACC 保持开启，驾驶员没有 override。

## 4. 快速跳变识别

在 `IACCBSCE_AutBrkTp` 原始状态序列中查找：

- 上一个状态为 `2`
- 跳到 `1`
- 再跳回 `2`

参数：

- `state_1_values = {1}`
- `state_2_values = {2}`

中间 `1` 状态持续时间：

- `state1_duration = state2_return_timestamp - state1_start_timestamp`

只有满足以下条件，才认为是一次快速跳变：

- `state1_duration <= 0.10s`

## 5. 减速度跳变判定

以 `1 -> 2` 返回时刻作为观察窗口起点：

- `decel_window_seconds = 1.00`

在该 1 秒窗口内取纵向加速度最大值和最小值：

- `decel_range = max_acceleration - min_acceleration`

case 条件：

- `decel_range > 2.0`

严重度：

- `severity = decel_range - decel_range_threshold`

## 6. 输出字段

事件级输出：

- `state1_start_timestamp`
- `state2_return_timestamp`
- `state1_duration`
- `decel_window_end_timestamp`
- `decel_range`
- `min_acceleration`
- `max_acceleration`
- `severity`

## 7. 统计口径

- `All Case Count`：满足快速跳变和减速度跳变条件的事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最大单事件严重度

## 8. 默认参数

- `mid_state_max_duration_seconds = 0.10`
- `decel_window_seconds = 1.00`
- `decel_range_threshold = 2.0`
- `state_1_values = {1}`
- `state_2_values = {2}`

## 9. 输出示例

```text
[Event state1_start=24.820, state2_return=24.900, state1_duration=0.080, decel_window_end=25.900, decel_range=2.430, min_acceleration=-2.180, max_acceleration=0.250, severity=0.430]
```

## 10. 边界说明

- 直接使用 `ACCBSCE_AutBrkTp` 原始状态切换识别 `2 -> 1 -> 2`，不额外做状态滤波。
- 减速度跳变窗口固定为 1 秒，起点是 `1 -> 2` 的返回时刻。
- 跳变量使用观察窗内纵向加速度 `max - min`，不是 jerk。
- 如果后续要把同一大制动过程中的多次快速跳变合并，需要新增事件合并逻辑。
