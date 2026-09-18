# acc_lost_arbitration 指标说明

## 1. 检查对象

检查 ACC 请求状态是否长时间进入 `Lost Arbitration`。

当 ACC 请求 active，同时请求状态为 `Lost Arbitration`，且连续持续时间达到 5 秒，记为 1 个 bad case。

## 2. 使用信号

- `PPEI_Adaptive_Cruise_Axl_Trq_Req_194_M.IACCATC_ACCAct`
  ACC 请求 active 状态。
- `PPEI_Propulsion_General_Data_1_0C2_M.IACCATCS_RqStat`
  ACC 请求状态，用于识别 `Lost Arbitration`。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 2 个信号为准。

当前代码兼容文本枚举和值枚举。本批数据按以下数值识别：

- `ACCATC_ACCAct = 1`
- `Lost Arbitration = 1`

## 3. 判定逻辑

先取两个信号的公共时间段。

分析主时间轴使用 `IACCATC_ACCAct` 原始时间轴，`IACCATCS_RqStat` 按最近邻采样到该时间轴。

单点有效条件：

- `IACCATC_ACCAct == 1`
- `IACCATCS_RqStat == Lost Arbitration`

连续满足条件的点合并为事件片段。片段持续时间必须满足：

- `min_bad_case_duration_seconds = 5.0`

满足持续时间要求后，记为 1 个 bad case。

## 4. 输出字段

事件级输出：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `severity`

严重度直接取持续时间：

- `severity = duration`

持续越久，严重度越高。

## 5. 统计口径

- `All Case Count`：检出的 `Lost Arbitration` 事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最长 `Lost Arbitration` 事件的持续时间

## 6. 默认参数

- `min_bad_case_duration_seconds = 5.0`
- `acc_active_values = [1]`
- `lost_arbitration_values = [1]`

## 7. 输出示例

```text
[Scene start=12.340, end=18.020, duration=5.680, severity=5.680]
```

## 8. 边界说明

- `Lost Arbitration` 当前按数值 `1` 识别。如果后续 DBC 映射不同，需要同步修改代码和文档。
- 严重度目前只看持续时间，没有叠加车速、纵向加速度或其他业务影响因子。
