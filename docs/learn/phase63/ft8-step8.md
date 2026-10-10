# FT8 run-loop 物理接线：vfo channelizer → 12k 抽取 → feedFt8Baseband

HEAD=d173bef。干净室自写 MIT。

## 1. 接线 file:line

| 组件 | file:line |
|---|---|
| VfoChannel::lastBaseband 成员（channelizer 复基带只读喂数源） | `vfo_manager.h:106` |
| 存储 baseband 到信道 | `vfo_manager.cpp:455` |
| run loop 4:1 抽取（48k→12k）+ feedFt8Baseband | `spectrum_engine.cpp:1423` |

**触发条件**：FT8 启用（ft8Enabled_）且选中 VFO 信道有 lastBaseband（channelizer
已产出复基带）。48k→12k 简单 4:1 抽取；抗混叠由 channelizer 12 kHz 信道带宽提供，
不额外重构。

## 2. 限制（诚实）

- 48k→12k 为硬抽采样（无抗混叠 FIR），FT8 仅 6.25 Hz 分辨率可接受；
- 仅当选中 VFO 为数字模式/有 channelizer 输出时喂数；模拟模式无 baseband → 空态；
- run loop 喂数与测试直调双路径并存，测试直调保留。

## 3. e2e 结论

test_ft8_e2e 6 passed（含喂数路径槽）；test_ui_integration 26、test_agent 37 全绿无回归。

## 4. 诚实未完成项

- UTC 15s slot 对齐（当前自由滑动窗）；
- SIC 逐帧谱减；
- 与 wsjtx 真实弱信号链路不直接类比。
