# apa_parking_rollback_count 指标说明

## 1. 检查对象

统计 APA 泊车开启/引导过程中出现的溜车次数。

当前口径使用 `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth ∈ {5,6}` 且 `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType == 1` 识别 APA 场景。在该过程中，如果车辆处于前进挡、车速接近 0、四个车轮方向都显示 Reverse，且四个车轮角速度都不是 0，则记为泊车溜车。

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
- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth`
  APA 泊车状态，用于限制泊车开启/引导过程。
- `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType`
  APA 类型。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这些信号为准。

当前代码按以下数值识别：

- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth == 5 或 6`
- `IAPABT_APAType == 1`
- `Reverse = 4`
- `CVT Forward Gear = 12`

## 3. 判定逻辑

先取全部信号的公共时间段。

分析主时间轴使用左前轮角速度信号，其他信号按最近邻采样到该时间轴。

单点有效条件：

- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth == 5 或 6`
- `IAPABT_APAType == 1`
- 四个轮速方向信号都等于 `Reverse`
- `IVehSpdAvgNDrvn < 0.5 kph`
- 四个车轮角速度都不等于 `0`
- `ITransEstGear_ECP_H1 == CVT Forward Gear`

泊车溜车不增加以下条件：

- 不要求 `IACCBSCE_AutBrkTp == hold`
- 不要求 `IACCAct376 == true`
- 不判断 `IAccPdlOvrrdAtv`

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
- `parking_status`
- `severity`

严重度直接取事件段内四个轮角速度绝对值均值的平均值：

- `severity = mean_abs_wheel_speed`

轮角速度越大，溜车越明显。

## 5. 统计口径

- `All Case Count`：检出的泊车溜车事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最大单事件严重度

## 6. 默认参数

- `max_vehicle_speed_kph = 0.5`
- `min_scene_duration_seconds = 0.20`
- `min_wheel_speed_abs = 0.01`
- `parking_active_status_values = [5, 6]`
- `apa_type_values = [1]`
- `reverse_direction_values = [4]`
- `forward_gear_values = [12]`

## 7. 输出示例

```text
[Scene start=12.340, end=13.020, duration=0.680, vehicle_speed=0.120, wheel_speed_mean=1.870, parking_status=5, severity=1.870]
```

## 8. 边界说明

- 枚举值基于当前代码配置；DBC 或解码方式变化后需要同步更新。
- 车辆速度条件按 `< 0.5kph` 判断，没有取绝对值。
- 该指标统计 `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth ∈ {5, 6}` 且 `IAPABT_APAType == 1` 的 APA 场景。
- 严重度当前使用轮角速度均值。如果后续更关注溜车距离或持续时间，可以替换评分方式。
