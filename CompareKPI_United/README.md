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

界面里选择 `J6B` 或 `MMT`，选择 target 报告文件/文件夹；base 可以留空，留空时只展示 target 结果。输出文件固定写到程序所在目录的 `reports/` 文件夹，输出文件名可以在界面里自定义，留空则自动命名。

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

J6B 和 MMT 模板底部均包含 ABS、TCS、VSE 激活关联 ACC 退出三条 MPI。它们按功能激活后
1 秒内出现 ACC 退出的时序关联规则计数，不能视为已证明因果。

### 行车-无干扰加速 CPI

J6B 和 MMT 模板底部均增加 12 条 CPI：最大加速度、加速建立时间、平均加速度分别按
`0–30`、`30–60`、`60–90`、`>=90 km/h` 初始车速分层。报告值是有效事件测量值的均值，
同时显示样本数；这类指标不按 Bad Case 比例判优，比较表只展示两版本的均值和差值，不着色
表达优劣。

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
