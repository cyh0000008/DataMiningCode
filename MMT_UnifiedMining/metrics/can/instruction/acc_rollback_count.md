# acc_rollback_count 指标说明

## 1. 检查对象

检查 ACC 自动制动 hold 阶段是否出现溜车。

当前溜车定义：车辆处于前进挡、ACC 处于开启、ACC 自动制动类型为 hold、车速接近 0，但四个车轮方向都显示 Reverse，且四个车轮角速度都不是 0。

## 2. 使用信号

- `Wheel_Distance_Direction_Info_High_Frequency.IWhlRotDirStatLFHigFreq`
  左前轮转动方向。
- `Wheel_Distance_Direction_Info_High_Frequency.IWhlRotDirStatRFHigFreq`
  右前轮转动方向。
- `Wheel_Distance_Direction_Info_High_Frequency.IWhlRotDirStatLRHigFreq`
  左后轮转动方向。
- `Wheel_Distance_Direction_Info_High_Frequency.IWhlRotDirStatRRHigFreq`
  右后轮转动方向。
- `PPEI_Vehicle_Speed_and_Distance_208_M2.IVehSpdAvgNDrvn`
  车辆速度，用于确认接近静止。
- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  ACC 开启状态，用于限制只统计 ACC 开启时段。
- `Adaptive_Cruise_Command_Ext_1A3_M.IACCBSCE_AutBrkTp`
  ACC 自动制动类型，用于识别 hold。
- `PPEI_Chassis_General_Data_1_031_M.IWhlAngVelLFrtAuth`
  左前轮角速度。
- `PPEI_Chassis_General_Data_1_031_M.IWhlAngVelRFrtAuth`
  右前轮角速度。
- `PPEI_Chassis_General_Data_1_031_M.IWhlAngVelLRrAuth`
  左后轮角速度。
- `PPEI_Chassis_General_Data_1_031_M.IWhlAngVelRRrAuth`
  右后轮角速度。
- `PPEI_Trans_General_Status_2_ECP_H1_0D7_M.ITransEstGear_ECP_H1`
  档位估计，用于限制前进挡。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这些信号为准。

当前代码兼容文本枚举和值枚举。本批数据按以下数值识别：

- `ACC active = 1`
- `Reverse = 4`
- `hold = 5`
- `CVT Forward Gear = 12`

## 3. 判定逻辑

先取全部信号的公共时间段。

分析主时间轴使用左前轮角速度信号，其他信号按最近邻采样到该时间轴。

单点有效条件：

- 四个轮速方向信号都等于 `Reverse`
- `IVehSpdAvgNDrvn < 0.5 kph`
- `IACCAct376 == true`
- `IACCBSCE_AutBrkTp == hold`
- 四个车轮角速度都不等于 `0`
- `ITransEstGear_ECP_H1 == CVT Forward Gear`

连续满足条件的点合并为溜车片段。为避免单点抖动误报，片段持续时间必须满足：

- `min_scene_duration_seconds = 0.20`

## 4. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `max_vehicle_speed`
- `mean_abs_wheel_speed`
- `severity`

严重度直接取事件段内四个轮角速度绝对值均值的平均值：

- `severity = mean_abs_wheel_speed`

轮角速度越大，溜车越明显。

## 5. 统计口径

- `All Case Count`：检出的溜车事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最大单事件严重度

## 6. 默认参数

- `max_vehicle_speed_kph = 0.5`
- `min_scene_duration_seconds = 0.20`
- `min_wheel_speed_abs = 0.01`
- `acc_active_values = [1]`
- `reverse_direction_values = [4]`
- `hold_brake_type_values = [5]`
- `forward_gear_values = [12]`

## 7. 输出示例

```text
[Scene start=12.340, end=13.020, duration=0.680, vehicle_speed=0.120, wheel_speed_mean=1.870, severity=1.870]
```

## 8. 边界说明

- 枚举值基于当前数据解码结果配置；DBC 或解码方式变化后需要同步更新。
- 车辆速度条件按 `< 0.5kph` 判断，没有取绝对值。
- 严重度当前使用轮角速度均值。如果后续更关注溜车距离或持续时间，可以替换评分方式。
