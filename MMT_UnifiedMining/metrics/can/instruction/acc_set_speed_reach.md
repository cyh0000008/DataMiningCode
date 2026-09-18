# acc_set_speed_reach 指标说明

## 1. 检查对象

检查 ACC 开启且驾驶员没有 override 的稳速场景，判断表显车速是否达到驾驶员设定速度。

该指标不直接用总线车速和原始设定速度做比较，而是先按 CLEA 速度偏置表换算成表显速度后再判断。

## 2. 使用信号

- `PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1`
  车辆实际车速。
- `Adaptive_Cruise_Disp_Stat.IACCDrvrSeltdSpd`
  ACC 驾驶员设定速度。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  驾驶员油门 override 状态。

## 3. 有效样本条件

进入稳速候选前，采样点必须满足：

- `IACCAct376 == true`
- `IAccPdlOvrrdAtv == false`
- `IACCDrvrSeltdSpd > 0`

也就是 ACC 已开启、驾驶员未接管、且存在有效设定速度。

## 4. 表显速度换算

实际车速和设定速度都按同一套 CLEA 偏置表换算为表显值。

偏置表：

- `X = [0, 40, 80, 120, 200, 300]`
- `Y = [1.000, 1.075, 1.052, 1.045, 1.039, 1.036]`

换算流程：

- 根据实际速度所在区间做分段线性插值，得到 `bias`
- `bias` 四舍五入保留 3 位小数
- `display_speed = actual_speed * bias`
- 最终表显速度四舍五入为整数

`IACCDrvrSeltdSpd` 也按同样规则换算为 `display_set_speed`。后续 near、稳速和达速判定都使用表显值。

## 5. 稳速场景识别

先构造 near 条件：

- `abs(display_speed - display_set_speed) <= target_band_kph`

默认：

- `target_band_kph = 3.0`

连续满足 near 条件的区间，持续时间必须满足：

- `duration >= min_scene_duration_seconds`
- `min_scene_duration_seconds = 5.0`

随后检查稳定性：

- 表显速度波动范围 `display_range <= stable_range_kph`
- 设定速度在场景内不变化，即 `set_speed_range == 0`
- `stable_range_kph = 2.0`

满足 near、持续时间和稳定性要求后，该区间记为 1 个稳速场景。

## 6. 达速判定

对每个稳速场景：

- `scene_set_speed`：场景内平均表显设定速度
- `max_display_speed`：场景内最大表显车速

只要场景内任意采样点满足：

- `display_speed >= scene_set_speed - reach_margin_kph`

就认为该场景达到设定速度。

默认：

- `reach_margin_kph = 0.0`

## 7. 输出字段

场景级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `set_speed`
- `max_display_speed`
- `display_range`
- `reached`
- `severity`

字段含义：

- `set_speed`：场景内平均表显设定速度
- `max_display_speed`：场景内最高表显速度
- `display_range`：场景内表显速度波动范围
- `reached`：是否达到设定速度
- `severity`：未达速差值

## 8. 严重度和统计口径

严重度：

- 已达速：`severity = 0`
- 未达速：`severity = set_speed - max_display_speed`

统计字段：

- `All Case Count`：识别出的稳速场景总数
- `Bad Case`：稳速场景中始终没有达到设定速度的场景数量
- `Severity Score`：所有未达速场景里的最大差值

## 9. 默认参数

- `target_band_kph = 3.0`
- `min_scene_duration_seconds = 5.0`
- `stable_range_kph = 2.0`
- `reach_margin_kph = 0.0`

## 10. 输出示例

```text
[Scene start=12.300, end=18.100, duration=5.800, set_speed=100.0, max_display_speed=98.0, display_range=1.0, reached=False, severity=2.000]
```

## 11. 边界说明

- 当前默认使用 CLEA 架构速度偏置表。
- 稳速场景要求 near、持续时间和稳定性同时满足，场景筛选偏严格。
- 如果业务允许一定达速裕量，可以调整 `reach_margin_kph`。
