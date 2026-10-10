# FT8 第③步落地：C++ 检测层 + 三通道工具

HEAD=1c78080。干净室自写 MIT，不抄 WSJT-X GPL；Python 原型
(`mbdsdr_ai/ft8_modem.py`/`ft8_codec.py`) 为自写基准。

## 1. 检测层落地 file:line

| 组件 | file:line |
|---|---|
| Ft8Candidate 结构 / Ft8Detector 类 | `cpp/src/dsp/ft8_detector.h:20`/`:33` |
| Costas 二维粗搜（符号对齐起始 × 频偏 ±50 Hz @ 6.25 Hz，第一段 7 符号） | `cpp/src/dsp/ft8_detector.cc:58` |
| 8-tone 能量 DFT 匹配 | `cpp/src/dsp/ft8_detector.cc:24` toneEnergy |
| 诚实空态（quality<0.5 → valid=false；纯噪声零候选） | `ft8_detector.cc:88` |
| 引擎接线（ft8Enabled_/ft8Last_/setFt8Enabled） | `cpp/src/dsp/spectrum_engine.h:115`、`.cpp:296` |

**检测层 vs 解码层现状**：本轮落地 Costas 粗同步 + 候选帧统计（频偏/时偏/
syncQuality/candidate_count）。C++ BP 解码（tanh + CRC14 + 77-bit unpack）
**留第④步**；本轮不编造解码消息，无信号 → active=false。

## 2. 三通道工具 file:line

| 通道 | set_ft8（写） | get_ft8_status（读） |
|---|---|---|
| tool_schema.cpp（spec） | `:646` | `:660` |
| agent_tools.cpp（executor+dispatch） | `:941` execSetFt8、`:1559` dispatch | `:954` execGetFt8Status、`:1560` |
| control_hub.cpp/h（命令表+handler） | `:117` 表、`.cpp:1449` cmdSetFt8 | `:156` 表、`.cpp:1457` cmdGetFt8Status |

HTTP POST /command 自动透传，无新路由。写 gate 镜像 set_ctcss/set_cdcss
（手动模式 gatedResult，引擎内部不 clamp 越界——工具层才是诚实门）。

## 3. 金集 90→92 / mobile 51→53

- test_tool_registry：8 passed（schemas.size 51→53，写断言 +set_ft8/get_ft8_status）
- test_agent：36→37 passed（新槽 ft8LandReadbackHonestEmpty；gate 计数 31→32 写/20→21 读）
- test_control_hub：31→32 passed（新槽 ft8SetLandHonestEmpty）
- test_control_http：15→16 passed（FT8 over POST /command 透传）
- mobile：`tool_catalog.dart` +2 条目（set_ft8/get_ft8_status，顺序对齐），
  `tools_catalog_test.dart` 51→53，`tools_catalog_page.dart` 注释 51→53；
  buildRadioTools 保持 10（桌面独有）。

## 4. e2e 结论

`cpp/tests/test_ft8_detector.cpp`（5 passed）：
- 合成 Costas 帧（12 kS/s，20 dB SNR，seed=42）→ valid=true，syncQuality≈1.0；
- 纯噪声（seed=7）→ valid=false（零候选，无假检测）；
- disabled → valid=false。

## 5. 诚实未完成项（第④步）

- C++ tanh BP 解码 + CRC14 早停 + 77-bit unpack（Python 原型已就绪，跨进程或 C++ 移植）；
- 真实 12k 窄带抽取链接入（vfo_manager → Ft8Detector 的运行时喂数）；
- 时隙对齐（多帧 15 s 窗）、SIC；
- UI 面 FT8 开关/状态徽标（弹性 token，本轮未做）；
- phase31 工具文档重生成（regen_tool_doc_ctcss，本轮未跑，留用户 commit 后）。
