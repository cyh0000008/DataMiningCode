# apa_gear_switch_jerkiness 指标说明

## 1. 检查对象

检查 APA 场景期间 D / R 换挡后的 2 秒窗口，判断窗口内是否出现纵向顿挫。

当前只看 D 和 R 之间的双向切换：

- `12`：D 档
- `14`：R 档

如果切换后 2 秒内目标档位又发生变化，这次切换不计入统计。

## 2. 使用信号

- `IMU_Yaw_Long_Acc_037_M2.IIMULonAccSec`
  纵向加速度，用于顿挫识别。
- `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType`
  APA 类型。
- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth`
  APA 泊车状态。
- `PPEI_Trans_General_Status_2_ECP_H1_0D7_M.ITransEstGear_ECP_H1`
  变速箱估计档位，用于识别 D / R 切换。

## 3. 窗口定义

先取 3 个信号的公共时间段。

D / R 切换窗口必须满足：

- 档位发生 `D -> R` 或 `R -> D`
- 切换后完整 2 秒数据存在
- 2 秒内档位持续保持切换后的目标档位
- 2 秒内 `IAPABT_APAType == 1`
- 2 秒内 `IAPASP_APAStsAuth` 持续为 `5` 或 `6`

只有有效窗口内检出顿挫，才记为 bad case。

## 4. 顿挫识别

窗口内顿挫判定与 `apa_jerkiness`、`acc_jerkiness` 保持一致，三类分支并联：

- 短时振荡顿挫
- 长周期交替加减速
- 减速度短时跳变

任意一类命中，该换挡窗口记为 1 个 bad case。

## 5. 输出字段

场景级输出：

- `switch_timestamp`
- `from_gear`
- `to_gear`
- `window_end_timestamp`
- `jerk_event_count`
- `severity`

## 6. 统计口径

- `All Case Count`：2 秒内发生顿挫的 D / R 切换窗口数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：所有 bad case 中最大的单窗口严重度

单窗口严重度取该窗口内顿挫事件的最大严重度。

## 7. 默认参数

- `switch_window_seconds = 2.0`
- `drive_gear_values = [12]`
- `reverse_gear_values = [14]`
- `apa_type_values = [1]`
- `apa_status_values = [5, 6]`
- 其余顿挫参数与 `apa_jerkiness` 一致

## 8. 输出示例

```text
[Scene switch=48.682, from=12, to=14, window_end=50.682, jerk_event_count=1, severity=985.397]
```

## 9. 边界说明

- 只统计 D / R 双向切换，不统计其他档位变化。
- 切换后 2 秒内目标档位再次跳变时，该次切换直接跳过。
- `All Case Count` 和 `Bad Case` 都表示发生顿挫的切换窗口数，不是全部有效切换窗口数。
