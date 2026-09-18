# apa_lost_arbitration 指标说明

## 1. 检查对象

检查 APA 场景时推进系统响应是否长时间处于 `Arbitration Failed`。

当 `APAType == 1` 且 `APAStsAuth ∈ {5,6}`，同时推进系统响应为 `Arbitration Failed`，且连续持续时间达到 5 秒，记为 1 个 bad case。

## 2. 使用信号

- `APA_Brake_Propulsion_Torqu_1F0_M.IAPABT_APAType`
  APA 类型。
- `Advanced_Park_Assist_Status_190_M.IAPASP_APAStsAuth`
  APA 泊车状态。
- `PPEI_Trans_General_Status_2_ECP_H1_0D7_M.ISuprRmtPrkPrplSysResp`
  远程泊车推进系统响应，用于识别 `Arbitration Failed`。

这些信号需要在 `config.json` 的 `preprocess_signals` 中配置，代码里的 `REQUIRED_SIGNALS` 也以这 3 个信号为准。

当前代码兼容文本枚举和值枚举。本批数据按以下数值识别：

- `APAType = 1`
- `APAStsAuth = 5 或 6`
- `Arbitration Failed = 2`

## 3. 判定逻辑

先取三个信号的公共时间段。

分析主时间轴使用 `IAPASP_APAStsAuth` 原始时间轴，`IAPABT_APAType` 和 `ISuprRmtPrkPrplSysResp` 按最近邻采样到该时间轴。

单点有效条件：

- `IAPABT_APAType == 1`
- `IAPASP_APAStsAuth == 5 或 6`
- `ISuprRmtPrkPrplSysResp == 2`

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

- `All Case Count`：检出的 APA `Lost Arbitration` 事件数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内最长 APA `Lost Arbitration` 事件的持续时间

## 6. 默认参数

- `min_bad_case_duration_seconds = 5.0`
- `apa_type_values = [1]`
- `apa_status_values = [5, 6]`
- `arbitration_failed_values = [2]`

## 7. 输出示例

```text
[Scene start=12.340, end=18.020, duration=5.680, severity=5.680]
```

## 8. 边界说明

- 当前样本里这两个信号扫描结果都只有 `0`，本批数据大概率不会命中事件；这只说明样本没有该状态，不影响指标逻辑。
- 如果后续 DBC 映射不同，需要同步修改代码和文档中的枚举值。
