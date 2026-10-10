# P2 LRPT 第③轮·线 B：三通道工具 set_lrpt / get_lrpt_status

> HEAD=97bec94 起。本轮把 LRPT 卫星云图接收的**状态控制层**接进三通道（C++ 状态层 + schema/executor/dispatch/hub + mobile catalog + phase31 文档）。
> **诚实边界**：LRPT 解码链（QPSK 解调 + Reed-Solomon/ASU FEC）当前为 Python 原型（`mbdsdr_ai/lrpt_modem.py` + `lrpt_fec.py`，第①②轮）；C++ 移植在**第④轮**。本轮工具**不是空壳**——`set_lrpt` 真实翻转引擎使能标志、写门真实拦截、缺参/越界 `ok:false` 诚实拒绝；`get_lrpt_status` 读回**诚实空态**（C++ 解码器未移植前 `sync_locked=false`、`decoded_frames=0`，绝不编造图像/帧）。

## 1. C++ 最小状态层（SpectrumEngine）

镜像 FT8 "检测层/状态层，解码留后续" 的诚实空态先例：

- 读回 `spectrum_engine.h`：`lrptEnabled()` / `lrptSyncLocked()` / `lrptDecodedFrames()`（默认 false / false / 0）。
- setter `setLrptEnabled(bool on)`（`spectrum_engine.cpp`）：只 arm 期望标志，不编造统计。
- 成员：`std::atomic<bool> lrptEnabled_{false}; bool lrptSyncLocked_{false}; int lrptDecodedFrames_{0};`（后两者为第④轮 C++ 解码器将真实驱动的诚实占位）。

## 2. 三通道 file:line

| 通道 | set_lrpt（写） | get_lrpt_status（读） |
|---|---|---|
| tool_schema.cpp | `set_lrpt`（write=true，required `enabled` bool） | `get_lrpt_status`（read） |
| agent_tools.cpp | `execSetLrpt`（缺 enabled→errResult） | `execGetLrptStatus`（读 enabled/sync_locked/decoded_frames）+ dispatch 注册 |
| control_hub.cpp | 写表 `{"set_lrpt",true,&cmdSetLrpt}` + `cmdSetLrpt`（needBool 校验） | 读表 `{"get_lrpt_status",false,&cmdGetLrptStatus}` + `cmdGetLrptStatus` |
| control_hub.h | `cmdSetLrpt` / `cmdGetLrptStatus` 声明 | 同左 |
| HTTP | POST /command 透传（无新路由） | 同左 |

越界/缺参：缺 `enabled` 或非布尔 → `ok:false` 诚实拒绝；写工具在手动模式 gate 拦截（`gated:true`、引擎不动）。

## 3. 金集计数（offscreen 真实 passed）

| 二进制 | 本轮前 | 本轮 |
|---|---|---|
| test_tool_registry | 8 | **8 passed, 0 failed**（schema 数 53→55，spot check +2） |
| test_tool_schema | 10 | **10 passed, 0 failed**（description map +2、headerCount 53→55） |
| test_agent | 36 | **38 passed, 0 failed**（+`lrptLandReadbackHonestEmpty`；tools 53→55、写/读 32/21→33/22） |
| test_control_hub | 31 | **33 passed, 0 failed**（+`lrptSetLandHonestEmpty`） |
| test_control_http | 15 | **15 passed, 0 failed**（+LRPT HTTP 透传断言） |

## 4. mobile catalog 53→55

- `tool_catalog.dart`：+`set_lrpt`(write) / `get_lrpt_status`(read) 逐名对齐注册序。
- `tools_catalog_test.dart`：53→55（length / names.toSet / 头部"桌面端共 55 个"）。
- `tools_catalog_page.dart` 注释 53→55。
- `buildRadioTools` 保持 10（移动端无 LRPT 解调链，桌面独有，诚实标注）。

## 5. phase31 文档（agent-tool-documentation.md）

- 一次性脚本 `cpp/scratch/regen_tool_doc_ctcss` 是**预编译 ELF**，工具清单在其自身编译期固化（跑一次仍吐 "53 个工具"、无 lrpt）；重跑需从 `scratch/regen_tool_doc.cpp` 重编译，而 scratch 为隔离文件**本轮不碰**。
- 按任务"脚本不可复用则如实留手工"：**手工**在 ft8 块后补入 `set_lrpt`/`get_lrpt_status` 两段（description/schema/错误示例格式对齐既有），头部计数改 55。结论：**55 工具手工落档**。

## 6. 诚实未完成项

- LRPT 解调/FEC（QPSK Costas 同步、Viterbi/R-S 解扰、图像拼帧）的 **C++ 移植 = 第④轮**；当前 `sync_locked`/`decoded_frames` 恒为诚实空态（false/0），不由 Python 原型桥接。
- 未做 LRPT 图像预览 UI、未接 demod 数据流（本轮无 UI 任务）。
- phase31 为手工补段（预编译脚本未重编译），第④轮补完解码链后建议从 scratch 源码重编译 regen 脚本一次性再生成。
