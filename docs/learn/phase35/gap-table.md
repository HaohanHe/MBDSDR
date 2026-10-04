<!-- SPDX-License-Identifier: MIT | Phase35 W2：差距裁决汇总表（docs 域，只从五篇笔记提炼） -->

# Phase35 差距裁决表（gap-table）

> 来源：`docs/learn/phase35/{radio-frontend,dsp-core,scheduler-modem,spectrum-render,file-sink}.md`
> 基线：HEAD b20bfb7；对照我方 `cpp/src/`（干净室学机制，不抄 GPL 代码）。
> **W2 落地状态：进行中**——本轮只落两项真差距（AGC 块级峰值前瞻防削波、maxHold 逐帧衰减），
> 其余补齐候选均未立项；本表不动 cpp/ tools/ mobile/。
> 判定三档：**补齐**（本轮 W2 落地）/ **YAGNI**（理由见列，现在不做）/ **反超**（我方已优于或不同于 SDR++，不补）。

---

## 1. radio-frontend（L1：解调链 / AGC / 静噪 / WFM 立体声）

| SDR++ 机制 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|
| AGC 削波前瞻：命中削波预测时扫本块剩余样本取峰值、先压包络再放大 | `agc.cpp:38-56` block-based，有 maxGain ceiling（`agc.h:24` DefaultMaxGain=12）但无块内峰值前瞻 | **补齐（W2 进行中）**：O(n) 块级一次 `max_element` 扫描，挡突发强信号瞬态削波；注意按块级扫而非逐样点嵌套（避免上游最坏 O(n²)） | `repos/sdrpp/core/src/dsp/loop/agc.h:91-104`；我方 `cpp/src/dsp/agc.cpp:38-56`、`agc.h:24` | W2 落地，干净室重写 + ctest |
| 鉴频后 Nuttall-windowed sinc LPF（cutoff=bw/2，trans=bw·0.1，可选开关） | `demod.cpp:93-95,166-170` NuttallLpf 单测通过，但 `firEnabled_=false` live chain 关闭（block-streaming 跟 channelizer 交互有问题，multi_vfo 281→2.64） | **YAGNI（本轮不修）**：不是"选择不做"而是"做了没跑通"的联调 bug；邻道抑制目前靠 VFO 带宽兜底。修 block 流式是独立调试任务，不并进 W2 的 AGC/maxHold 范围 | `repos/sdrpp/decoder_modules/radio/fm.h:110-130`；我方 `cpp/src/dsp/demod.cpp:93-95,166-170` | 记已知项，待单独排期修 live 链路 |
| 静噪放在 IF 域（解调前），关时整块 IQ 置零，省算力防爆音 | `squelch.cpp:25-44` 在解调后音频域；平滑 RMS + hangover | **YAGNI**：搬 IF 域是算力/爆音优化，但我方 hangover 已解决开合 chatter；搬位置需重构解调链接线，本轮不做 | `repos/sdrpp/decoder_modules/radio/power_squelch.h:43-46`；我方 `cpp/src/dsp/squelch.cpp:25-44` | 不做 |
| WFM 导频带通 FIR（18.75–19.25 kHz, BW 500 Hz）滤干净再给 PLL | `wfm_stereo.cpp:112-216` NCO 直接混频 → I/Q 低通 → atan2 鉴相 | **YAGNI**：上游带通 FIR 抗噪更好，但我方积分器能把弱信号 PLL 拉回；弱信号专项场景再加 | `repos/sdrpp/decoder_modules/radio/broadcast_fm.h:43`；我方 `cpp/src/dsp/wfm_stereo.cpp:112-216` | 不做 |
| 去加重放在 AF 链 48 kHz 重采样后（音频域操作，位置更"正确"） | `demod.cpp:133-134,183-184` 在 IF SR 下做 | **YAGNI**：数值差异不大；挪位置属重构，未来重采样链重构时一并挪 | `repos/sdrpp/core/src/dsp/deephasis.h:58-94`、`radio_module.h:105,110`；我方 `demod.cpp:133-134,183-184` | 不做（重构项） |
| CTCSS 亚音静噪 | 无 | **YAGNI**：需窄带音频亚音解码，无对讲用户 | `repos/sdrpp/decoder_modules/radio/radio_module.h:861` | 不做 |
| FM-IF NR（谱减 IF 域降噪） | 已有 ANR 音频域 STFT-Wiener | **YAGNI**：IF 域对语音收益有限，音频域 ANR 已覆盖 | `repos/sdrpp/decoder_modules/radio/`（pre-demod IF chain）；我方 `cpp/src/dsp/anr.h` | 不做 |
| NoiseBlanker 接 IF 链 | 有代码没接线，用户未报脉冲噪声问题 | **YAGNI** | `repos/sdrpp/core/src/dsp/noise_reduction/noise_blanker.h:44-51`；我方 `anr.h:13` 已引用 | 不做 |
| RDS 解码 | wfm_stereo 有 raw MPX tap | **YAGNI**：解码器后续再说 | `repos/sdrpp/decoder_modules/radio/broadcast_fm.h:52,167` | 不做 |
| SNR-based / 迟滞静噪 | — | **YAGNI**：SDR++ 自己都 `// TODO: Rewrite better!!!!!` 没实现，不抄 | `repos/sdrpp/decoder_modules/radio/power_squelch.h:4` | 不做 |
| 静噪平滑 + hangover（SDR++ 是粗糙整块开关、无迟滞无 hangover） | `squelch.cpp:25-44` 快 attack 5ms/慢 decay 50ms + hangover 200ms | **反超**：我方静噪更细腻，上游是占位实现，勿向它看齐 | 我方 `cpp/src/dsp/squelch.h:42`（hangover 200ms） | 保持，不抄上游 |
| 立体声/单声道自动切换（SDR++ 无，导频丢了就出噪声） | `wfm_stereo.cpp:186-215` quality/blend 平滑过渡 | **反超**：我方 blend 切换是优势，别被上游带偏 | 我方 `cpp/src/dsp/wfm_stereo.cpp:186-215` | 保持 |
| M/S 对齐靠显式 delay line 补群延迟 | `wfm_stereo` 同一组 taps 逐样本 push，L/R 对称 | **反超**：我方 M/S 对称 FIR 对齐，机制更干净 | 我方 `cpp/src/dsp/wfm_stereo.cpp`（M/S 对称） | 保持 |

---

## 2. dsp-core（L2：抽取 FIR / AGC / 多速率 / ANR / FFT）

| SDR++ 机制 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|
| AGC 前瞻防削波（块级峰值扫描） | block-based AGC 仅 maxGain 上限，无前瞻 | **补齐（W2 进行中）**：同 §1 AGC 行，机制小、挡瞬态削波 | `repos/sdrpp/core/src/dsp/loop/agc.h:83,91-104`；我方 `cpp/src/dsp/agc.h:24` | W2 落地 |
| 2 的幂多级预抽取 tap plan（PowerDecimator 查预生成表，{64,8,2} 三级） | channelizer 单级抽取 FIR + rational 残差 | **YAGNI（先 profile）**：现 RTL~2.4 MHz→信道单级尚可；只有实测 channelizer 成热点且大抽取比时才值得多级化 | `repos/sdrpp/core/src/dsp/multirate/power_decimator.h:91-113`、`decim/plans.h:36-140`；我方 `cpp/src/dsp/channelizer` | 不做，等 profiling |
| VOLK/SIMD 点积（volk_32fc_32f_dot_prod） | `rational_resampler.h:94-95` 纯标量 `acc+=ph[k]*data[..]` | **YAGNI**：引 VOLK 是重依赖+编译链负担；标量对 48k 音频/窄带足够，等 profiling 证明热点 | `repos/sdrpp/core/src/dsp/filter/decimating_fir.h:51-56`；我方 `cpp/src/dsp/rational_resampler.h:94-95` | 不做 |
| FFTW3f（`fftwf_plan_dft_1d` + ESTIMATE，窗/抽稀控帧率） | `fft.cpp` 手写 radix-2 Cooley-Tukey，std::complex，无外部依赖 | **YAGNI**：ANR 帧 512/1024、谱分析自写 FFT 足够；FFTW(LGPL) 重且非必要，保持纯 std。我方走的是不同依赖路线，不要为"对齐上游"引入 FFTW | `repos/sdrpp/core/src/signal_path/iq_frontend.cpp:60-62,252-262`；我方 `cpp/src/dsp/fft.cpp` | 不做 |
| FastAGC（输出反馈单率，退化简化版 AGC） | `agc.h` 已有更优 attack/decay 非对称 + carrier AGC | **YAGNI**：FastAGC 是退化简化不是升级，我方已覆盖且更细 | `repos/sdrpp/core/src/dsp/loop/fast_agc.h:63-76`；我方 `cpp/src/dsp/agc.h` | 不做 |
| NoiseBlanker 冲激噪声归一 | `anr.h:13` 已引用 `noise_blanker.{h,cpp}` | **无差距**：机制已在 | `repos/sdrpp/core/src/dsp/noise_reduction/noise_blanker.h:44-51`；我方 `cpp/src/dsp/anr.h:13` | 保持 |
| PowerSquelch 整块能量静噪 | `squelch.cpp` | **无差距/勿抄**：上游自己标 TODO 重写，整块均值无迟滞会 chatter，我方 hangover 版勿向它看齐 | `repos/sdrpp/core/src/dsp/noise_reduction/power_squelch.h:4,33-49`；我方 `cpp/src/dsp/squelch.cpp` | 保持我方 |
| 谱减 / 维纳 ANR | SDR++ 无（仅冲激 + 整块静噪，无重型 ANR） | **反超**：我方 anr.h 是 STFT-Wiener + CV-VAD + decision-directed，比 SDR++ 重型得多 | 我方 `cpp/src/dsp/anr.h` | 保持 |
| 多相 commutator 分支less推进 | `rational_resampler.h:98-101` 同款 | **无差距**：机制已对齐，仅实现语言不同 | `repos/sdrpp/core/src/dsp/multirate/polyphase_resampler.h:85-91`；我方 `cpp/src/dsp/rational_resampler.h:98-101` | 保持 |
| fastAtan2 有理逼近 | demod/相位相关处按需用 libm | **YAGNI**：非热点；需要时再加查表/逼近，现在是过度优化 | `repos/sdrpp/core/src/dsp/math/fast_atan2.h:13-24` | 不做 |
| GCD 约分 + 重采样误差告警 | rational_resampler 约分 | **无差距/方向一致**：我方 commutator 形态对齐 | `repos/sdrpp/core/src/dsp/multirate/rational_resampler.h:120-165`；我方 `rational_resampler.h:88-102` | 保持 |

---

## 3. scheduler-modem（L3：Task/Action 调度 + decoder/modem 组织）

| SDR++ 机制 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|
| Task→Action 组合 + Action 抽象工厂（trigger 顺序广播、JSON 序列化） | `task_orchestrator.h` TaskPlan=确定性步骤序列，每步=一次 tool 调用，走与 LLM tool-loop 同一执行点；步骤参数可引用上一步真实结果（JSON path）、StepState(Gated/Failed/Aborted)、原子 requestStop | **反超/不补**：我方已把 Action 泛化为"tool + json-args + 单执行点"，且多了引用解析与原子停止纪律 | `repos/sdrpp/misc_modules/scheduler/src/sched_task.h:9-13`、`sched_action.h:9-17,29`；我方 `cpp/src/ai/task_orchestrator.h:4-10,31-35,78-90` | 保持 |
| 时间触发 / cron / 周期调度 | `task_orchestrator.h:76-77` 刻意不持有任何定时器/墙钟；`frequency_scanner.h:3-22` owns no wall clock、由 caller 喂 elapsedMs | **YAGNI（不采纳半成品方向）**：SDR++ 根本没做——倒计时列直接打印字面量 `"todo"`、触发条件硬编码一行、无定时器无重复周期。我方触发时机交引擎/外层，纯逻辑可单测 | `repos/sdrpp/misc_modules/scheduler/src/main.cpp:116`、`sched_task.h:63`；我方 `task_orchestrator.h:76-77`、`frequency_scanner.h:3-22` | 不做 |
| VFO 作为唯一抽点 + 多态 decoder 按 mode 热切换 | `vfo_manager.h:77-79` 每 VfoChannel=Channelizer+unique_ptr<IDemod>+resampler；数字 decoder 仅匹配 mode 时构造否则 null，随 rebuild 重建；能力谓词 isPocsag/isM17/isDigital | **反超/不补**：比 SDR++"每 decoder 一个独立 .so + 自己 createVFO"更内聚；decoder 真实现已落地 | `repos/sdrpp/core/src/signal_path/vfo_manager.h:29,33`、`pager_decoder/src/main.cpp:86-114`；我方 `cpp/src/dsp/vfo_manager.h:77-79,100-155` | 保持 |
| Demod 宽能力查询接口（getDefaultBandwidth/Min/Max/SquelchAllowed/DeempAllowed…） | `demod.h:20-24` IDemod 极简（process/reset/name/outputSampleRate/setBandwidth），无那组 getter | **YAGNI（watch-item）**：当前带宽预设中心化管理，不需要每解调器自报区间；仅当未来要做"用户任选 per-mode 带宽并自动撑 VFO 框/自动决定后处理可用否"才补。现在补=过度设计 | `repos/sdrpp/decoder_modules/radio/src/demod.h:37-60`、`radio_module.h:365-398`；我方 `cpp/src/dsp/demod.h:20-24` | 记观察项，不立项 |
| stale 时间戳判活（DSP 异步产出，UI 按多久没更新显示真实值/占位符） | `vfo_manager.h:144-146` 诚实快照，但 pocsagMessages/m17Calls 只在 rebuild/clear 时清，无"这条消息多久前的"时间戳 | **小补候选（低优先级，非 W2）**：给每条读出附 lastUpdateMs、UI 据此灰显。属增量优化，不阻塞当前 | `repos/sdrpp/decoder_modules/m17_decoder/src/main.cpp:144-147,230-237`；我方 `cpp/src/dsp/vfo_manager.h:144-146` | 暂缓，W2 不做 |

---

## 4. spectrum-render（L4：FFT 窗口 / 色板 / 历史 / doZoom / Hold）

| SDR++ 机制 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|
| Peak-Hold 逐帧衰减（`max(new, old - fftHoldSpeed)`，包络上沿保持、每帧向下衰减） | `spectrum_display.h:343` `maxHold_` 是 flat max envelope，无逐帧衰减 | **补齐（W2 进行中）**：在 setSpectrum 末尾对 maxHold_ 每 bin 做 `std::max(frame[i], maxHold_[i]-kHoldDecay)`，约 5 行，长信号观察更耐看 | `repos/sdrpp/core/src/gui/waterfall.cpp:935-939`；我方 `cpp/src/ui/spectrum_display.h:343` | W2 落地，干净室重写 + ctest |
| 1M 项色板 LUT（WATERFALL_RESOLUTION=1000000，uint32 BGRA，4 MB BSS） | `spectrum_render.h:121-140` buildLut256 仅 256 项 | **YAGNI**：显示深度本就 8-bit/通道，256 项足够；上游 1M 项是过度设计 | `repos/sdrpp/core/src/gui/waterfall.h:11`、`waterfall.cpp:944-958`；我方 `cpp/src/ui/spectrum_render.h:121-140` | 不做 |
| 零拷贝环行写（DSP 线程 acquireFFTBuffer 直接拿环行指针原地写 dB，写完 pushFFT） | `spectrum_display.cpp:208-218` pushHistoryRow 一次拷 dbfs 进 ringDb_ | **YAGNI**：我方 FFT 帧率低（Qt 主线程帧驱动），拷贝开销可忽略；零拷贝会把 DSP/UI 锁耦合更紧 | `repos/sdrpp/core/src/gui/waterfall.cpp:876-901`、`main_window.cpp:230-236`；我方 `cpp/src/ui/spectrum_display.cpp:208-218` | 不做 |
| trace EMA 平滑（`alpha·latest + beta·smoothingBuf`，volk 向量化） | 无专门 trace EMA，但有 setPersistenceMode 余晖包络（衰减系数 0.78/0.93） | **YAGNI**：余晖残影与瞬时 EMA 平均用途重叠，不加 EMA 开关 | `repos/sdrpp/core/src/gui/waterfall.cpp:914-920,1190-1194`；我方 `cpp/src/ui/spectrum_display.h:95-97`、`tokens.h:121-123` | 不做 |
| FFT 窗口集（GUI 只暴露 RECTANGULAR/BLACKMAN/NUTTALL 三种） | `power_spectrum.cpp:79-104` rebuildWindow 实现 Hann/Flattop/Blackman 三种 | **反超**：我方窗口集比上游 GUI 暴露的还全（多了 Flattop 平坦幅频） | `repos/sdrpp/core/src/dsp/window/`、`iq_frontend.h:17-21`、`main_window.cpp:91`；我方 `cpp/src/dsp/power_spectrum.cpp:79-104`、`spectrum_engine.h:293` | 保持 |
| fftshift 藏在窗函数里（窗乘 `(-1)^n` 调制） | `power_spectrum.cpp:47-48,117-118` 用 std::rotate | **等价/不抄**：语义相同，我方更直白 | `repos/sdrpp/core/src/signal_path/iq_frontend.cpp:updateFFTPath`；我方 `cpp/src/dsp/power_spectrum.cpp:47-48,117-118` | 保持 |
| VFO SNR（选中带内取 max，两侧 guard band 取平均当噪声底） | `setNoiseFloorDb` 注入真实测量噪声底 | **不抄**：guard band 平均会被邻道信号污染底噪；我方真 RSSI 更诚实 | `repos/sdrpp/core/src/gui/waterfall.cpp:558-598`；我方 `cpp/src/ui/spectrum_display.h:148` | 保持我方 |
| autoRange（扫整幅 latestFFT min/max，fftMin=min-5, fftMax=max+5） | `spectrum_display.cpp:295-304` 滑动 24 帧峰值窗 + eased ceiling | **反超**：我方比上游整幅扫一次更稳 | `repos/sdrpp/core/src/gui/waterfall.cpp:976-990`；我方 `cpp/src/ui/spectrum_display.cpp:295-304` | 保持 |
| 块最大降采样 doZoom（取块 max 不取平均，窄带峰不被平均掉） | `spectrum_render.h:82-107` decimateBlockMaxRange（double scale、isfinite 过滤、每块≥1 bin） | **已对齐（更稳）**：机制一致，我方用 double 避免长图漂移 | `repos/sdrpp/core/src/gui/waterfall.cpp:65-90`；我方 `cpp/src/ui/spectrum_render.h:82-107` | 保持 |
| raw-dB 环存历史（rawFFTs 存整圈 dB，切色板/改 dB 范围整圈重染） | `spectrum_display.h:361` ringDb_ 存 raw dB，materialiseHistory 从环重渲 QImage | **已对齐**：我方按 dB 存更省内存（行宽=bins 而非 dataWidth×4B×rows） | `repos/sdrpp/core/src/gui/waterfall.h:286,293`、`:600-631`；我方 `cpp/src/ui/spectrum_display.cpp:220-240`、`spectrum_display.h:361` | 保持 |
| 外部 JSON 色板（resources/colormaps/*.json，均匀分布 hex） | `spectrum_render.h:174-252` parseColormapJson 支持两种 root、失败返回错误串不静默 fallback | **已对齐（更诚实）**：我方比上游只认均匀分布更宽容 | `repos/sdrpp/core/src/gui/colormaps.cpp:12-47`；我方 `cpp/src/ui/spectrum_render.h:174-252` | 保持 |

---

## 5. file-sink（L5：录制 / WAV / IQ 导出 / rtl_tcp / 回放）

| SDR++ 机制 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|
| 连续 IQ 网络导出（TCP srv / TCP cli / UDP，int8/16/32/f32，Reshaper 定长切片，try_lock 丢包不阻塞 DSP） | 无网络 IQ 导出模块；仅 one-shot `exportIqSegment` 落盘（`spectrum_engine.h:133`） | **YAGNI**：产品形态是本机分析 + MCP 按需 dump，不对外做 IQ 服务器；外部 SDR 客户端接入不在范围。若后续要做 SDR++ 兼容 IQ stream server，按 Reshaper+volk+try_lock 机制干净室重写 | `repos/sdrpp/misc_modules/iq_exporter/main.cpp:487-528,491-498`；我方 `cpp/src/dsp/spectrum_engine.h:125,133` | 不做 |
| WAV 多比特深（u8/i16/i32/f32 可选） | `wav_writer.cpp:75-83` i16 only | **YAGNI**：i16 WAV 覆盖 99% 回放场景；f32 需求用 SigMF cf32 代替 | `repos/sdrpp/core/src/utils/wav.cpp:160-170`、`recorder/main.cpp:59-62`；我方 `cpp/src/dsp/wav_writer.cpp:75-83` | 不做 |
| VFO 级 IQ 导出（建指定采样率 VFO 后导出） | 仅 baseband 整段导出（`spectrum_engine.h:126`） | **YAGNI**：VFO 重采样导出等价于 demod 后再采样，与现有 demod 链重叠 | `repos/sdrpp/misc_modules/iq_exporter/main.cpp:433-438`；我方 `cpp/src/dsp/spectrum_engine.h:126` | 不做 |
| file_source WAV 回放（固定 44 字节头，EOF seekg 回绕循环） | `file_source.cpp:29-60` SigMF cf32 回放 | **YAGNI**：回放自己录的 SigMF 即可；WAV 回放用外部工具，不按上游脆弱实现抄 | `repos/sdrpp/source_modules/file_source/wavreader.h:47,62-76`；我方 `cpp/src/dsp/file_source.cpp:29-60` | 不做 |
| 录制容器 WAV（u8/i16/i32/f32，uint32 chunk size） | `recorder.cpp:91-111` SigMF cf32_le + JSON sidecar；音频 `gated_recorder.cpp:183-200` WAV i16 | **反超/不补**：SigMF 无 4GB 限制、自带元数据，研究场景优于 WAV；音频 i16 WAV 已是交换标准 | `repos/sdrpp/core/src/utils/wav.cpp`、`riff.cpp:83-106`；我方 `cpp/src/dsp/recorder.cpp:91-111` | 保持 |
| >4GB 长录（uint32 size，RF64 枚举声明但 UI 注释掉、未实现） | `recorder.cpp:155-156` SigMF 裸写无上限 | **反超**：上游录 >4GB chunk size 回绕会坏文件；我方无此问题 | `repos/sdrpp/core/src/utils/wav.h:21`、`recorder/main.cpp:58`、`riff.cpp:99`；我方 `cpp/src/dsp/recorder.cpp:155-156` | 保持 |
| 自动分段 + 碰撞规避文件名 | `recorder.cpp:161-168,134-151` 按样本数分段、`_2/_3` 碰撞规避 | **反超**：上游文件名模板同秒同频会覆盖（秒级时间戳无碰撞检测） | `repos/sdrpp/misc_modules/recorder/main.cpp:498-503`；我方 `cpp/src/dsp/recorder.cpp:50-53,161-168` | 保持 |
| 静音门控录制（"Ignore silence"：absMax<10e-6 时本块直接跳过写盘，无 pre-roll/hang/头尾修剪） | `gated_recorder.cpp:113-117,67-88` pre-roll ring buffer + attack/release 包络 + hangover + 头尾修剪 + fade-in + 归一化 | **反超**：上游只是静音跳过不是门控录制；我方是完整触发录制器，方向相反，勿误以为重复造轮子 | `repos/sdrpp/misc_modules/recorder/main.cpp:28,535-545`；我方 `cpp/src/dsp/gated_recorder.cpp:28-29,67-117` | 保持 |
| 音频网络 sink（network_sink：mono/stereo int16 LE，TCP server/UDP） | `network_audio_sink.cpp:59-220` UDP connected + TCP server + keepalive + RCVTIMEO + 单客户端替换 + EPIPE 检测 | **反超**：我方更鲁棒（注意上游 network_sink 是音频 int16 不是 IQ） | `repos/sdrpp/sink_modules/network_sink/main.cpp:241-259`；我方 `cpp/src/dsp/network_audio_sink.cpp:59-220` | 保持 |
| rtl_tcp 客户端握手 | `rtl_tcp_client.cpp:80` worker 直接 recv IQ，不读 daemon 启动时 12 字节 `RTL0` magic + tuner_type + gain_count | 我方 `rtl_tcp_source.cpp:114-152` 正确 drain 握手 + poll 超时保护 | **反超**：上游前 12 字节 magic 被当成 6 个 IQ 样本（DC 尖刺）；我方比 SDR++ 更兼容真实 rtl_tcp daemon | `repos/sdrpp/source_modules/rtl_tcp_source/rtl_tcp_client.cpp:80-87`；我方 `cpp/src/dsp/rtl_tcp_source.cpp:114-152` | 保持 |

---

## 6. 收口统计

- **本轮 W2 补齐（进行中，仅 2 项）**：
  1. AGC 块级峰值前瞻防削波（`agc.h:91-104` 机制 → 我方 `agc.cpp:38-56`）；
  2. maxHold 逐帧衰减（`waterfall.cpp:935-939` 机制 → 我方 `spectrum_display.h:343`）。
- **YAGNI（不补，理由见表）**：Nuttall LPF live 联调、CTCSS、FM-IF NR、NoiseBlanker 接 IF、RDS、多级 tap plan、VOLK-SIMD、FFTW、FastAGC、fastAtan2、cron 时间触发、能力查询 getter、stale 时间戳、1M LUT、零拷贝环行、trace EMA、连续 IQ 导出、WAV 多比特深、VFO 级 IQ 导出、WAV 回放、静噪搬 IF、导频 BPF、去加重挪位。
- **我方反超/已对齐**：Decoder 真实现（vfo_manager 内聚）、ANR STFT-Wiener、gated_recorder、静噪 hangover、立体声 blend、M/S 对称 FIR、SigMF 容器/无 4GB 限制、文件名碰撞规避、network_audio_sink 鲁棒性、rtl_tcp RTL0 握手、块最大降采样 double 精度、滑动窗 autoRange、Hann/Flattop 窗口集、dB 环历史省内存。

> 红线：本表只汇总 docs/learn/phase35/ 五篇笔记的裁决，不新增机制主张；W2 落地域（cpp/ tools/ mobile/）不在本文件触碰范围。
