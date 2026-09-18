# 数据挖掘性能优化合并说明

## 1. 文档目的

本文说明 MMT_UnifiedMining 本轮性能优化的实现方式，供其他数据挖掘项目迁移使用。

本轮修改只优化数据读取、Topic 预处理、公共场景复用和运行状态落盘，不修改指标阈值、筛选条件、严重度算法或报告字段。

涉及文件：

- DataMining.py
- longitudinal_preprocessor.py
- signal_lib.py

不需要修改 config.json。运行状态批量落盘间隔可以通过环境变量调整。

## 2. 实测效果

基于 E:\DataMiningData\20260823\20260823-094606_done 的回归测试：

| 项目 | 优化前 | 优化后 | 说明 |
| --- | ---: | ---: | --- |
| 单目录 Topic 预处理 | 约 7.025 s | 约 2.597 s | 同一轮冷热缓存条件下约 2.7 倍 |
| 当前及相邻目录 Topic 预处理 | 约 21.09 s | 约 6.59 s | 包含跨目录帧拼接 |
| Topic 指标计算 | 约 0.79 s | 约 0.20 s | 公共场景只提取一次 |
| 单目录完整处理 | 约 26.38 s | 约 9.88 s | CAN、Topic 及相邻目录处理合计 |
| Topic 场景扫描次数 | 25 次 | 10 次 | 共 27 个 Topic 指标 |

对 260 个 _done 目录，当前估算完整运行约 28 至 35 分钟。实际耗时仍受磁盘速度、系统缓存、文件大小和缺失数据影响。

## 3. 推荐合并顺序

建议按以下顺序逐项合并并回归：

1. Topic 文本分段优化。
2. H5 时间戳排序与合并优化。
3. Topic 公共场景缓存。
4. 运行状态批量落盘。

四项优化可以独立合并。逐步验证更容易定位项目间的数据格式差异。

## 4. Topic 文本分段优化

### 4.1 修改位置

文件：longitudinal_preprocessor.py

新增函数：

~~~python
def _split_timestamped_paragraphs(
    content: str,
    marker: str,
) -> List[Tuple[str, str]]:
    ...
~~~

当前用于以下六类文本：

- UNP planning info
- Fusion
- Chassis
- MSD
- Body
- MFF

### 4.2 优化原理

原实现使用多个带非贪婪匹配和前瞻的 re.findall，在大文本上重复回溯和扫描。新实现只用正则定位时间戳头部，再按照相邻匹配位置切片正文：

1. 一次线性扫描找到全部时间戳头部。
2. 当前头部结束位置到下一个头部开始位置作为正文。
3. 中间段执行 rstrip，保持原正则捕获结果。
4. 最后一段保留原有尾部语义。

其他项目中类似下面的整段正则，应替换为统一分段函数：

~~~python
re.findall(
    r"时间戳头部(.*?)(?=下一个时间戳头部|\Z)",
    full_content,
    re.DOTALL,
)
~~~

marker 必须与文本头部字段一致，例如 seq 或 header。

### 4.3 UNP raw_content

原实现对每帧原始 JSON 再格式化一次：

~~~python
raw_content = _format_json(frame_id_buffer)
~~~

修改为：

~~~python
raw_content = frame_id_buffer
~~~

影响仅限 raw_content 的缩进和空白格式。解析出的业务字段、场景判断和指标结果不变。如果目标项目把 raw_content 的排版作为外部接口，应保留旧格式，或先确认调用方不依赖缩进。

## 5. H5 时间戳优化

### 5.1 修改位置

文件：signal_lib.py

新增常量和辅助函数：

~~~python
TIMESTAMP_DUPLICATE_EPSILON = 1e-9

def _timestamps_are_unique(values: list[float]) -> bool:
    ...

def _timestamp_ranges_are_separate(
    first: list[float],
    second: list[float],
) -> bool:
    ...

def _merge_sorted_signal_samples(...):
    ...
~~~

主要修改 load_signal_dict 和 merge_signal_dicts。

### 5.2 load_signal_dict 快速路径

加载单个 H5 信号后，先判断时间戳是否非递减。时间戳已经有序时：

- 不再创建排序索引。
- 不再按随机索引重排 timestamps 和 values。
- 存在 end_timestamp 时使用 bisect_right 截断。
- 记录 timestamps_sorted。
- 同时记录 timestamps_unique，供后续合并选择快速路径。

时间戳无序时继续使用原排序逻辑，兼容历史数据。

### 5.3 merge_signal_dicts 快速路径

合并当前及相邻 H5 信号时分三种情况：

1. 两段都有序、时间范围不重叠且时间戳唯一：直接拼接。
2. 两段都有序但时间范围重叠：使用双指针进行 O(n+m) 稳定归并。
3. 任意一段无序：使用原有合并后排序逻辑。

重复时间戳按 1e-9 容差识别，并保留原实现的稳定去重语义。

### 5.4 合并注意事项

- 不要仅凭 H5 文件名假设内部时间戳有序，必须保留检测。
- 不要删除无序回退路径，历史数据可能包含乱序采样。
- timestamps 与 values 必须同步截断、拼接和归并。
- 如果目标项目已有不同的重复时间戳优先级，需要先对齐旧行为。
- 元数据只能作为本次加载结果提示，不能替代输入数据校验。

## 6. Topic 公共场景复用

### 6.1 修改位置

文件：DataMining.py

新增内容：

~~~python
_TOPIC_SCENE_FINGERPRINT_CACHE = {}
TOPIC_SCENE_FINGERPRINT_FUNCTIONS = (...)

def topic_scene_fingerprint(module: object) -> str | None:
    ...

def clone_topic_scene_segments(segments: Any) -> Any:
    ...

def run_topic_metric_with_shared_scenes(...):
    ...
~~~

process_topic_folder 中：

1. 每个 _done 目录只构建一次跨目录帧 cross_done_frames。
2. 为当前目录创建一个局部 scene_cache。
3. 所有 Topic 指标通过 run_topic_metric_with_shared_scenes 执行。

### 6.2 场景指纹

不能只按 _find_risk_segments 函数名复用，因为不同指标模块可能有同名但不同实现。

当前实现读取场景提取函数及其依赖辅助函数的 AST，规范化后计算指纹。只有场景实现完全一致的指标才共享提取结果。若某个指标修改了场景函数或依赖函数，其指纹会自动变化并退出原共享组。

当前识别的共享组：

| 场景组 | 指标数量 |
| --- | ---: |
| curvature | 3 |
| follow stop | 5 |
| slope no front | 3 |
| stop2go | 4 |
| traffic light stop2go | 5 |

场景逻辑不同的指标继续独立计算，例如 follow start 指标对、red light stop 指标对和 follow_stop_fluct。

### 6.3 缓存边界

缓存必须满足：

- 每个 _done 目录新建，不能跨目录永久复用。
- 缓存键至少包含场景指纹。
- 缓存值返回前应克隆，避免指标后处理修改公共结果。
- 缺少可识别场景函数时返回 None，按原方式执行指标。

### 6.4 并发限制

当前实现通过临时替换指标模块中的场景提取函数复用结果，适用于同一进程内顺序执行 Topic 指标。

如果目标项目要在同一进程内并发执行多个 Topic 指标，不应直接使用该方案。建议先把指标接口统一改为：

~~~python
calculate(frames, precomputed_scenes=None)
~~~

也可以使用显式的场景提供器对象，避免共享模块状态。

## 7. 运行状态批量落盘

### 7.1 修改位置

文件：DataMining.py

RunState 新增：

~~~python
self.dirty: bool
self.last_saved_monotonic: float

def mark_dirty(self) -> None:
    ...

def flush(self) -> None:
    ...
~~~

start_task 和 finish_task 不再每次重写整个状态 JSON，而是调用 mark_dirty。

### 7.2 刷盘策略

状态文件在以下时机写入：

- 距离上次保存达到配置间隔，默认 30 秒。
- 一个 _done 目录正常处理完成。
- 主流程退出、异常或用户中断时的 finally。

可使用环境变量调整间隔：

~~~powershell
$env:RUN_STATE_FLUSH_INTERVAL_SECONDS = "30"
~~~

保存仍使用临时文件加原子替换，避免进程中断产生半截 JSON。

### 7.3 性能收益与恢复语义

以 260 个目录、46 个指标为例：

- 原实现最多约 23,920 次完整 JSON 重写。
- 新实现通常约每个目录一次，加上每 30 秒安全刷盘。

恢复语义保持不变。极端情况下，中断可能让当前目录最近 30 秒内的任务状态未落盘，恢复后会重新执行这部分任务；已完整完成并刷盘的目录会继续跳过。

## 8. 回归验证

### 8.1 基础检查

~~~powershell
python -m py_compile DataMining.py longitudinal_preprocessor.py signal_lib.py
~~~

确认：

- CAN 和 Topic 指标均能完整加载。
- config.json 无需新增字段。
- 缺失 Topic 或 H5 文件时仍按原逻辑跳过。
- 指标数量与优化前一致。

### 8.2 Topic 预处理等价性

选择一个同时包含相邻目录的真实样本，分别运行优化前后预处理器：

- 帧数一致。
- 时间戳一致。
- 除 raw_content 空白格式外，全部业务字段一致。
- 所有 Topic 指标的事件数量、起止时间和问题内容一致。

### 8.3 H5 等价性

至少覆盖：

1. 已排序、无重复时间戳。
2. 已排序、有重复时间戳。
3. 无序时间戳。
4. 两段时间范围完全分离。
5. 两段时间范围重叠。
6. 空信号。
7. end_timestamp 位于首帧前、中间及末帧后。

真实 H5 回归时，对每个信号逐项比较 timestamps 和 values。

### 8.4 场景缓存等价性

关闭和开启场景缓存各运行一次，比较全部 Topic 指标：

- 事件数量一致。
- 每个事件开始和结束时间一致。
- 指标值及严重度一致。
- 报告行一致。

同时记录场景提取函数调用次数，确认相同指纹只执行一次。

### 8.5 运行状态恢复

1. 启动完整挖掘。
2. 在处理中断进程。
3. 使用相同配置重新启动。
4. 选择继续上次运行。
5. 确认已完成目录跳过，未完成目录重新处理。
6. 检查最终报告不存在重复行。

## 9. 合并检查清单

- [ ] 目标项目的数据文本头部格式与 seq/header 正则一致。
- [ ] 目标项目没有依赖格式化后的 raw_content。
- [ ] H5 values 与 timestamps 始终等长。
- [ ] 重复时间戳处理语义与当前项目一致。
- [ ] Topic 指标在单进程内顺序执行。
- [ ] 场景缓存是目录级局部变量。
- [ ] 场景结果返回前执行克隆。
- [ ] 状态保存使用临时文件和原子替换。
- [ ] 主循环在 finally 中强制 flush。
- [ ] 合并后完成真实数据全指标回归。

## 10. 暂未实施的进一步优化

下一阶段收益最大的方向是按 _done 目录进行多进程并行。该方案尚未实施，因为需要先统一处理：

- Excel/JSON 报告并发写入。
- 运行状态跨进程同步。
- 相邻目录数据读取和缓存。
- 内存峰值和磁盘并发压力。
- 结果顺序和去重。

引入目录级并行前，不建议在同一进程内并发执行 Topic 指标，因为当前公共场景复用包含模块函数临时替换。

## 11. 回滚边界

四项优化相互独立，可以分别回滚：

- Topic 解析异常：恢复六处原 re.findall 分段逻辑。
- H5 合并异常：恢复 load_signal_dict 和 merge_signal_dicts 的统一排序路径。
- 场景缓存异常：在 process_topic_folder 中直接调用原指标入口。
- 状态恢复异常：让 start_task 和 finish_task 恢复为每次立即保存。

回滚性能优化时不需要修改指标实现或筛选标准。
