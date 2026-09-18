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

表单下方的“挖掘完成后自动生成对比报告”默认勾选：挖掘成功后会从 stdout 里
`Report written: <路径>` 这一行捕获本次生成的报告路径，作为对比报告的
`--target` 自动提交一个对比任务（`--report-profile J6B`）。可选填写一个
“基线报告”路径作为 `--base`（用于和历史结果对比），留空则对比报告只展示
本次挖掘结果。若挖掘任务失败/被取消，或者未能从输出中捕获到报告路径，则
不会触发自动对比（后一种情况会在任务日志里给出明确提示，可在“对比报告”
页签手动补跑）。

自动对比区域还可以分别覆盖 Base / Target 的车型、日期和软件版本表头。这些字段
留空时仍由对比脚本根据报告文件名自动识别，并会随 J6B 参数预设一起保存。

### MMT 挖掘

对应 [MMT_UnifiedMining/DataMining.py](../MMT_UnifiedMining/DataMining.py)，
运行方式等价于：

```bash
python MMT_UnifiedMining/DataMining.py --config <临时生成的config.json> --resume auto
```

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

MMT 自动对比同样支持覆盖 Base / Target 的车型、日期和软件版本表头，相关值会
随 MMT 参数预设一起保存。

### 对比报告

对应 [CompareKPI_United/mpi_cpi_compare.py](../CompareKPI_United/mpi_cpi_compare.py)，
表单字段和该脚本的命令行参数一一对应（`--base` / `--target` / `--template` /
`--output` / `--details-sheet` / 表头覆盖 / `--base-source-date` 等）。这个
页签既可以手动单独运行对比（例如对比两次历史报告），也是 J6B / MMT 挖掘
任务自动触发对比时在后台实际执行的同一段逻辑；自动触发生成的对比任务会
出现在任务队列里，名称形如“对比报告（自动）- <来源任务名>”，可点开日志
查看其完整命令行和执行输出。

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
