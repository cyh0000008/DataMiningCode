"""UnifiedApp: 一体化数据挖掘 + KPI 对比报告桌面应用。

本包只负责：
- 以子进程方式调用 ``J6B_UnifiedMining``、``MMT_UnifiedMining``、
  ``CompareKPI_United`` 三个既有工程里未经修改的脚本；
- 提供图形界面配置参数、管理任务队列（最多同时运行 3 个进程）、
  展示实时进度和日志。

不修改、不导入三个既有工程内部的 Python 模块，避免依赖冲突。
"""

__version__ = "1.0.0"
