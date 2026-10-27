# CompareKPI_United 使用说明

这个目录用于对比 base 和 target 两份 MPI/CPI 报告，并生成汇总对比表。
如果 base 目录为空，脚本会只展示 target 的 MPI/CPI 结果，不执行对比。

## 目录结构

```text
CompareKPI_United/
  mpi_cpi_compare.py
  README.md
  templates/
    template_J6B.xlsx
    template_MMT.xlsx
  base/
    J6B/
    MMT/
  target/
    J6B/
    MMT/
  reports/
```

- 选择 `J6B` 时，默认读取 `base/J6B/` 和 `target/J6B/`。
- 选择 `MMT` 时，默认读取 `base/MMT/` 和 `target/MMT/`。
- 选择 `J6B` 时，默认使用 `templates/template_J6B.xlsx`。
- 选择 `MMT` 时，默认使用 `templates/template_MMT.xlsx`。
- 不传 `--output` 时，默认输出到 `reports/MPI_CPI_汇总对比_<配置>_<target日期>.xlsx`。

## 支持的输入格式

### 最新 unified 报告

汇总表需要包含这些列：

- `指标来源`
- `指标名称`
- `指标类型`
- `总里程(km)`
- `总次数`
- `触发次数`
- `MPI`
- `CPI`

文件建议按下面格式命名：

```text
车型_开始日期_结束日期_版本.xlsx
```

车型名本身包含 `_` 时，脚本会自动寻找连续两个日期段，日期段前面的内容都作为车型。

### 旧版 Longitudinal_CPI_Report

旧的 `Longitudinal_CPI_Report_...xlsx` 汇总表仍兼容。脚本会在比较前自动转换成最新 8 列格式，转换缓存写到 `.converted_reports/<配置>/<base|target>/`，原始文件不改动。

### 旧版 Topic/Can 报告

也兼容原 CompareKPI 的输入方式：

```text
base/J6B/Topic_车型_开始日期_结束日期_软件版本.xlsx
base/J6B/Can_车型_开始日期_结束日期_软件版本.xlsx
target/J6B/Topic_车型_开始日期_结束日期_软件版本.xlsx
target/J6B/Can_车型_开始日期_结束日期_软件版本.xlsx
```

## 运行

### 图形界面

```powershell
cd D:\Data_Mining\CompareKPI_United
python .\compare_kpi_gui.py
```

界面里选择 `J6B` 或 `MMT`，并选择动力形式 `ICE`、`PHEV` 或 `BEV`，再选择 target 报告文件/文件夹；base 可以留空，留空时只展示 target 结果。筛选标准列优先使用模板中所选动力形式对应的标准。输出文件固定写到程序所在目录的 `reports/` 文件夹，输出文件名可以在界面里自定义，留空则自动命名；自动生成的文件名包含动力形式。

打包 exe：

```powershell
cd D:\Data_Mining\CompareKPI_United
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

打包完成后运行 `dist/CompareKPI_UI.exe`。从本项目的 `dist/` 启动时，报告会写到项目根目录的 `reports/` 文件夹；如果把 exe 复制到别的目录单独运行，则写到 exe 同目录的 `reports/` 文件夹。

### 命令行

```powershell
cd D:\Data_Mining\CompareKPI_United
python .\mpi_cpi_compare.py --report-profile J6B
python .\mpi_cpi_compare.py --report-profile MMT
```

可通过 `--powertrain-type` 选择 `ICE`、`PHEV`、`BEV`；不传入时会交互选择，非交互环境默认 `PHEV`。
动力形式会用于解析该车型对应的筛选标准；对比报告的筛选标准列只展示标准正文，
不再为每项标准添加动力形式前缀。动力形式记录在报告表头、
自动生成的文件名及 Excel 属性/筛选标准表头备注中。
报告表头在“车型”和“日期”之间新增“动力形式”行，Base 和 Target 两侧均展示本次选择的动力形式；
该行表示本次对比所选标准，不代表对历史 Base 数据重新挖掘。
模板中已按动力形式分列的标准优先于脚本内的旧固定标准；若模板缺少所选类型的标准会报错，
不会借用另一种动力形式的标准。未按动力形式分列的旧模板仍沿用代码中的兼容标准：

静态模板会并列展示每项指标的 `ICE`、`PHEV`、`BEV` 三套独立标准；ICE 保留原 ICE 标准，
PHEV 和 BEV 的初始值分别沿用原 PHEV/BEV 标准。生成报告时只保留所选类型的标准正文。
“行车-无干扰加速”的三类指标均按事件判定并采用常规 CPI 展示及优劣比较：
最大加速度 `1.5–2.5 m/s²`（含边界）、平均加速度 `>1.5 m/s²`、加速建立时间 `<3.5 s`。
筛选标准列只描述统计关键量和标准值，详细场景条件见挖掘说明。历史均值报告需重新挖掘后再比较。
旧的双类型模板仍兼容：其中 `PHEV/BEV` 条目作为 PHEV、BEV 的兼容来源，独立条目优先。

```powershell
python .\mpi_cpi_compare.py --report-profile MMT --powertrain-type ICE
python .\mpi_cpi_compare.py --report-profile J6B --powertrain-type PHEV
```

也可以不复制报告，直接指定 base/target 路径：

```powershell
python .\mpi_cpi_compare.py `
  --report-profile J6B `
  --base "D:\Data_Mining\CompareKPI_United\base\J6B" `
  --target "D:\Data_Mining\CompareKPI_United\target\J6B"
```

如果只想展示 target 结果，让对应 `base/<配置>/` 保持为空，或用 `--base` 指向一个空目录。

如果需要保留读取明细，增加：

```powershell
--details-sheet
```

如果 base 是 `MPI_CPI_汇总对比.xlsx` 这类旧对比汇总表，可以选择其中一个日期组作为新的 base：

```powershell
python .\mpi_cpi_compare.py `
  --report-profile MMT `
  --base-source-date "20260722-20260723"
```

## 对比规则

- base 和 target 的 bad case 都为 `0` 时标记为一致。
- 只有一边 bad case 为 `0` 时，bad case 为 `0` 的一边更优。
- 两边 bad case 都非 `0` 时，优先比较括号前的 MPI/CPI 指标值，指标值更大的一边更优。
- target 更好时标绿色，更差时标红色。
- 无法解析指标值时，退回比较 bad case 数量。

## 准出判断

生成报告在原有表格右侧增加“Base准出标准及结论”和“Target准出标准及结论”两列，
分别展示本次动力形式下的准出阈值及结论，例如 `CPI ≥30` / `可准出`。
“可准出”为绿色文字，“不满足准出”为红色文字。准出是绝对门槛判断，
与原有 Base/Target 相对优劣比较独立；没有 Base 时仍正常判断 Target。

准出阈值依据提供的准出标准表：L2 对应 J6B，L2++ 对应 MMT。
ICE 中“保持”继承同平台 PHEV/BEV 的准出值；明确不同的值为：
J6B 顿挫 ICE ≥70、PHEV/BEV ≥80，起步顿挫 ICE ≥280、PHEV/BEV ≥320；
MMT 顿挫 ICE ≥90、PHEV/BEV ≥100，起步顿挫 ICE ≥360、PHEV/BEV ≥400。
ICE、PHEV、BEV 阈值分别保存于脚本的 `POWERTRAIN_RELEASE_THRESHOLDS`。
此处仅增加准出规则，不修改已有场景筛选和 Bad Case 阈值。

- 有 Bad Case 时，按括号中的总次数/Bad Case（CPI）或总里程/Bad Case（MPI）计算，
  大于等于准出阈值即通过；不使用已四舍五入的展示值判断边界。
- 零 Bad Case 时，总次数（CPI）或总里程 km（MPI）达到准出阈值才显示“可准出”；
  否则显示“样本不足，无法判定”。总量为零也不直接通过。
- 旧报告仅有数值时按该数值判断；`>数值` 仅在该下限已达到准出阈值时通过，
  否则样本不足，不将下限当成确定值。
- 数据缺失、N/A、错误值、历史测量均值等显示“数据缺失或无效，无法判定”。
- 点刹的准出标准在 J6B/MMT、ICE/PHEV/BEV 中统一配置为 MPI ≥80；
  当前点刹挖掘与报告仅在 J6B 中提供，不因此新增 MMT 点刹指标。
- 图片未覆盖的无干扰加速12项、ABS/TCS/VSE关联ACC退出3项，
  显示“未配置准出标准”，不自行推定阈值。
- 控制抖动和跟车刹停波动已从挖掘与对比报告中移除；历史输入中的对应结果不再展示。
- Base/Target 均按本次所选动力形式的准出阈值判断，不代表对历史 Base 重新挖掘。

J6B 和 MMT 模板底部均包含 ABS、TCS、VSE 激活关联 ACC 退出三条 MPI。它们按功能激活后
1 秒内出现 ACC 退出的时序关联规则计数，不能视为已证明因果。

### 行车-无干扰加速 CPI

J6B 和 MMT 模板底部均增加 12 条 CPI：最大加速度、加速建立时间、平均加速度分别按
`0–30`、`30–60`、`60–90`、`>=90 km/h` 初始车速分层。三类指标均按事件阈值判定 Bad Case，
以总次数/Bad Case 的常规 CPI 形式展示和比较；历史测量均值不能直接比较，需重新挖掘。

两侧统一以 ACC 激活、无 override/前车、SetSpeed 高于车速至少 22 km/h、未触发曲率限速或
曲率限速高于目标车速至少 2 km/h 为基础筛选。条件连续满足 0.5 秒后开始事件，30 秒内车速
须增加 20 km/h，期间不能出现持续 0.5 秒、超过 1 km/h 的平滑车速回落；速度和加速度使用
0.5 秒平滑。最大加速度为事件最大平滑加速度，加速建立时间为加速度首次超过 0.2 m/s² 到
最大加速度的时间，平均加速度为 20 km/h 除以事件耗时。指标使用原始纵向加速度，不做坡度补偿。

MMT 还要求红灯状态有效且未亮红灯、停止线距离字段有效且大于 50 m，并要求有效的 Fusion
数据；当前通过 Fusion 前方目标标志排除前车/cut-in，不代表具备独立 cut-in 状态识别。J6B
当前没有红灯/停止线信号映射，因此未应用该项筛选，J6B 与 MMT 结果应结合此差异解读。任一
侧的必要场景信号缺失时不纳入统计，而不是按“无干扰”处理。
MMT 中无停止线的场景仅在上游显式提供有效且大于 50 m 的距离值时纳入；未解析到距离字段仍
按未知状态排除，不将解析默认值当成“无停止线”。
MMT 当前按 `vehicle_speed_average`、`SetSpeed` 和 `curv_velocity_limit` 为 km/h，
`accleration_on_wheel` 为 m/s² 处理；本次只用合成数据验证逻辑，仍需用真实 H5 确认信号单位与
解析字段映射。

## 新增指标

新增指标时需要同步更新：

- `templates/template_J6B.xlsx` 或 `templates/template_MMT.xlsx`
- `mpi_cpi_compare.py` 里的 `SIGNAL_MAP`
- 如果是 J6B 指标，还需要加入 `J6B_METRIC_NAMES`
