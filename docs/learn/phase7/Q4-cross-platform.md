<!--
SPDX-License-Identifier: MIT
-->

# Q4 · Flutter ↔ 桌面（C++/Qt）行为一致性抽检

> 范围：抽查两端在 **连接/断开语义、增益控制、录音/回放、get_status 字段、VFO 出声**
> 五个轴上的行为是否一致。方法：真读两端源码（file:line 见下），只把**确有必要且
> 云内可验证**的差异落地为修复 + 测试；真机项、架构性差异如实记录、不强行抹平。
>
> 环境：云 VM 无硬件。基线：`flutter analyze` = 0、`flutter test` = **293**（288 基线
> + 本批新增 5）；`QT_QPA_PLATFORM=offscreen ctest` = **91/91**。
>
> 两端后端**不同**：移动端经 **rtl_tcp 网络客户端**（mobile/lib/services/rtl_tcp_client.dart），
> 桌面端直接挂 **librtlsdr 本地源**（cpp/src/dsp/rtl_sdr_source.cpp）+ 可选 rtl_tcp/spyserver。
> 这一根本差异解释了"增益档表"等轴上的大多数分歧——不是 bug，是协议边界。

---

## 差异清单

### A. 连接 / 断开状态语义

| | Flutter | 桌面 C++ |
|---|---|---|
| 状态机 | 5 态 `disconnected/connecting/connected/reconnecting/error`（radio_state.dart:7-22） | `sourceTelemetry(connected:bool)` + 三信号 `sourceDropped/sourceError/reconnectRequested`（spectrum_engine.h:259-272） |
| 断流处理 | IQ 流 error / 对端 onDone → `_beginReconnect` 指数退避 1→2→4→8→15s（radio_controller.dart:322-362；rtl_tcp_client.dart:125-137） | `readIQ` 连续失败 → readWatchdog 闩死 → `rtlsdr_close`、`isConnected()=false`（rtl_sdr_source.cpp:224-240） |
| 用户主动断开 | `disconnect()` 置 `_userDisconnected=true`、停退避、收尾录音（radio_controller.dart:538-551） | 断开走 sourceTearDown，`sbSdr_` 标 `(非硬件)`（main_window.cpp:3672） |

- **差异**：两端都诚实披露"就绪与否"，但 Flutter 是**显式五态机**，桌面是
  **就绪布尔 + 三路独立信号**。Flutter 的动作工具已正确 gate（未连接回 `ok:false`，
  ai_tools.dart:26-31）。
- **影响**：仅 AI 自报层面——`get_status` 此前把五态压成一个 `connected` 布尔，
  reconnecting 与 idle-disconnected 不可区分。
- **建议 / 处置**：**已修复**（见 D）。

### B. 增益控制

| | Flutter | 桌面 C++ |
|---|---|---|
| 控件 | 连续滑条 `AppTokens.gainMinDb..gainMaxDb`（spectrum_page.dart:601-609） | 有档表→离散 `gainCombo_`；无表→连续 `gainSlider_`（main_window.cpp:3746-3782） |
| 下发 | `setGainDb(db)` → rtl_tcp `0x04 setGain`，`(db*10).round()`（rtl_tcp_client.dart:179-180） | `setGain` 先 `TunerGainTable.snap` 到就近档再下发，读回真值（rtl_sdr_source.cpp:28-30,184） |
| 档表来源 | 无（rtl_tcp 服务端**不向客户端推档表**）；`0x0d setGainByIndex` 已定义但未用（rtl_tcp_client.dart:195-196） | 本地 `rtlsdr_get_tuner_gains` 两次调用填表（rtl_sdr_source.cpp:164-170） |

- **差异**：桌面能读硬件离散档表并吸附；Flutter 经 rtl_tcp 拿不到档表，只能连续发。
- **影响**：Flutter 手动增益实际由服务端就近取整；显示值是请求值不是落档值。
- **建议 / 处置**：**不修（架构性差异，云内不可验）**。rtl_tcp 协议不把 tuner 档表推给
  客户端，Flutter 无从知道合法档位；强行造一张表 = 臆造。已记录。未来若客户端与服务端
  协商出档表，可切 `0x0d setGainByIndex`，属后续增强，真机/联调项。

### C. 录音 / 回放

| | Flutter | 桌面 C++ |
|---|---|---|
| 录什么 | **解调后音频**（过静噪门后，"录听到的"）→ 16-bit 单声道 WAV + sidecar（radio_controller.dart:437-449, 490-534） | **原始 IQ** → SigMF `cf32_le`（recorder.cpp / recording_library） |
| 回放 | `FilePlayer` 走 `mbdsdr/audio` MethodChannel（startFile/pause/resume/stop/filePosition，file_player.dart:99-138） | SigMF 回放（playback.py / IQPlayback） |
| 空态 | 未注入 onPlay/onStop → **不渲染假播放按钮**（recordings_page.dart:209-229） | 无录制→空库 |

- **差异**：Flutter 录解调音频 WAV；桌面录原始 IQ SigMF。用途不同（一个是"听到的"，
  一个是可再分析的基带）。回放原生实现已写但**云未编译·真机待验**（file_player.dart:18-21）。
- **影响**：无 bug；两端产物本就不同。
- **建议 / 处置**：**不修**。Flutter 原生回放是真机项，云内不可验；保持诚实空态。

### D. get_status 字段对齐 ★（本批修复）

| | Flutter get_status（修复前） | 桌面 telemetry |
|---|---|---|
| 字段 | `{ok, connected, frequency_hz, mode, gain_db, auto_gain, sample_rate_hz}`（ai_tools.dart:221-240） | `sourceTelemetry(name, connected, centerHz, sampleRateHz, gainDb)` + `snrLevel/noiseFloorLevel`（spectrum_engine.h:271-281） |

- **差异**：Flutter 把五态机压成单个 `connected` 布尔；桌面除就绪布尔外，用
  `sourceDropped/sourceError/reconnectRequested` 三路信号披露掉线原因。
- **影响**：掉线自动重连窗口内，AI 调 `get_status` 看到 `connected=false`，
  与"用户主动停手(idle)"无法区分，也拿不到 `errorMessage`。
- **处置（已修，云内可验）**：`get_status` **新增** `status`（完整状态机名字符串）与
  `error_message` 两字段，保留 `connected` 布尔向后兼容；description 同步更新。
  对齐桌面"就绪布尔 + 掉线原因"的披露粒度。改动见 `mobile/lib/app/ai_tools.dart`。
- **测试**：新增 `get_status 字段对齐` 5 例（connected/reconnecting/disconnected/error/
  旧字段不回退），见 `mobile/test/ai_tools_real_link_test.dart`。

### E. VFO 出声语义

| | Flutter | 桌面 C++ |
|---|---|---|
| VFO 概念 | **无**。单频率 `_freqHz`、单解调链 | 多 VFO，每 VFO 独立信道化+解调（vfo_manager.{h,cpp}） |
| 出声 | 仅一条解调音频流 | 只有选中 VFO 进声卡，UI 标 `[出声]`（main_window.cpp:2989-2990）；`vfo_audible` 测试 #52 |
| 解调模式 | 仅 nfm / wfm（radio_controller.dart:379-382） | WFM 立体声/RDS/CW/AX.25/APT 等 |

- **差异**：移动端是**单信道语音接收**；桌面是**多信道 SDR**。移动端无 VFO 可对齐。
- **影响**：功能范围差异，非 bug。
- **建议 / 处置**：**不修**（属大功能，非小修；云内不可作为"一致性修补"落地）。如实记录为范围差。

---

## 本批实际修复

仅一项（确有必要、云内可验）：

1. **`mobile/lib/app/ai_tools.dart` `get_status`**：新增 `status`（状态机名）与
   `error_message`，保留 `connected` 布尔。理由：对齐桌面"就绪布尔 + 掉线原因"披露，
   让 AI 在 reconnecting/error 窗口不再误判为 idle。

未修项及理由（避免过度改动）：增益档表（rtl_tcp 不推表，云内不可验）、录音/回放
（产物本就不同 + 原生回放真机待验）、VFO（范围差，非小修）。

## 验证

- `flutter analyze`：**No issues found**（0）。
- `flutter test`：**293 passed**（288 基线 + 5 新增 get_status 字段用例）；工具总数仍为 6
  （`buildRadioTools` 审计断言不破——本批只加输出字段，不加工具）。
- `QT_QPA_PLATFORM=offscreen ctest`（cpp/build）：**91/91**（本批未改 C++，基线不破）。
