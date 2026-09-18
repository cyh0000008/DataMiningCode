# apa_parking_completion_count 指标说明

## 1. 检查对象

统计 APA 泊车完成次数。

当 `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth` 从 `5` 跳到 `6` 时，记为 1 次泊车完成。
同时要求 `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType == 1`，且完成前后都处于 APA 场景，即 `APAStsAuth ∈ {5,6}`。

## 2. 使用信号

- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth`
  APA 泊车状态，用于识别泊车完成状态跳变。
- `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType`
  APA 类型。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 2 个信号为准。

当前版本按数值枚举识别：

- `status_ready_value = 5`
- `status_complete_value = 6`
- `apa_type_values = [1]`
- `apa_status_values = [5, 6]`

## 3. 判定逻辑

直接使用 `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth` 原始时间轴，`APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType` 按最近邻采样到该时间轴。

从第二个采样点开始逐点检查相邻状态：

- 前一采样点：`Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth == 5`
- 当前采样点：`Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth == 6`
- 前一采样点：`IAPABT_APAType == 1`
- 当前采样点：`IAPABT_APAType == 1`

满足 `5 -> 6` 时，在当前采样点时间戳处记 1 次泊车完成。

如果信号持续保持在 `6`，不会重复计数；必须先回到其他状态，再出现新的 `5 -> 6`，才会新增一次。

## 4. 输出字段

事件级输出：

- `timestamp`
- `prev_status`
- `curr_status`

## 5. 统计口径

- `All Case Count`：识别出的泊车完成事件数
- `Bad Case`：同 `All Case Count`，用于兼容现有汇总流程
- `Severity Score`：固定为 `0.0`

该指标是次数统计，不表达异常严重程度。

## 6. 输出示例

```text
[Event timestamp=125.360, prev_status=5, curr_status=6]
```

## 7. 边界说明

- 当前只识别数值枚举 `5 -> 6`，并要求 `APAType == 1` 且事件前后处于 APA 场景。如果 DBC 状态定义变化，需要同步更新代码和文档。
- 该指标是离散边沿计数，不要求状态 `6` 持续一定时间。
