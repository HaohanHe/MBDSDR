# Phase63 — DemodRaw（RAW 直听解调）端到端落地

> 状态：**已落地**（HEAD=7e0418e 之上工作树）。RAW = 所选 VFO 信道化窄带 IQ 的
> L=I / R=Q 直通，不经任何解调。本档记录机制、语义取舍、file:line 与测试计数。
> 干净室：只学 SDR++ raw.h「直通」机制概念，未抄 GPL；MIT。

## 1. 语义定稿

- **RAW 直听 = 所选 VFO 信道化窄带 IQ 直通**。channelizer 仍用模式默认带宽
  （`kBwRawHz=12500`，`bandwidth_preset.h:70`）切出该 VFO 的窄带复 IQ；DemodRaw
  对该 IQ **不做任何解调/滤波/缩放/AGC**，逐采样直通为音频流。
- **L=I / R=Q**：`DemodRaw::process(iq)` 输出交错 `[I0,Q0,I1,Q1,...]`（偶数下标=I=左
  声道，奇数下标=Q=右声道）。引擎 run() 链把它**去交错**为真立体声 `writeStereo(L,R)`，
  便于监听者直接对照同相/正交两路（示波器/频谱交叉核对、外部分析仪输入）。
- **下游消费核对**：vfo_manager 的 `audio48k` 虽是单声道流，但 RAW 走 run() 的
  **立体声分支**（`selMode=="RAW"` 时去交错），不是 mono 取 I——故 L 与 Q 两路都保留。
  窄带 ifTarget=48000，channelizer Rational 模式输出严格 48000，resampler 为 1:1，
  I/Q 对不被重采样打乱。
- **outputSampleRate() = ifRate**（=48000 窄带），下游 ifRate→48k 重采样为恒等直通。

## 2. ANR / AGC / squelch / CTCSS 取舍

| 级联级 | RAW 下行为 | 理由 |
|---|---|---|
| ANR | **旁路**（identity，`spectrum_engine.cpp:1518`） | ANR 是音频域降噪，作用于交错 I/Q 会改动「直通应逐采样保真」的样本 |
| AGC | **旁路**（增益包络=1，`:1587-1594`） | AGC 是音频域包络压缩，对 IQ 直通无意义且会重缩放 I/Q 对 |
| squelch | **保留**（RMS 能量门控，`:1568`） | 交错 I/Q 的 RMS ≈ 载波能量，仍是诚实的「有/无载波」判据；门关闭时 L/R 同零 |
| CTCSS 亚音门控 | **不门控**（检测仍跑在流上，但不静音 RAW） | RAW 无解调音频，亚音 PL 对其无语义；检测器照常运行，speaker 不因无匹配 PL 静音 |

> 录音 / WAV 连续性仍走 `out`（交错、squelch 门控后），保持 48k 流连续。

## 3. 落地 file:line

**DSP**
- `demod.h:163` `class DemodRaw : public IDemod`（L=I/R=Q 交错、outputSampleRate=ifSr、
  reset 无状态、setBandwidth 用基类空 no-op——工具层拒绝，DSP 层不静默吞）。
- `demod.cpp:254` 构造、`:256` process 逐复数写 [I,Q]、`:266` reset 空。
- `vfo_manager.cpp:262` analog 分支 `else if (mode=="RAW") demod = make_unique<DemodRaw>(ifRate)`
  （chBw 走模式默认 12.5k，channelizer 仍切窄带）。
- `spectrum_engine.cpp:1517-1518` rawSel + ANR 旁路；`:1587-1594` AGC 旁路（单位包络）；
  `:1611` RAW 立体声去交错分支（L=even=I、R=odd=Q、squelch 门控、尾奇数样本丢弃保帧对齐）。

**带宽预设**
- `bandwidth_preset.h:70` `kBwRawHz=12500`；`:90` `defaultBandwidthHzForMode("RAW")` 映射。

**三通道（非新工具，金集保持）**
- `tokens.h:1172` `kControlHubModes[]` 加 `"RAW"`（control_hub `needMode` :322 与
  agent_tools `execSetMode` :109 同源白名单自动生效）。
- `control_hub.cpp:469` `cmdSetBandwidth`：mode=="RAW" → `errResult`（ok:false，
  「带宽对 RAW 直通无意义…」），不触碰引擎。
- `agent_tools.cpp:233` `execSetBandwidth`：同上 ok:false 诚实拒绝。
- AI LLM schema 枚举（`tool_schema.cpp:92`）**保持 6 语音模式不动**——`test_tool_schema`
  精确断言该枚举，RAW 为调试/专家模式不进 LLM 枚举面。

**UI**
- `main_window.cpp:597` `demodCombo_` addItems 末尾加 `"RAW"`。
- 模式→带宽联动（`:2566 bandwidthOnModeSwitch`）与 WFM 特殊化（`:2541 forceMono`）核对：
  RAW 下 `wfm=false`、forceMono 禁用、落默认 12.5k，不误伤；持久化 `rx/demodMode="RAW"` 天然兼容。

## 4. 测试（确定性、真实源、无 mock）

- **DSP 单测** `test_demod.cpp::rawPassthroughLIQ`：合成旋转相量 IQ → 逐采样断言
  `out[2i]==I[i]`、`out[2i+1]==Q[i]`（位级保真、无缩放）；size=2N；name=="RAW"、
  outputSampleRate==ifSr；reset 后第二块流式一致。**PASS**（test_demod 6→7）。
- **引擎 e2e** `test_engine_audio_e2e.cpp::rawDirectListenProducesStereoPassthrough`：
  真实 AM 载波离线文件 → mode=RAW、squelch 关 → `writeStereo` 非空、L/R 帧对齐、
  Lrms/Rrms 均>0（实测 L 帧=R 帧=21600，Lrms=0.0967、Rrms=0.8056——I/Q 两路确实不同）。
  **PASS**（引擎 e2e 11→12）。
- **三通道**：`test_control_hub` `commandsDriveEngineAndReadback` 内并入——set_mode RAW
  落地（get_mode 读 "RAW"）+ set_bandwidth RAW ok:false；`test_agent`
  `readbackLoopHonestRejectsAndAutoLand` 内并入——引擎读回 mode=="RAW"、set_bandwidth RAW
  ok:false 且 error 含 "RAW"。**均 PASS**。
- **金集（测试槽位）保持 88**：tool_registry 8 + agent 35 + control_hub 30 +
  control_http 15 = **88**（RAW 断言并入既有槽，未新增槽；未新增 CMake 目标）。

## 5. 快照（ui_shot_narrow，MBD_MODE=RAW MBD_BW=12500 MBD_SCROLL=demodCombo）

- 640 / 960 / 1920 三档均拍出：解调 combo 显示 **RAW**、带宽 **12.5 kHz**、
  状态栏「RAW 未连接 VFO A 98.500 MHz」。**0 裁切、0 叠字**（窄档左侧滚动区正常滚动，
  控件本身无重叠/裁切）。

## 6. 红线 & 诚实未完成项

- 本档及 diff 无「比赛/competition」字样；无 mock；GPL 仅机制概念对照，产物 MIT。
- 未改动隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*`、`cdcss.*`、
  `test_squelch_gate.cpp`（后两者为并行会话文件）；未 git add/commit/push。
- **未完成/取舍说明**：
  1. 音频电平表（`audioLevel`）在 RAW 下仍读 AGC 旁路后的空闲值——未为 RAW 单算 RMS
     电平，属可接受的 UI 度量取舍（不影响直通路径）。
  2. ST 立体声 badge 仍仅 WFM 导频驱动；RAW 虽为 L=I/R=Q 真立体声，但不 claimed
     「ST」badge（诚实：badge 语义是 WFM 导频锁定）。
  3. 录音/WAV 在 RAW 下记录的是交错 I/Q 的 mono 48k 流（非分离双声道文件）——直通语义
     下的诚实产物，未为 RAW 加独立立体声录音路径。
