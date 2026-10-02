<!--
SPDX-License-Identifier: MIT
-->

# P4 · Flutter ↔ 桌面（C++/Qt）一致性抽检补齐（phase8 复查轮）

> 范围：在 phase7 `Q4-cross-platform.md`（上一轮差异清单，已修 get_status 五态披露）
> 的基础上**复查是否仍有"该修"的差异**，重点是**工具集**（C++ 8 工具 vs Flutter 原 6 工具）。
> 方法：真读两端源码（file:line 见下），只把**确有必要且云内可验证**的差异落地为
> 修复 + 测试；架构性差异、真机项如实记录、不强行抹平。
>
> 环境：云 VM 无硬件。基线（本轮改动前）：`flutter analyze` = 0、`flutter test` =
> **293**；本轮改动后：`flutter analyze` = **0**、`flutter test` = **298**
> （293 基线 + 本批新增 5）。未改 C++，`ctest 91/91` 基线不受影响。
>
> 两端后端**不同**：移动端经 rtl_tcp 网络客户端，桌面端直挂 librtlsdr 本地源
> （见 Q4 文档 §背景）。这解释了增益档表等轴上的分歧——不是 bug，是协议边界。

---

## 工具集总览（本轮焦点）

| # | 桌面 C++ 工具（agent_tools.cpp） | 类别 | Flutter 工具（ai_tools.dart） | 对齐 |
|---|---|---|---|---|
| 1 | `tune_frequency` (:96) | 写 | `set_frequency` (:61) | ✅ 同义（命名差异） |
| 2 | `set_mode` (:106) | 写 | `set_mode` (:110) | ✅ |
| 3 | `start_recording` (:116) | 写 | `start_recording` (:223) | ✅ **本轮补齐** |
| 4 | `stop_recording` (:133) | 写 | `stop_recording` (:262) | ✅ **本轮补齐** |
| 5 | `scan_band` (:141) | 写 | —（无对应） | ❌ 缺 → 架构性不修 |
| 6 | `set_bandwidth` (:170) | 写 | —（带宽随模式派生） | ❌ 缺 → 架构性不修 |
| 7 | `get_status` (:180) | 只读 | `get_status` (:295) | ✅（Q4 已修字段） |
| 8 | `predict_passes` (:201) | 只读 | `predict_passes` (:324) | ✅ |

Flutter **额外**拥有（桌面 AI 工具面未暴露）：`set_gain` (:142)、`set_sample_rate` (:186)。

---

## A. 连接 / 断开状态语义

| | Flutter | 桌面 C++ |
|---|---|---|
| 状态机 | 5 态 `disconnected/connecting/connected/reconnecting/error`（radio_state.dart:7-22） | `sourceTelemetry(connected:bool)` + 三信号 `sourceDropped/sourceError/reconnectRequested`（Q4 引 spectrum_engine.h:259-272） |
| AI 披露 | get_status 同时给 `connected` 布尔 + `status` 完整状态机名 + `error_message`（ai_tools.dart:304-306） | `connected` 布尔 + 三路掉线信号 |
| 动作 gate | 动作工具 execute 前 `_disconnectError`：非 connected 回 `ok:false`（ai_tools.dart:26-31） | writeTools 在手动模式 gate（agent_tools.cpp:20-34） |

- **差异**：无新增。上一轮 Q4-D 已把 Flutter 五态机压成单布尔的问题修掉，
  get_status 现在同时披露"就绪布尔 + 完整状态机 + 错误原因"。
- **影响**：无。
- **判定**：**不修（已对齐）**。

## B. 工具集差异 ★（本轮复查重点）

### B1. `start_recording` / `stop_recording` —— 本轮修复

- **桌面现状**：两个**动作类**工具（writeTools，agent_tools.cpp:23-24）。
  `start_recording` 传播引擎真实 bool，失败回 `error`，成功回 `path`（:116-132）；
  `stop_recording` 停录并回 ok（:133-140）。
- **Flutter 修复前**：`RadioController` **本就有** `startRecording()`/`stopRecording()`
  能力（落 16-bit WAV + sidecar，radio_controller.dart:490-534），但**未接入 AI 工具集**——
  即 UI 能录，AI 不能录。这是一个真实的"能力已在、入口未开"的一致性缺口。
- **影响**：桌面 AI 可自行开始/停止录制；移动端 AI 不能，尽管应用支持。
- **判定 / 处置**：**该修，已修（云内可验）**。新增 `start_recording`/`stop_recording`
  两个动作工具（ai_tools.dart:223-293），并加入手动模式 gate 集合（:408-415，与桌面
  writeTools 一致）。关键语义对齐：
  - `start_recording`：过连接 gate（`_disconnectError`，未连接回 `ok:false`）；
    已在录回 `already_recording:true` 不重复开文件；捕获 `StateError`（未连接/未配
    录音目录）诚实回错，**绝不假装录上了**。
  - `stop_recording`：**不做连接 gate**（断连时也应能收尾落盘，与 radio_controller
    `disconnect()` 自动收尾语义一致）；未在录回 `当前未在录制`（不报错、不造文件）；
    落盘成功回 `wav_file`/`duration_ms`/`frequency_hz`/`mode`（取 RecordingMeta 真值）。
  - 产物差异保持诚实：工具 description 明写"桌面录原始 IQ（SigMF），本端录解调音频
    （WAV）"。

### B2. `scan_band` —— 缺，架构性不修

- **桌面现状**：动作类工具（writeTools，agent_tools.cpp:27）。扫频接收机跨频段、
  回峰值命中 `hits[0]`（:141-169）；测试信号上诚实标注 `synthetic:true`。
- **Flutter 现状**：**无扫频引擎**。移动端是单信道语音接收（单 `_freqHz`、单解调链，
  radio_controller.dart:168-169），没有"跨频段扫 + 峰值检测"的闭环。
- **影响**：功能范围差异，非 bug。
- **判定**：**架构性不修**。补 scan_band = 新建扫频功能（频率步进调谐 + FFT 峰值
  搜索），是特性开发而非一致性小修，且强依赖真实 IQ 流（真机项）。如实记录为范围差。

### B3. `set_bandwidth` —— 缺，架构性不修

- **桌面现状**：动作类工具（writeTools，agent_tools.cpp:25），设信道滤波带宽（:170-179）。
- **Flutter 现状**：**无独立带宽控制**。带宽由解调模式派生（spectrum_page.dart:23-27
  `bandwidthForMode`：nfm=12500、wfm=200000），代码注释明写"移动端带宽随模式而定，
  不单独控制"。Bookmark 仅记录 bandwidthHz 备查（bookmark.dart:29-30）。
- **影响**：无独立带宽旋钮是刻意设计，不是缺失。
- **判定**：**架构性不修**。加 set_bandwidth = 改造解调器暴露可变带宽，非小修；
  移动端 NFM/WFM 带宽与模式绑定，符合单信道语音接收定位。

### B4. Flutter 额外工具 `set_gain` / `set_sample_rate` —— 合理拥有

- **Flutter**：`set_gain`(:142)、`set_sample_rate`(:186)。
- **桌面 AI 工具面**：未暴露这两个（桌面增益走本地档表吸附、采样率由设备能力决定，
  不向 AI 开放写）。
- **判定**：**合理拥有，不算缺口**。移动端经 rtl_tcp 客户端**直接控制**接收机，
  增益/采样率本就是客户端控制面；桌面直挂本地源、由 UI/设备能力管理。两端各自
  暴露自己后端真实可控的旋钮，方向一致。

## C. get_status 其余字段

| 字段 | Flutter get_status（ai_tools.dart:298-309） | 桌面 get_status（agent_tools.cpp:185-199） |
|---|---|---|
| ok / connected | ✅ / ✅ | ✅ / ✅ |
| 状态机 | `status` + `error_message` | `connected` 布尔 + 三路信号 |
| frequency_hz / mode | ✅ / ✅ | ✅ / ✅ |
| 增益/采样率 | `gain_db`/`auto_gain`/`sample_rate_hz` | —（引擎不暴露，:181-184 注释明写不臆造） |
| 测试信号/来源 | — | `test_signal`/`source`（:188-189） |
| 带宽 | — | `bandwidth_hz`（:192） |
| 摘要串 | — | `summary`（:193-198） |

- **差异**：字段集不同，但**各自只报后端真实知道的值**——Flutter 无测试信号源、
  无独立带宽，故不报；桌面不暴露增益/采样率，故不臆造（:181-184 注释）。
- **影响**：无 bug。属"随后端能力披露"，不是契约漂移。
- **判定**：**不修**。两端 get_status 的共同骨架（ok/connected/frequency/mode）对齐；
  扩展字段反映各自后端能力，强行对齐 = 臆造。`summary` 摘要串为桌面 UI 便利，
  Flutter 已回结构化字段，无需补。

## D. 录音 / 回放语义

| | Flutter | 桌面 C++ |
|---|---|---|
| 录什么 | 解调后音频（过静噪门）→ 16-bit 单声道 WAV + sidecar（radio_controller.dart:490-534） | 原始 IQ → SigMF cf32_le（recorder.cpp） |
| AI 入口 | `start_recording`/`stop_recording`（本轮接入） | `start_recording`/`stop_recording` |
| 回放 | FilePlayer 走 `mbdsdr/audio` MethodChannel（真机待验） | SigMF 回放 |
| 空态 | 未在录制回"当前未在录制"；未连接/未配目录抛 StateError（已捕获回错） | 无录制→空库 |

- **差异**：产物本就不同（听到的音频 vs 可再分析基带），Q4-C 已判定不修。
- **本轮变化**：AI **入口**已对齐（B1）——此前 UI 能录而 AI 不能，现已打通。
- **判定**：**产物差异不修；AI 入口本轮已补齐；原生回放真机待验**（file_player.dart）。

## E. 增益控制

| | Flutter | 桌面 C++ |
|---|---|---|
| 控件 | 连续滑条（rtl_tcp 不推档表） | 有档表→离散吸附；无表→连续 |
| 下发 | `set_gain` 工具(:142) 连续发，服务端就近取整 | `setGain` 先 snap 到档再下发读回真值 |

- **差异**：rtl_tcp 协议不把 tuner 档表推给客户端，Flutter 无从知道合法档位（Q4-B）。
- **判定**：**不修（架构性，云内不可验）**。与 Q4 结论一致，无新增。

---

## 本批实际修复

仅一项（确有必要、云内可验）：

1. **`mobile/lib/app/ai_tools.dart` 接入 `start_recording`/`stop_recording`**：
   把 `RadioController` 已有的录制能力开给 AI，对齐桌面同名动作工具。理由：移动端
   本就支持 UI 录音却不对 AI 开放，是"能力已在、入口未开"的真实工具集缺口；工具
   包装层（连接 gate、手动模式 gate、StateError 诚实捕获、未在录空态）纯 Dart，云内可验。
   工具总数 6 → 8，审计断言同步更新。

未修项及理由（避免过度改动）：`scan_band`（无扫频引擎，特性开发 + 真机项）、
`set_bandwidth`（带宽随模式派生，刻意设计）、get_status 扩展字段（随后端能力，非契约）、
增益档表（rtl_tcp 不推表）。

## 测试

- `mobile/test/ai_tools_real_link_test.dart`：
  - RecordingRadio fake 补 `recording`/`startRecording`/`stopRecording`；
  - P0 断开用例纳入 `start_recording`（未连接回 `ok:false`、底层零调用）；
  - 新增"录音工具"5 例（connected 真正下发 / 已在录不重复开 / 未在录诚实空态 /
    在录 stop 回 meta 字段 / 手动模式被 gate）；
  - 工具总数审计 6 → 8。
- `flutter analyze`：**No issues found**（0）。
- `flutter test`：**298 passed**（293 基线 + 本批 5 新增）。
- 未改 C++：`ctest 91/91` 基线不受影响（本轮无 C++ 改动，未重跑）。

## 未完成 / 待验项（诚实披露）

- **scan_band / set_bandwidth**：判定架构性不修，未实现；若后续要做，属新特性 + 真机联调。
- **原生录音回放**（FilePlayer / mbdsdr/audio）：真机待验，云内仅 mock MethodChannel。
- **真实落盘 WAV 的 AI 调用链路**：工具包装层云内已测（fake）；真实 FileRecordingSink
  端到端已由 `radio_recording_test.dart` 覆盖，但"经 AI 工具真实触发落盘"未在云内跑
  （需录音目录注入 + 真实 IQ，真机项）。
