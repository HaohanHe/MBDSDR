# 工具依赖地图与典型任务链（phase67 · 工作流程轴）

> **定位**：本文件是 MBDSDR AI 工具面的**工作流视图**——不只是「有哪些工具」，
> 更是「先做什么后做什么、失败时退到哪、谁会改变谁」。逐工具的权威契约以
> `mbdsdr_ai/sdr_tools.py` / `multimon_decoders.py` 注册表中的描述为准
> （DSH 四维：前置条件/失败恢复/副作用/能力边界），`test_tool_description_quality.py`
> 门禁防其腐化；本文件回答的是描述不回答的问题：**调用次序与任务组合**。
> **生成方式**：2026-10-12 从真实注册表（147 个内置工具）人工核对梳理。
> 新增核心工具时同步更新本表。

## 1. 依赖图（核心链路）

```
                    ┌─────────────────────────────┐
                    │  sdr_list_devices（枚举）    │  ← 一切硬件操作的入口
                    └──────────────┬──────────────┘
                                   │ device_id
                                   ▼
   ┌──────────┐  connect   ┌─────────────┐  set_frequency  ┌──────────────┐
   │  IQ 文件  │ ─────────→ │  sdr_connect │ ─────────────→ │  sdr_set_* 组 │
   │ (离线源)  │  open_iq   └─────────────┘                │ 频率/采样率/  │
   └──────────┘              │ 失败=如实报错                 │ 增益/带宽/    │
                             ▼                             │ 静噪/解调     │
                    ┌─────────────────────┐                └──────┬───────┘
                    │  sdr_status（只读）   │                       │
                    └─────────────────────┘                       ▼
                                              ┌──────────────────────────┐
                                              │  sdr_spectrum_analyze     │
                                              │  sdr_sweep_scan（找台）    │
                                              └────────────┬─────────────┘
                                                           ▼
┌──────────────────┐   vfo_select   ┌─────────────────────────────┐
│ vfo_add/list     │ ─────────────→ │  定频收听 / sdr_record_start  │
│ （预置守听点）    │                └──────────────┬──────────────┘
└──────────────────┘                               │ recording_path
                                                  ▼
                              ┌────────────────────────────────────┐
                              │ sdr_record_stop → sdr_recordings_list │
                              │ → sdr_open_iq_file（回放源）         │
                              │ → pocsag_/dtmf_/zvei_/afsk1200_*   │
                              └────────────────────────────────────┘
```

**三条铁律**（从四维契约归纳，模型最容易违反的三点）：

1. **硬件写操作前必须已连接**：`sdr_set_*` / `vfo_select` / `sdr_record_start`
   未连接时全部如实报错——先 `sdr_connect` 或 `sdr_open_iq_file`，不要重试同一调用。
2. **device_id / VFO 名 / 书签名必须来自枚举**：`sdr_list_devices` / `vfo_list` /
   书签列表是唯一事实来源，**禁止凭记忆构造 id 或名称**。
3. **产物用透传字段接续**：`sdr_record_start/stop` 返回的 `recording_path` 是
   解码链与工作流 `{{recording_path}}` 模板的唯一接力点，不要凭路径规则猜文件名。

## 2. 核心工具契约速查（32 个，按调用序）

> 「—」= 该维度无实际约束（纯查询/无副作用），不是漏写；四维齐全要求只对
> 状态变更型工具生效（见门禁测试 `_STATE_MUTATORS`）。

| 工具 | 前置条件 | 副作用 | 失败恢复 |
|---|---|---|---|
| `sdr_list_devices` | 无（本机枚举） | — | 空列表=无硬件，如实空态，**不要虚构设备** |
| `sdr_connect` | 有可用设备 | 独占占用设备（GQRX 等无法同时打开） | 失败→先 list_devices 再用返回 id 重试，**不会伪造连接** |
| `sdr_switch_device` | device_id 来自 list_devices | 中断进行中的接收/录制 | 失败后设备保持断开→重新 connect |
| `sdr_disconnect` | 无（幂等） | 停止所有接收/录制 | 未连接也安全返回已断开 |
| `sdr_status` | 无 | —（只读） | 未连接返回未连接状态而非报错 |
| `sdr_set_frequency` | 已连接/已开 IQ 文件 | 改变硬件调谐频率 | 超范围→用 list_devices 的频率范围核对，不要重发同值 |
| `sdr_set_sample_rate` | 已连接/已开 IQ 文件 | 影响带宽上限与录制码率 | 失败→核对支持范围后重试 |
| `sdr_set_gain` | 已连接/已开 IQ 文件 | **关闭 AGC** | 超范围返回「操作失败」→核对增益范围 |
| `sdr_set_agc` | 已连接/已开 IQ 文件 | 开启会覆盖手动增益 | 设备不支持→改用手动 set_gain |
| `sdr_set_bandwidth` | 已连接/已开 IQ 文件 | 影响频谱分辨率/音频带宽 | 返回「操作失败」→换合法值，勿重试同值 |
| `sdr_set_squelch` | 已连接/已开 IQ 文件 | 仅静音音频，不动 IQ | 设备不支持→改解调侧滤波 |
| `sdr_set_volume` | 无 | 立即生效 | 无声卡时设置成功但不发声（play_audio status 查询） |
| `sdr_set_demod` | 已连接/已开 IQ 文件 | 影响音频与解码链路 | 模式无效→返回合法值列表 |
| `sdr_spectrum_analyze` | 已连接/已开 IQ 文件 | —（只读） | 未连接如实报错 |
| `sdr_sweep_scan` | 已连接/已开 IQ 文件 | 临时改频，结束回原频率 | 离线 IQ 单段源不能扫频（如实说明） |
| `vfo_add` | 无（可预置） | 写本地 VFO 列表（持久化） | 超设备频率范围→返回错误 |
| `vfo_list` | 无 | — | 空列表=未添加，先 vfo_add |
| `vfo_select` | 已连接 + VFO 存在 | 改变当前频率与解调 | 未连接→先连；VFO 不存在→list 核对名称 |
| `vfo_remove` | VFO 存在（名称精确匹配） | 移除预设（持久化） | 不存在→返回未找到，核对名称 |
| `sdr_record_start` | 已连接/已开 IQ 文件 | **持续写盘**（cf32≈8MB/s @2MS/s） | 「设备未连接」→连了再试；写盘失败→查权限/磁盘 |
| `sdr_record_stop` | 无 | 落盘关句柄，之后 start 生成新文件 | 未在录制→返回提示，先 recordings_list 核对 |
| `sdr_recordings_list` | 无 | —（只读） | 目录不存在=无文件提示而非报错 |
| `sdr_open_iq_file` | path 存在且格式受支持 | 切换当前设备为回放源 | 无元数据格式必须传 sample_rate_hz，否则频率轴整体偏移 |
| `pocsag_decode` | codewords 非空（17 个/批） | — | 空数组→明确错误，不伪造解码 |
| `pocsag_bch_correct` | codeword 为 32-bit 整数 | — | errors_corrected=0=无错/不可纠 |
| `dtmf_decode` | samples 数字数组 + 正确 sample_rate | — | 空/非数组→明确错误；解不出→success=false |
| `zvei_decode` | 同上 | — | 同上 |
| `afsk1200_demod` | samples 数字数组（~8kHz 佳） | — | 空数组→明确错误；无帧→不虚构文本 |
| `radio_set_mode` | 电台已连接 | 影响音频与解码链路 | 未连接/模式无效→返回合法值 |
| `radio_set_frequency` | 电台已连接 | 改变电台实际接收频率 | 超范围→返回硬件频段范围 |
| `radio_ptt` | 电台已连接且有发射能力 | **实际发射射频** | 只收电台→返回不支持，不虚构发射成功 |

## 3. 典型任务链（Goal → Mechanism，DSH 式）

| 目标 | 调用链 |
|---|---|
| 听本地调频广播 | list_devices → connect → set_frequency(如 98.5MHz) → set_demod(WFM) → set_bandwidth(150000) → set_volume |
| 找正在发射的台 | connect → sweep_scan(起止频率) → 对命中频点 set_frequency → set_demod |
| 无人值守抓突发信号 | connect → watch_capture(频率/书签) → 停止后 recordings_list 拿路径 |
| 解码 POCSAG 寻呼 | record_start → record_stop →（离线）pocsag_decode(codewords) 或经解调链路 |
| 离线复现历史录制 | recordings_list 选文件 → open_iq_file(**必须传 sample_rate_hz**) → spectrum_analyze / 解码工具全家 |
| 卫星过境守听 | 设置页配测站坐标 → sky 页过境预测 → bookmark_goto(ISS 等) → set_demod → （可选）多普勒自动补偿 |
| 只读体检再动手 | status →（按返回的真实频率/模式/增益决策）→ 才发写操作 |

## 4. 与描述契约、门禁测试的关系

```
sdr_tools.py / multimon_decoders.py 描述（DSH 四维，权威契约）
        │ 被读取
        ▼
test_tool_description_quality.py（地板 + 状态变更型四维齐全 + 棘轮覆盖率）
        │ 通过即允许提交
        ▼
本文件（工作流视图：依赖图 + 任务链，人工维护，回答「次序与组合」）
```

- 描述改坏了 → 门禁测试红；
- 新增核心工具 → 门禁参数表（`_STATE_MUTATORS` / `_QUERIES`）与本文件同步补；
- 门禁阈值是**棘轮**（只升不降），当前实测：任一维度 29.3% / 前置条件 18.4% /
  失败恢复 21.1% / 副作用 8.2% / 能力边界 10.2%（147 工具，2026-10-12）。
