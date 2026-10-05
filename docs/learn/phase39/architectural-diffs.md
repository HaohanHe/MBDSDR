# Phase39 Wave1 — 架构性差异逐项（不硬造桥接）

> 本文档把「桌面有、移动无」中**不应/不必桥接**的差异逐项固定下来，避免 Wave2 误当真差距去补。
> 每项：差异内容 / 原因 / 是否需桥接及条件。证据 `file:line` 相对仓库根。
> 配套：真差距候选清单见本文 §3（供 Wave2）。

---

## 1. 信号路径总纲

桌面是**本机全栈引擎**：`SpectrumEngine` 直接持有 RTL IQ，进程内完成 FFT → channelizer → 多 VFO → 多态解调/解码 → 录制（SigMF/WAV）→ AI 35 工具。
移动是**两端分工**：
1. **rtl_tcp 瘦客户端**（`mobile/lib/services/rtl_tcp_client.dart`）只拿远端 IQ，本地做 FFT + NFM/WFM 音频解调（`radio_controller.dart:455-477`）；
2. **桌面 ControlHub 只读查看器**（`control_hub_client.dart`）经 HTTP 拉数字解码结果。

→ 凡是「引擎内 DSP / channelizer / 多 VFO / IQ 录制」能力，在移动都没有对应信号源，不移植。这是下面多条差异的共同根因。

---

## 2. 架构性差异逐项

### D1. 多 VFO（含 channelizer、按 VFO 解码、`?channel=N`）
- **差异**：桌面可增/删/复制/重命名/切换多个 VFO，每个独立 freq/offset/bandwidth/mode，数字 decoder 挂在选中 VFO 上（`cpp/src/ui/main_window.cpp:544-570`、`cpp/src/control/control_hub.cpp:100-118,819-838`）。移动端单 `_freqHz` 中心频点、单解调（`radio_controller.dart:195`）。
- **原因**：多 VFO = 对同一段 IQ 做数字信道化后并行多解。移动是 rtl_tcp 单客户端，本地只有一个解调链；并行多 VFO 需在移动端重写 channelizer + 多态 decoder（桌面 `vfo_manager.h:77-79` 那套），与「瘦客户端」形态冲突。
- **是否桥接**：**不桥接**。条件：仅当未来移动改为本地直连 RTL（不经 rtl_tcp）且要并行双监听时才议；当前无需求。`?channel=N`（`control_http_server.cpp:41-57`）随 VFO 一起不移植。

### D2. IQ 录制（SigMF cf32）vs 移动解调音频录制（WAV i16）
- **差异**：桌面录制对象可选「基带 IQ (SigMF)」或「解调音频 (WAV)」（`main_window.cpp:653-656`），IQ 走 SigMF cf32 + meta（`cpp/src/dsp/recorder.cpp:91-111`，历史裁决 phase35/gap-table.md:89）。移动只录过门后的解调音频 WAV 16-bit（`radio_controller.dart:548-593`）。
- **原因**：移动定位是「边听边录听到的」便携值守；录原始 IQ 需在移动端持续落盘 raw IQ（cf32 吞吐 ~4MB/s@1Msps），便携存储/电源不友好。AI 工具入口 `start_recording` 两端一致，产物不同是有意（`mobile/lib/app/ai_tools.dart:227-228` 已明文）。
- **是否桥接**：**不桥接**。条件：仅当移动要做「事后在桌面离线分析本机录的 IQ」闭环时，再议移动侧 SigMF 录制；当前离线分析在桌面（`main_window.cpp:1594-1687`）。

### D3. 增益档表不推送（连续 gain_db vs 离散档表）
- **差异**：桌面有空增益表回退 25.4dB + 离散档位步进（`cpp/src/control/control_hub.cpp:793-804` `list_gains`、历史裁决 phase11/P4-mobile-align.md:91-107）。移动无「增益档表」概念，`set_gain` 按连续 `gain_db`（0–49.6）下发。
- **原因**：rtl_tcp 协议命令集固定（`rtl_tcp_client.dart:20-28`：0x01/0x03/0x04/0x05/0x0d），**无查询离散增益档表的命令**——客户端拿不到 librtlsdr 的档位数组。臆造档位=伪造远端调谐器未上报的档位，违反诚实模型。移动默认 `autoGain=true`（`radio_controller.dart:198`）从机制上消除「0dB 聋棒」失效模式。
- **是否桥接**：**不桥接**。这是 rtl_tcp 瘦客户端的必然（历史已裁决，本轮复核维持）。`list_gains` AI 工具移动不需要。

### D4. 本地全 DSP vs 遥控 HTTP 分工
- **差异**：桌面 POCSAG/M17/VOR/CW/ADS-B/星座/APT 全在进程内解码；移动不本地解这些数字/宽频码，只读桌面 ControlHub 的 `/pocsag_messages`、`/m17_calls`、`/vor_radial`（`control_hub_client.dart:99-120`）。
- **原因**：数字解码器（POCSAG/M17/CW）与宽频捕获（ADS-B 1090、APT 气象）都依赖引擎内多 VFO/基带处理；移动 rtl_tcp 链只解 NFM/WFM 音频。
- **是否桥接**：**已桥接（只读）**——通过既定 RemoteDecoderPanel（`remote_decoder_panel.dart:1-12`）。不再补「移动本地解码」。例外见 §3-C（远程解码面板与桌面端点对齐是否漏端点）。

### D5. 值守/触发式录制 + 离线分析 + 录制模板
- **差异**：桌面有信号触发值守录制（前滚/结束延时，`gated_recorder`）、离线打开 .wav/.sigmf 过 DSP 链 seek、文件名模板/立体声/忽略静噪（`main_window.cpp:657-713,1594-1687`）。移动仅手动开/关录 WAV。
- **原因**：值守录制器/离线文件源是引擎能力；移动便携形态手动录制足够，且离线分析依赖桌面 DSP 链。
- **是否桥接**：**不桥接**。条件：仅当移动要做「无人值守自动录音」产品化时再议值守。

### D6. 世界地图 / ADS-B 表 / 气象 APT 图
- **差异**：桌面中央「世界」离线地图（GNSS/ADS-B/卫星图层 + GNSS 串口）、ADS-B 8 列表、气象 APT 解码图（`main_window.cpp:737-958,830-840`）。移动天空页只有极坐标雷达 + 过境表 + 导航星表。
- **原因**：地图/宽频解码是桌面大屏 + 引擎能力；移动用极坐标雷达 + SGP4 预测替代（且有时间预览滑条，桌面反无）。
- **是否桥接**：**不桥接**。条件：仅当移动要在地图上叠飞机/卫星图层时再议（需引入地图库，重）。

### D7. TX / 电台面板（serial CAT / CW / AX.25 KISS / SoapySDR TX）
- **差异**：桌面右栏「电台」页（`main_window.cpp:1880-1881` RadioPanel）含发射能力。移动无。
- **原因**：移动是 rtl_tcp **接收**客户端，rtl_tcp 协议本身无发射；硬件（MBDSDR-Mini）接收向。
- **是否桥接**：**不桥接**（产品/硬件均 RX 向）。

### D8. ANR / Noise Blanker / PPM 频率校正
- **差异**：桌面有 ANR（STFT-Wiener）、Noise Blanker、PPM spin（`main_window.cpp:606-628,329`）。移动解调链无这些后处理/校正。
- **原因**：后处理在引擎 DSP；移动 NFM/WFM 解调链精简。PPM 校正对应桌面 AI 工具 `calibrate_frequency`/`apply_frequency_correction`。
- **是否桥接**：**不桥接**（DSP 链差异，非 UI 缺失）。条件：若移动 NFM 解调出现邻道/DC 问题再单独排期。

### D9. AI 自主多步任务模板（sweep/target/fixed/sat）
- **差异**：桌面有确定性任务模板 + 可编辑参数 + 运行/停止 + 步骤视图（`main_window.cpp:1758-1830`）。移动 `TaskTemplatesBar` 只把草稿填进输入框，不自动多步跑（`chat_page.dart:401-402`）。
- **原因**：桌面自主任务依赖 35 工具全集（扫频联动/录制/书签 CRUD 等）；移动仅 8 工具，多步任务的动作面不全。属「能力面差异」而非纯 UI 缺失。
- **是否桥接**：**条件桥接**——见 §3-C 候选（待 Wave2 先补本地工具面，再议是否接模板执行器）；本轮不硬造。

### D10. 状态词汇 dropped vs reconnecting
- **差异**：桌面 status 用 no_telemetry/connected/dropped/error/disconnected（`control_hub.cpp:752-758`）；移动用 disconnected/connecting/reconnecting/connected/error（`radio_controller.dart:386-415`）。
- **原因**：桌面由引擎 source 事件（sourceDropped）驱动；移动是客户端指数退避重连。语义等价（掉线重连中），词汇不同。
- **是否桥接**：**不桥接**（两端各自诚实，AI get_status 五态语义已对齐 `ai_tools.dart:294-322`）。

---

## 3. 真差距候选清单（供 Wave2，每项带可行性）

> 判定标准：同架构下移动「该有却没有」、且本地能力已就绪或仅缺薄层。每项给出改哪里 / 是否动 cpp / 测试策略。**本轮不动手**。

### G1. AI 上下文压缩（真差距，中优先）
- **现状**：history 全量直传 `working`（`ai_client.dart:429-430`），无折叠/摘要；桌面有「压缩上下文」按钮（`main_window.cpp:1734-1738`）。
- **改哪里**：`mobile/lib/services/ai_client.dart` 或 chat 发送前加一层：超 N 轮时把早期 user/assistant 文本折叠成一条 system 摘要（可复用同 key 做一次小请求生成摘要）。`chat_session_store.dart:38-40` 已承认 tool 瞬态不落盘，压缩可与之共存。
- **是否动 cpp**：否（纯 Dart）。
- **测试策略**：注入 fake `ChatTransport`，断言超窗历史被折叠、近期原文保留；`ai_client_test` 增「触发压缩」用例，现有 342 不回归。

### G2. 录制回放落地（真差距，高优先）
- **现状**：回放缝 `onPlay/onStop/playing` 已留，未注入时诚实不渲染按钮（`recordings_page.dart:19,209-229`）；桌面分块播放已跑通（`main_window.cpp:1637-1653`）。
- **改哪里**：`mobile/lib/audio/` 加原生 WAV 解码 + AudioTrack/AVAudioEngine 分块喂已存在的播放 sink（`radio_controller.dart` 的 PcmSink 路径）；外壳 main.dart 注入 `onPlay`。
- **是否动 cpp**：否。
- **测试策略**：纯 Dart 层先测 WAV 解析/分块 seek；原生播放部分云内无设备，标「真机待验」并保持未注入时诚实空态（不假装出声）。

### G3. 双游标 A/B（真差距，中优先）
- **现状**：桌面游标 A/B 测频差（`spectrum_widget.cpp:123-138`）；移动 `SpectrumDisplay` 仅单频点 tap + 固定 marks。
- **改哪里**：`mobile/lib/widgets/spectrum_display.dart` 叠加两个可拖竖线 + 一行 A-B Hz 读数；不动 DSP（频率=画布换算，与 tap 调谐同一映射）。
- **是否动 cpp**：否。
- **测试策略**：widget 测试断言两游标放置/拖动后频差计算；纯渲染，无真实 IQ 也可断言传 frame。

### G4. 扫频完备性（方向/暂停/命中停留/只扫书签/命中存书签）（真差距，低-中优先）
- **现状**：移动扫频仅起止+固定 25kHz 步进+300ms 驻留（`radio_controller.dart:626-670`），缺方向/暂停/hold 模式/只扫书签/命中存书签（桌面 `main_window.cpp:1288-1340`）。
- **改哪里**：`radio_controller.dart` `startScan` 加可选参数（direction/holdMs/lingerMs/bookmarksOnly）+ pause 标志；`spectrum_page.dart:_promptScan` 对话框加控件（走 AppTokens 弹性，禁裸数）。
- **是否动 cpp**：否。
- **测试策略**：`radio_controller` fake 测试：断言方向遍历顺序、暂停标志生效、命中回调频率集合；对话框测试断言传参。

### G5. AI 工具补面：set_squelch / bookmark CRUD / recordings list-delete / scan_band（真差距，中优先）
- **现状**：这些能力移动端**本地 UI 已有**（静噪 `radio_controller.dart:730-758`、书签 `settings_service`、录制索引 `recordings_page.dart`、扫频），但未注册成 AI 工具（`ai_tools.dart` 仅 8 个）。
- **改哪里**：`mobile/lib/app/ai_tools.dart` 追加 `set_squelch`、`list_bookmarks`/`add_bookmark`/`delete_bookmark`、`list_recordings`/`delete_recording`、`scan_band`；手动模式 gate 集合 `mutatingTools`（`ai_tools.dart:408-415`）同步加。
- **是否动 cpp**：否（本地直调 RadioApi/SettingsService）。
- **测试策略**：每个新工具断言传参校验 + 未连接回错 + 手动模式 gated；`tool_arguments_validator` 覆盖新 schema。

### G6. 多普勒实时补偿（真差距，低优先，需设计）
- **现状**：`dopplerHz` 恒 null，诚实空态（`sky_page.dart:422-427`、`spacetime_status.dart:98-108`）；桌面 `dopplerCompChk_` 已接 SGP4 range-rate（`main_window.cpp:1696`）。
- **改哪里**：移动已有 SGP4 传播（`astro/`），需加一个随选中目标定时（~1Hz）算 range-rate 并微调调谐频率的小引擎，再把值喂 `SpacetimeStatusCard.dopplerHz`。
- **是否动 cpp**：否（纯 Dart SGP4）。
- **测试策略**：注入 fake clock + 已知 TLE，断言 range-rate 符号/量级合理；补偿不落调谐时先只显值不自动改频（保守第一步）。
- **备注**：捕获时一次性预测调谐已存在（`sky_page.dart:156-178`），实时补偿是增量。

### G7. AI 会话重命名（真差距，低优先，小）
- **现状**：移动无重命名（`chat_page.dart` 无 rename）；桌面有（`main_window.cpp:1723-1724`）。
- **改哪里**：`chat_session_store.dart` 加 `renameSession(id,title)`，抽屉 `ListTile` 加重命名入口。
- **是否动 cpp**：否。
- **测试策略**：store 测试断言 rename 持久化 + 索引更新。

---

## 4. 不列入候选（本轮裁决不做）
- 本地 POCSAG/M17/VOR/CW/ADS-B/星座/APT 解码（D4/D6，只读桥接已够）。
- 多 VFO、IQ SigMF 录制、增益档表、TX、ANR/NoiseBlanker/PPM、世界地图、值守录制、离线分析（D1/D2/D3/D5/D6/D7/D8）。
- AI 自主多步任务模板执行器（D9，依赖 G5 先补工具面后再议，本轮不硬造）。

## 5. 未解决项 / 待 Wave2 决策
1. **远程解码面板端点对齐**：移动 ControlHub 客户端用 4 个 GET（`control_hub_client.dart:92-120`），桌面 HTTP 6 端点含 `POST /command`（写）与 `GET /`（发现）。移动只读定位明确不调 `/command`——本轮判定为**架构性（只读查看器）**，但需 Wave2 确认：是否要补 `GET /` 发现或 `/status` 里更多字段？当前 `/status` 已拉（`fetchStatus`），够面板用。
2. **AI 压缩策略选型**：折叠成摘要需一次额外 LLM 请求，要定「触发阈值轮数 + 摘要模型/是否同 key」，Wave2 再定，本轮仅记录差距。
3. **G6 多普勒是否自动改频**：先只显值（安全）还是直接微调调谐（有风险），需 Wave2 定边界。
