# vse_activation_count 指标说明

## 1. 检查对象

统计 VSES 功能触发次数，以及每次触发持续时间。

当总线信号 `PPEI_Chassis_General_Data_3_0AC_M.IVSEAct` 为 `1` 时，认为 VSES 处于触发状态；每一段连续为 `1` 的时间片段记为 1 次触发。

## 2. 使用信号

- `PPEI_Chassis_General_Data_3_0AC_M.IVSEAct`
  VSES 激活状态信号，用于识别 VSES 是否触发。

该信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以该信号为准。

当前版本按以下枚举识别触发状态：

- `VSES active = 1`

## 3. 判定逻辑

直接使用 `PPEI_Chassis_General_Data_3_0AC_M.IVSEAct` 原始时间轴，不做重采样。

单点有效条件：

- `PPEI_Chassis_General_Data_3_0AC_M.IVSEAct == 1`

将连续满足条件的采样点合并为一个事件片段。每个片段记为 1 次 VSES 触发。

触发持续时间定义为：

- `duration = end_timestamp - start_timestamp`

## 4. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `severity`

严重度直接取持续时间：

- `severity = duration`

## 5. 统计口径

- `All Case Count`：检出的 VSES 触发事件数
- `Bad Case`：同 `All Case Count`，用于兼容现有汇总流程
- `Severity Score`：该文件夹内最长一次 VSES 触发持续时间

## 6. 默认参数

- `vse_active_values = [1]`

## 7. 输出示例

```text
[Scene start=35.120, end=35.480, duration=0.360, sample_count=10, severity=0.360]
```

## 8. 边界说明

- 如果 `PPEI_Chassis_General_Data_3_0AC_M.IVSEAct` 长时间保持为 `1`，只记作 1 次触发，直到信号回到非 `1` 后再次变为 `1` 才会新增一次。
- 如果数据只有单个采样点或无有效公共时间段，结果返回 0。
