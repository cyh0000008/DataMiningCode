# UnifiedApp — 统一数据挖掘 & KPI 对比报告工具

把 [J6B_UnifiedMining](../J6B_UnifiedMining)、[MMT_UnifiedMining](../MMT_UnifiedMining)、
[CompareKPI_United](../CompareKPI_United) 三个既有工程整合到一个跨平台（Windows / Ubuntu）
桌面应用里，提供图形化参数配置、任务队列（全局最多同时运行 3 个进程）和实时进度展示。

**本应用不修改、不导入三个既有工程内部的任何代码**：它以子进程方式调用各自的入口脚本
（`DataMining.py` / `mpi_cpi_compare.py`），通过解析子进程 stdout 输出推导进度，
从而完全避免三套工程各自 Python 依赖（h5py / pandas / openpyxl 等版本）之间的冲突。

## 功能

- 三个 Tab：J6B 挖掘 / MMT 挖掘 / 对比报告，分别对应三个既有工程最常用的运行参数。
- **挖掘完成后自动生成对比报告**：J6B / MMT 挖掘任务成功结束后，会自动从其
  输出中捕获本次生成的报告文件路径，并以此为 `target` 自动提交一个对比报告
  任务到队列，不需要手动切到“对比报告”页签重新配置。“对比报告”页签本身
  仍然保留，可随时手动单独运行对比（例如对比两次历史报告，而不是当次挖掘）。
- 全局任务队列：无论提交哪种任务（含自动触发的对比任务），最多同时运行 3 个
  子进程，超出的自动排队，完成一个再自动启动下一个排队任务。可在面板里把
  并发数调低（1~3）。
- 每个任务独立进度条 + 实时日志弹窗，可随时取消排队中或运行中的任务。
- 参数预设：三类任务都可以保存 / 加载 / 删除多套参数预设，方便重复运行同类任务。
- 环境设置：手动指定三个既有工程所在的仓库根目录，以及各自使用的 Python 解释器路径
  （便于分别指向各工程自己的 venv，如 `CompareKPI_United/.venv`）。

## 运行（源码方式）

```bash
cd UnifiedApp
pip install -r requirements.txt
python main.py
```

首次启动会尝试自动定位与 `UnifiedApp` 同级的 `J6B_UnifiedMining` /
`MMT_UnifiedMining` / `CompareKPI_United`；找不到时会弹出“环境设置”对话框，
手动选择仓库根目录即可。

> 注意：三个既有工程各自的 Python 依赖（h5py、pandas、openpyxl、numpy 等）
> 仍需要在“环境设置”里指定的 Python 解释器环境中安装好，UnifiedApp 本身
> 只依赖 PySide6。

## 参数说明

### J6B 挖掘

对应 [J6B_UnifiedMining/DataMining.py](../J6B_UnifiedMining/DataMining.py)，
运行方式等价于：

```bash
python J6B_UnifiedMining/DataMining.py <临时生成的config.json>
```

表单只暴露常用的运行参数（输入路径、日期范围、输出目录、报告名称、启用指标、
信号范围）；其余字段（如 `signals` 显式信号路径映射）会从你选择的“模板配置文件”
（默认 `J6B_UnifiedMining/config.json`）里原样保留，只覆盖表单里填写的部分，
生成一份临时 config.json 提交运行，运行结束后自动清理。

“动力形式”在 J6B 挖掘页的运行参数中选择 `ICE`、`PHEV` 或 `BEV`，所选值会覆盖模板配置中的
`powertrain_type`，用于选择对应的指标筛选阈值；同时会保存在任务预设中。
自动生成对比报告时也会沿用该动力形式，显式传入 `--powertrain-type`，不会回退到对比脚本默认值。

表单下方的“挖掘完成后自动生成对比报告”默认勾选：挖掘成功后会从 stdout 里
`Report written: <路径>` 这一行捕获本次生成的报告路径，作为对比报告的
`--target` 自动提交一个对比任务（`--report-profile J6B`）。可选填写一个
“基线报告”路径作为 `--base`（用于和历史结果对比），留空则对比报告只展示
本次挖掘结果。若挖掘任务失败/被取消，或者未能从输出中捕获到报告路径，则
不会触发自动对比（后一种情况会在任务日志里给出明确提示，可在“对比报告”
页签手动补跑）。

运行参数中填写“当前报告车型”“当前报告软件版本”和已有的“日期范围”。
挖掘成功后会额外保存原始 XLSX，命名为 `车型-开始日期-结束日期-软件版本.xlsx`。
例如 `E2LB-2-ES27PV011-20260901-20260910-V1.2.3.xlsx`，表头车型显示为
`E2LB-2 ES27PV011`。可配置“原始报告额外保存目录”，留空则保存在原始报告旁。
同名文件追加序号，不覆盖历史报告；自动对比使用该副本，基线也从其文件名识别表头。
不再提供表头覆盖输入。旧预设的 Target 车型和版本自动迁移，日期始终使用挖掘日期范围。

J6B 和 MMT 每次成功运行后自动打包报告，无需额外开关：
启用自动对比时，等待对比成功后，将本次对比 XLSX 和 Bad Case XLSX 压缩到同一个 ZIP，
直接放在表单选择的“输出目录”下（不是原始报告额外保存目录或本次报告子目录）。
输出目录留空时使用模板配置的输出目录。文件名为
`车型_当前报告软件版本_YYYYMMDD_HHMMSS.zip`，时间取打包时的本地时间，
例如 `E2LB-2-ES27PV011_V1.2.3_20261028_173115.zip`；非法文件名字符替换为下划线，
同名追加序号，不覆盖已有 ZIP。车型和版本直接复用当前报告字段，与旧预设迁移规则一致。
ZIP 只含这两个 Excel，不含 PNG、JSON、CSV 或汇总报告；Bad Case 中的完整 PNG 外链仍依赖原目录。
未启用自动对比时，只打包本次生成的 Bad Case Excel，并在日志中明确说明。
禁用 Bad Case 提取时只打包已生成的对比报告；没有任何可打包报告、自动对比失败/取消或打包失败时，
不会生成冒充完整结果的 ZIP，日志及源任务的状态提示会显示打包失败。
MMT dry-run 不打包。仅使用本次子进程返回的精确报告路径，不扫描历史报告。
打包在 J6B 延迟检查之前完成，延迟检查失败不影响已生成的报告 ZIP。
日志中可查看 ZIP 的完整路径，任务状态悬停也可查看；此应用逻辑更新需重新打包旧 EXE 才能生效。

J6B 运行参数另有“信号图”复选框（默认关闭），勾选后在挖掘结束时为筛选后的
Bad Case 自动绘制并嵌入信号图。选项随预设保存；图中展示实际筛选阈值、
数值型指标的越界点，以及事件类指标的时间窗口/关键时刻。
该功能需要 J6B Python 环境安装 Pillow；生成时间和报告体积会增加。
完整 PNG 与报告一起保存在本次输出目录。新增复选框需要重新打包 UnifiedApp 才会出现在旧应用中。

J6B 的“延迟检查”开关默认开启，并随任务预设保存（旧预设默认开启）。
开启时，执行顺序为：挖掘 → 汇总/Bad Case 报告及信号图 → 自动对比报告 → ZIP 打包 → LCC 延迟检查。
延迟检查作为最后一个独立任务进入全局队列，不占用前面的报告生成流程；
不开启自动对比时，在挖掘及报告结束后直接入队。对比失败后仍可检查已完成的挖掘数据，
取消自动对比则不再启动后续检查。检查失败不改变已经成功的挖掘/对比任务状态。
关闭时不扫描延迟信号、不生成诊断图及 JSON，队列显示“未启用”，不影响原有报告和统计。
检查不受 Bad Case 信号图开关、指标选择或指标信号范围影响，
从本次 `run_manifest.json` 读取路径和日期筛选后实际挖掘的 H5 文件，不重新扫描输入目录。
任务队列新增“LCC延迟>200 ms”和“同步结果图”两列，点击“打开结果图”使用系统图片查看器打开 PNG。
开启/退出任一匹配边沿的绝对时间差严格大于 200 ms 即显示“是”；等于 200 ms 显示“否”。
缺少信号或没有匹配边沿时显示“否（无有效样本）”，悬停结果可看原因及有效/跳过文件数量；
这个“否”不代表已验证同步正常。失败或取消且未完成检查时不会显示“否”。
PNG 和逐事件 JSON 保存在本次报告目录；旧版 EXE 需要重新打包后才显示新增队列列。
延迟沿用独立同步检查的同向边沿匹配（最大窗口 ±10 s），时间差为 ETH 减总线，
使用原始时间戳，不自动校准时间轴，不跨文件配对；它包含采样、处理和时钟偏差，不等同于纯传输延迟。
检查依赖 J6B 环境中的 Pillow 及 Windows 中文字体。检查异常会在独立检查任务日志中明确报错，
并回填到原 J6B 任务的延迟检查列，已经输出的挖掘/对比报告仍保留。
个别 H5 读取失败时记录完整路径及原始错误，继续处理其他文件并保留结果图；
队列显示“检查不完整”，或已经发现超限时显示“是（结果不完整）”，不伪装成完整的否。
逐文件错误保存在诊断 JSON 的 `read_errors`。信号定位支持单层采集目录包装，
只进入 LCC 目标报文分支，不再递归扫描全部报文及 Topic。

### MMT 挖掘

对应 [MMT_UnifiedMining/DataMining.py](../MMT_UnifiedMining/DataMining.py)，
运行方式等价于：

```bash
python MMT_UnifiedMining/DataMining.py --config <临时生成的config.json> --resume auto
```

MMT 挖掘页也可在运行参数中选择动力形式（`ICE`、`PHEV` 或 `BEV`）；所选值会覆盖模板配置中的
`powertrain_type`、保存在任务预设中，并传入指标判定和后续自动对比报告。

同样以模板配置合并覆盖的方式生成临时配置；`pipelines` 内每个 pipeline 的
`func_folder_path` 等相对路径会在写出前自动转换为绝对路径，避免因为临时文件
不在原目录下而解析失败。

`续跑模式` 只提供 `auto`（配置和指标代码未变化时自动续跑未完成任务）和
`never`（总是从头运行）两个选项，不提供 `ask`，因为 `ask` 需要脚本从标准输入
读取用户确认，图形界面下的子进程无法交互应答。

同 J6B 一样，表单下方也有“挖掘完成后自动生成对比报告”开关，逻辑相同（捕获
stdout 里 `统计报告已生成：<路径>` 这一行作为 `--target`，`--report-profile
MMT`）。勾选了 `dry-run` 时不会生成报告文件，因此即使勾选了自动对比也会被
自动跳过。

MMT 使用同样的命名、归档和表头识别规则，日期使用“查询日期”，车型和报告软件版本
随预设保存。“软件版本过滤”仍仅用于筛选数据，与报告软件版本输入独立。

### 对比报告

对应 [CompareKPI_United/mpi_cpi_compare.py](../CompareKPI_United/mpi_cpi_compare.py)，
表单字段和该脚本的命令行参数一一对应（`--base` / `--target` / `--template` /
`--output` / `--details-sheet` 等）；表头自动从报告文件名识别。这个
页签既可以手动单独运行对比（例如对比两次历史报告），也是 J6B / MMT 挖掘
任务自动触发对比时在后台实际执行的同一段逻辑；自动触发生成的对比任务会
出现在任务队列里，名称形如“对比报告（自动）- <来源任务名>”，可点开日志
查看其完整命令行和执行输出。

单独生成对比报告时，请在此页选择对应的动力形式；该选项也随预设保存。
筛选标准列优先使用模板中该动力形式对应的标准，不会用旧固定阈值覆盖模板。
三种动力形式分别维护独立标准：ICE 沿用原 ICE 标准，PHEV 和 BEV 的初始标准各自沿用原
PHEV/BEV 标准。旧预设中的 `PHEV/BEV` 会提示警告并迁移为 `PHEV`，纯电车辆请明确选择 `BEV`；
旧预设未填写该字段时，J6B 默认 ICE，MMT 和对比页默认 PHEV。

J6B、MMT 对比报告默认命名为
`车型-Base软件版本-Target软件版本-YYYY-MM-DD-HH-MM.xlsx`，例如
`E2LB-2-V1.2.2-V1.2.3-2026-09-20-15-30.xlsx`。
对比报告文件名只保留车型（如 `E2LB-2`），表头及原始报告归档文件名仍保留车辆编号。
时间取当前版本原始挖掘 XLSX 的最后修改时间（归档复制保留该时间），在报告转换前读取。
未选择基线时 Base 版本为“无基线”；默认名称重名时追加序号。显式指定输出文件名时仍使用指定名称。

## 任务队列与进度

- 提交任务后立即出现在队列面板里，状态为“排队中”；当运行中的任务数低于
  当前设置的最大并发数时自动开始运行。
- 进度解析基于对既有脚本 stdout 的正则匹配（如 `[3/42]` 这种当前/总数样式的
  行、以及“Report written:” / “统计报告已生成” / “已生成：”等完成标记行），
  不依赖、不需要修改既有脚本的输出格式。同一套解析逻辑还会捕获“Report
  written: <路径>”/“统计报告已生成：<路径>”里的实际报告路径，用于挖掘完成
  后自动触发对比报告任务。
- 点击“查看日志”可以打开一个持续追加的日志窗口；点击“取消”可以立即结束
  排队中或运行中的任务。

## 打包为可执行文件

Windows：

```powershell
cd UnifiedApp
pip install -r requirements.txt pyinstaller
powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1
```

生成 `dist/UnifiedMiningApp.exe`。

Ubuntu / Linux：

```bash
cd UnifiedApp
chmod +x packaging/build_linux.sh
./packaging/build_linux.sh
```

`build_linux.sh` 会自动完成：

1. 若不存在虚拟环境，在 `UnifiedApp/.venv` 下自动创建一个（也可以传入自定义
   Python 路径：`./packaging/build_linux.sh /path/to/python`）；
2. 自动 `pip install` `requirements.txt`（PySide6）以及 `pyinstaller`；
3. 在检测到 `apt-get` 且具备 root/sudo 权限时，尽量自动安装 PySide6/Qt 运行
   所需的系统共享库（`libegl1`、`libxkbcommon0`、`libxcb-cursor0` 等）；
   如果这一步因权限或网络原因失败，不会中断打包，只会打印手动安装提示；
4. 执行 PyInstaller 打包并生成 `dist/UnifiedMiningApp`。

无需提前手动安装任何依赖，直接运行脚本即可完成从环境准备到打包的全过程。

两个平台打包出的程序都只是 UnifiedApp 自身（GUI + 调度逻辑），仍然需要机器上
另外安装好可以运行 J6B / MMT / CompareKPI 三个工程各自依赖的 Python 环境，
并在“环境设置”里指向对应的 Python 解释器。

修改应用参数或自动对比逻辑后，Windows 和 Ubuntu 都需要分别重新打包；已存在的 dist
不会自动更新。还需要同步三个业务工程的代码及对比模板到运行机器上的实际工程目录。

## 动力形式回归测试

在仓库根目录执行（测试环境需同时具备 PySide6、openpyxl 和 J6B/MMT 挖掘依赖）：

```text
python -m unittest discover -s UnifiedApp\tests -v
```

测试覆盖 J6B/MMT 三种动力形式的界面取值、临时配置、预设、起步 jerk 判定及自动对比参数传递，
并以合成挖掘结果实际调用对比脚本生成 Excel，逐行检查筛选标准。它不替代真实 H5 数据的挖掘验证。

## 目录结构

```text
UnifiedApp/
  main.py                     # 入口
  requirements.txt
  unified_app/
    paths.py                  # 仓库根目录探测、Python 解释器与设置持久化
    job_spec.py                # 三类任务的参数数据结构
    config_writer.py           # 合并模板 config.json 并绝对化相对路径
    progress_parser.py         # 从子进程 stdout 解析进度
    job_manager.py              # 任务队列 + 并发调度（最多 3 个子进程）
    presets.py                  # 参数预设保存/加载
    ui/
      main_window.py
      j6b_form.py / mmt_form.py / compare_form.py
      job_panel.py             # 任务队列面板
      log_dialog.py            # 单任务日志弹窗
      settings_dialog.py       # 环境设置
  packaging/
    UnifiedApp.spec
    build_windows.ps1
    build_linux.sh
```

预设文件和环境设置保存在用户目录下的 `~/.unified_mining_app/`（Windows 是
`%USERPROFILE%\.unified_mining_app\`），与仓库代码分离，不会被打包进可执行文件。
