# UnifiedMining

这是新的统一 H5 数据挖掘框架。Raw 目录只作为历史逻辑参考，新入口不调用 Raw 代码。

## 运行

```bash
python3 DataMining.py
```

Windows 环境如果没有 `python3` 命令，用 `python DataMining.py` 等价运行。程序会自动读取同目录下的 `config.json`。

输出在 `UnifiedMining/Result_Test`：

- 每个指标一个 CSV
- 每个指标一个 `_events.json`
- `skipped_metrics.csv` 记录因缺失信号而未运行的指标，包含指标名称、文件路径、缺失字段和缺失 H5 路径
- 一个汇总 XLSX 报告，汇总页和明细页都会标注 `指标类型`：CPI 或 MPI
- `run_manifest.json` 记录本次输入和指标列表

## 结构

- `DataMining.py`：唯一主入口，负责加载配置、发现 H5、加载 filters、生成报告。
- `config.json`：统一配置。`signals` 是标准字段名到 H5 显式路径的映射。
- `signal_preprocessor.py`：统一信号预处理，读取 H5、按时间轴采样、生成衍生字段。
- `metric_utils.py`：公共工具函数库，包含 Metric 数据结构、分段、采样后计算、jerk/TTC/严重度等可复用工具；具体指标逻辑不放在这里。
- `reporting.py`：CSV、JSON、XLSX 输出。
- `filters/`：每条指标一个筛选文件，指标类型、阈值、场景选择和 `evaluate` 逻辑都落在对应文件里。

## CPI / MPI

指标类型不是 H5 数据来源拆分，而是指标自身分类：

- CPI：纵向场景/体验类指标，例如跟停、跟起、坡道、曲率限速。
- MPI：状态/执行/请求类指标，例如 ABS/TCS/VSE 激活、ACC arbitration、重制动、顿挫、溜车等。

代码里每个 Metric 都带 `category` 字段；报告里用 `指标类型` 列标明 CPI/MPI，不拆成两份报告。

汇总页固定使用一列 `指标值`：

- CPI 指标沿用原口径：无 Bad Case 时显示 `100.0(总Case/Bad Case)`，有 Bad Case 时显示 `总Case / Bad Case`。
- MPI 指标使用里程归一化口径：`指标值 = 当前统计数据总里程 km / 总Case数`，显示为 `x.xxxkm/case(总里程/总Case)`。

总里程由 `VehOdo` 计算：每个 H5 文件取有效 `VehOdo` 末值 - 首值，所有文件相加。当前配置路径为 `CAN0/Vehicle_Odometer.IVehOdo`，有效位为 `CAN0/Vehicle_Odometer.IVehOdoV`；如 H5 中 `VehOdo` 不是 km 单位，调整 `defaults.odometer_scale_to_km` 即可。

## 信号路径规则

`config.json` 的输入配置：

```json
"input_path": "../Raw",
"date": "20260702-20260703"
```

- `input_path` 必须是文件夹路径。
- 程序会递归扫描该文件夹及其子文件夹中的所有 `.h5` / `.hdf5` 文件，因此可以直接填写车型上层目录，例如 `F:\数据挖掘\DataMiningData\J6B\_Data\ES27PV011`。
- `date` 格式固定为 `YYYYMMDD-YYYYMMDD`，按闭区间过滤。程序优先读取 H5 最近一级带日期的父文件夹；父目录均无日期时，再读取 H5 文件名中的日期。
- 文件夹名和文件名中的日期均支持 `YYYY-MM-DD` 或 `YYYYMMDD`，例如文件夹 `20260827-E2LB-2-ES27PV011_parsed`，或文件 `VC9_ver0520_2026-06-25_10_50_58_CAN_parsed.h5`。
- `filters` 目录固定为 `UnifiedMining/filters`；时间轴固定使用标准字段 `acceleration`。

`config.json` 中每个 `signals` 值都必须是 H5 内的明确路径，例如：

```json
"front_range": "Eth_Debug_CAN5/Forward_Looking_Target_1.FLT1_Long_Range"
```

缺失信号按指标处理，不按整车文件处理。预处理会读取当前 H5 中存在的信号并记录缺失路径；某条指标运行前只检查自己依赖的字段，缺失时该指标在对应 CSV 中标记为 `SKIPPED`，并写出标准字段名和配置 H5 路径，其他不依赖该信号的指标继续运行。

`stationary_state` 当前读取 `CAN2/Automatic_Braking_Status_044_M.IVehMvngStat`，其中 `0` 表示 moving，`1` 表示 stationary。`stationary_flag` 使用组合规则：`ego_speed_mps < 0.01` 或 `IVehMvngStat == defaults.stationary_value` 任一成立即认为 stationary；`moving_flag` 为其反值。

纵向加速度采用 `CAN2/IMU_Yaw_Long_Acc_038_M.IIMULonAccPri`，并用 `IIMULonAccPriV` 校验有效性；`IIMULonAccPriV == 0` 表示有效。车速采用 `IVehSpdAvgDrvn_1`，并用 `IVehSpdAvgDrvnV_1` 校验有效性；`IVehSpdAvgDrvnV_1 == 0` 表示有效。无效采样点会置为 `NaN`，不会参与后续指标计算。

坡度信号配置为 `slope_deg`，当前 H5 路径是 `Eth_Debug_CAN5/MPC_DebugMsg_Reserved_18.Debug_single_Reserved_51`，真实含义是坡度角，单位 `deg`。预处理会派生内部字段 `slope_acc = 9.80665 * sin(slope_deg)`，单位 `m/s^2`，仅用于 `slope_no_frnt_acc`、`slope_no_frnt_jerk`、`slope_no_frnt_vel_fluct` 的坡道场景筛选。`acceleration_total` 直接等于 `acceleration`，不再叠加坡度。

## Cut-in 目标说明

跟车目标和 cut-in 目标使用同一套 `Forward_Looking_Target_1.*`，由 `FLT1_CUTIN_Flag` 判断当前 FLT1 的语义：

- `FLT1_CUTIN_Flag = 0`：FLT1 表示跟车目标，派生 `front_*` 字段。
- `FLT1_CUTIN_Flag = 1`：FLT1 表示 cut-in 目标，派生 `cutin_*` 字段。

当前已配置的 FLT1 显式路径：

- `front_target_id`: `Eth_Debug_CAN5/Forward_Looking_Target_1_Res.FLT1_Global_ID`
- `front_range`: `Eth_Debug_CAN5/Forward_Looking_Target_1.FLT1_Long_Range`
- `front_velocity_abs`: `Eth_Debug_CAN5/Forward_Looking_Target_1.FLT1_Long_Velocity`
- `front_accel`: `Eth_Debug_CAN5/Forward_Looking_Target_1_Res.FLT1_Long_Accel`

样例 H5 暂未找到 `FLT1_CUTIN_Flag` 的明确路径，所以 `config.json` 中 `optional_signals.flt1_cutin_flag` 当前为 `null`，预处理按 `defaults.flt1_cutin_flag_default = 0.0` 处理，即默认 FLT1 为跟车目标。拿到真实路径后，把它填成明确 H5 路径即可，例如：

```json
"optional_signals": {
  "flt1_cutin_flag": "Eth_Debug_CAN5/xxx.FLT1_CUTIN_Flag"
}
```
