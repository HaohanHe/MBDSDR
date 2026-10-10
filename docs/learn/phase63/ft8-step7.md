# FT8 工程性收尾：vfo 真实喂数 + 时隙对齐 + SIC 评估

HEAD=622c46a。干净室自写 MIT。

## 1. vfo 喂数 / 时隙对齐 file:line

| 组件 | file:line |
|---|---|
| feedFt8Baseband()（15s 环形缓冲，满窗触发，滑动 1s 重叠） | `spectrum_engine.cpp:319` |
| ft8Ring_/ft8RingFilled_/ft8DecodedFrames_/ft8LastDedupKey_ 成员 | `spectrum_engine.h:619` |
| 多帧去重（文本+频偏 ±6Hz 粗桶合并，不重复计数） | `spectrum_engine.cpp:317` |
| ft8DecodedFrameCount() 读回 | `spectrum_engine.h:133` |
| get_ft8_status 新增 decoded_frame_count 字段 | `agent_tools.cpp:971` |

**内存/CPU 成本**：ft8Ring_ = 180000 复样本 × 8B ≈ 1.44MB（启用时常驻）；
满窗后一次 processFt8Window（Costas 粗搜 + BP 50 迭代）CPU 增量约 15s 一次。

**接入点约束（诚实）**：当前 feedFt8Baseband 为公开入口，测试直调已验证；
vfoManager_.process（channelizer 后 48k）→ 12k 二次抽取 → feedFt8Baseband
的 run loop 接线**未接**（IF 率/带宽约束下 12k 窄带抽取链待验）。当前触发路径
为测试分块喂入；真实硬件 run loop 喂数留下一步。

## 2. 喂数路径 e2e 结论（test_ft8_e2e 6 passed）

- feedPathRingBufferDecodes：合成 IQ 按 12000 样本/块分块喂入 → 环形缓冲满窗 →
  检出 + decoded_text == "K1ABC K2DEF EM12" + decoded_frame_count >= 1；
- 既有直调路径 decodesSyntheticFrame 保持全绿；
- 纯噪声/未启用诚实空态保持。

## 3. 时隙对齐

- 滑动窗：满窗后保留最近 1s（12000 样本）作重叠，避免 15s 硬切丢失跨边界帧；
- 去重：相同 decoded_text + 相近频偏（±6Hz）合并，ft8DecodedFrames_ 只增不重；
- **诚实边界**：本地自由运行窗，与 wsjtx UTC 15s slot 不直接类比（未对齐
  UTC 0/15/30/45s 边界）。

## 4. SIC 评估（未落地，文档排期）

SIC（逐帧迭代谱减，镜像 wsjtx subtractft8 概念）本轮**未落地**：工作量超弹性
token。评估：最小集需在解码最强帧后重构 8-tone 信号并从复基带谱减，再二次
Costas 检测——涉及检测层/缓冲层改动较大，诚实排期到下一轮。当前单信号检测。

## 5. 金集 92 保持

无新工具；get_ft8_status 仅扩 decoded_frame_count 字段。
test_tool_registry 8、test_agent 37、test_control_http 全绿；mobile catalog 53 不变。

## 6. ctest

ft8_e2e 6（含新喂数路径槽）；ft8 三套件合计 detector 5 + codec 6 + e2e 6 = 17 全绿。

## 7. 诚实未完成项

- vfo channelizer → 12k 抽取 → feedFt8Baseband run loop 接线；
- UTC 15s slot 对齐（当前自由滑动窗）；
- SIC 逐帧谱减；
- 与 wsjtx 真实弱信号链路不直接类比（合成闭环）。
