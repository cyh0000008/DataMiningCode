# acc_unreasonable_low_speed_count 指标说明

## 1. 指标目标

该指标用于识别 ACC 已开启、驾驶员未油门接管、前方没有需要低速跟随或避让的目标、也不是红绿灯停车或曲率限速导致的情况下，车辆表显速度长时间稳定低于 ACC 设定速度的场景。

换句话说，指标关注的是：车辆本应可以按 SetSpeed 行驶，但实际稳定行驶速度明显偏低的“不合理低速”。

## 2. 指标分类

- 指标名称：`acc_unreasonable_low_speed_count`
- 指标来源：`can`
- 指标分类：`MPI`
- 代码入口：`metrics/can/code/acc_unreasonable_low_speed_count.py`
- 指标包装入口：`metrics/can/acc_unreasonable_only/acc_unreasonable_low_speed_count.py`

## 3. 使用数据

### 3.1 CAN / H5 信号

- `PPEI_Vehicle_Speed_and_Distance_209_M1.IVehSpdAvgDrvn_1`
  - 自车实际车速。
  - 代码会复用 `acc_set_speed_reach` 中的 CLEA bias 表，将实际车速换算为表显车速。

- `Adaptive_Cruise_Disp_Stat.IACCDrvrSeltdSpd`
  - ACC 驾驶员选择的 SetSpeed。
  - 同样换算为表显速度口径。

- `Adaptive_Cruise_Disp_Stat.IACCAct376`
  - ACC active 状态。

- `PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv`
  - 驾驶员油门 override 状态。

### 3.2 Topic / TXT 数据

- `_perception_fusion_object_auto.txt` 或 `_perception_fusion_object.txt`
  - 用于判断是否存在前方风险目标。

- `_unp_planning_info.txt`
  - 用于判断红绿灯停车、曲率限速等规划原因。
  - 当前数据段缺少该文件时，整段跳过，不输出不合理低速 case。

## 4. 时间对齐

H5 时间戳单位为秒，topic 文本中的 `stamp` 或 `sensor_timestamp_us` 使用毫秒或微秒时间基。实现中会将 H5 时间转换为毫秒后，与 topic 帧做最近邻匹配。

- 默认 topic 匹配容差：`300ms`
- 如果最近 topic 帧超过容差，该采样点不参与有效场景判断。
- 如果 fusion topic 无匹配帧，则无法确认“无前车风险”，该采样点也不会作为有效候选点。

## 5. 有效采样点条件

一个采样点必须同时满足以下条件，才会进入后续 6 秒稳定低速窗口判断：

- ACC active：`IACCAct376 == true`
- 驾驶员未油门接管：`IAccPdlOvrrdAtv == false`
- SetSpeed 原始值大于 0
- 不存在前方风险目标
- 不是红绿灯导致的停车或低速
- 没有低于 SetSpeed 的曲率限速约束

## 6. 前方风险目标排除规则

fusion object 中只考虑可用目标：

- `available == True`
- 横向距离满足：`abs(relative_position.y) <= 1.5m`

满足以下任一条件，即认为存在前方风险目标，该采样点排除：

- 近距离前车：`0m < relative_position.x < 5m`
- TTC 风险目标：`1m <= relative_position.x <= 50m` 且 `crash_risk_ttc < 8s`

说明：

- 近距离前车即使 TTC 大于 8 秒，也会被排除。
- 这样可以避免把真实跟车、近距离停车队列等场景误判为不合理低速。
- cut-in 信号不再参与该指标判断。

## 7. 红绿灯停车排除规则

从 `_unp_planning_info.txt` 的 `traffic_light_decider_info` 中读取：

- `stop_flag`
- `stop_distance`
- `traffic_light_status`
- `raw_traffic_light_status`

满足以下条件时，认为是红绿灯相关停车或低速，该采样点排除：

- `stop_flag == true`
- 且 `stop_distance <= 150m`

如果 `stop_flag == true` 但 `stop_distance` 缺失，则按保守策略排除该采样点。若 `stop_distance < 0`，当前实现不认为它是有效红绿灯停车距离。

## 8. 曲率限速排除规则

从 `_unp_planning_info.txt` 的 `curvature_info` 中读取：

- `is_curv_v_limit_enable`
- `curv_velocity_limit`

判断逻辑：

- 未开启曲率限速时，采样点允许进入候选。
- 曲率限速值小于等于 0 时，采样点允许进入候选。
- 曲率限速开启且 `curv_velocity_limit_kph < selected_set_speed_kph` 时，认为低速可能由曲率限速导致，该采样点排除。

## 9. 不合理低速窗口判定

在连续有效采样点中，按 SetSpeed 不变的区间搜索 6 秒窗口。一个窗口必须同时满足：

- 窗口持续时间：`duration >= 6s`
- 窗口内 SetSpeed 保持不变
- 表显车速最低值：`v_min > 5kph`
- 表显车速最高值：`v_max > 5kph`
- 表显车速波动：`v_max - v_min <= 2kph`
- 低于设定速度：`SetSpeed - v_max >= 5kph`

满足条件后，该窗口形成一个不合理低速候选场景。

说明：

- `v_min > 5kph` 和 `v_max > 5kph` 用于排除静止、起步前等待、红灯停车残留等低速或零速片段。
- 使用 `SetSpeed - v_max` 判断低速差值，是为了确保整个稳定窗口即使取最高车速，仍然明显低于 SetSpeed。

## 10. 场景合并规则

### 10.1 单段数据内部合并

同一个 h5 或同一次分析上下文内，如果多个不合理低速窗口连续出现，会合并为同一个 case。

合并条件：

- 前一个场景结束到后一个场景开始的间隔：`<= 1s`
- 两个场景的 SetSpeed 差值：`<= 1kph`

合并后重新计算：

- `start_timestamp`
- `end_timestamp`
- `duration`
- `sample_count`
- `stable_display_speed`
- `v_min_kph`
- `v_max_kph`
- `display_range`
- `set_speed_gap_kph`
- `severity`
- `merged_window_count`

### 10.2 相邻 h5 / 相邻 done 文件夹合并

为了避免同一个连续低速场景被切成多个 bad case，fast runner 会在当前 h5 后拼接相邻连续 h5 的上下文。指标内部还会维护跨文件夹合并状态：

- 如果当前文件夹里的场景与上一个文件夹的最后场景连续，且满足 `<= 1s` 间隔与 `<= 1kph` SetSpeed 差值，则当前场景视为上一个 case 的延续。
- 延续片段不会重复计入 `All Case Count` 和 `Bad Case`。
- 输出详情中会出现：

```text
[Merged Continuation] suppressed_continuation_scenes=N
```

这表示该文件夹中有 N 个连续场景被合并到前面的 case 中。

## 11. 严重度计算

单个场景严重度：

```text
severity = max(SetSpeed - v_max, 0)
```

单位：`kph`

文件夹级统计：

- `All Case Count`：合并后的不合理低速场景数
- `Bad Case`：同 `All Case Count`
- `Severity Score`：该文件夹内所有 bad case 的最大严重度

## 12. 输出详情字段

每个场景输出类似：

```text
[Scene start=9105.235, end=9111.333, duration=6.098, set_speed=60.0, stable_display_speed=7.3, v_min=6.0, v_max=8.0, display_range=2.0, set_speed_gap=52.0, min_curvature_limit=0.0, max_topic_gap_ms=20.0, max_fusion_gap_ms=15.0, merged_windows=1, low_speed=True, severity=52.000]
```

字段含义：

- `start` / `end`：场景起止时间戳
- `duration`：场景持续时间
- `set_speed`：表显口径 SetSpeed
- `stable_display_speed`：窗口内平均表显车速
- `v_min` / `v_max`：窗口内表显车速最小值和最大值
- `display_range`：窗口内表显车速波动幅度
- `set_speed_gap`：`SetSpeed - v_max`
- `min_curvature_limit`：窗口内有效曲率限速最小值
- `max_topic_gap_ms`：UNP topic 最大匹配时间差
- `max_fusion_gap_ms`：fusion topic 最大匹配时间差
- `merged_windows`：合并后的原始窗口数量
- `low_speed`：是否命中低速判定
- `severity`：严重度

## 13. 运行方式

统一入口运行时，指标由 `DataMining.py` 根据 `config.json` 加载。

针对 `E:\数据挖掘\MMT_Data` 快速只跑不合理低速时，可使用：

```powershell
python run_mmt_unreasonable_low_speed_fast.py
```

输出目录：

```text
Result_MMT_UnreasonableLowSpeed_Fast
```

## 14. 边界说明

- 缺少当前文件夹 `_unp_planning_info.txt` 时，当前文件夹跳过。
- 缺少 fusion 匹配帧时，不会把该采样点当作“无前车风险”。
- 自车速度为 0 或低于等于 5kph 的窗口不会被判为不合理低速。
- 红绿灯 stop flag 生效且距离在阈值内时，会排除该采样点。
- 前方 5m 内有可用目标时，即使 TTC 大于 8 秒，也会排除。
- 连续 h5 中同一个不合理低速场景只统计为 1 个 bad case。
