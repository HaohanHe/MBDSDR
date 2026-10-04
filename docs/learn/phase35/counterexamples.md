<!-- SPDX-License-Identifier: MIT | Phase35 W2：反例核查汇总（docs 域，只从五篇笔记提炼） -->

# Phase35 反例核查清单（counterexamples）

> 用途：列出 SDR++ 里我方**「以为有、但实际没有或不同」**的点，每条带上游 `file:line` 证据 + 我方对照。
> 来源：`docs/learn/phase35/{radio-frontend,dsp-core,scheduler-modem,spectrum-render,file-sink}.md`。
> 目的：避免为"对齐 SDR++"而做错方向的补齐，也避免误把上游半成品/坑当范本。

---

## A. radio-frontend（L1）

### A1. 「radio 链上有 AGC」——没有
- 上游：IF/AF chain 都没挂 AGC block，`agc.h` 类存在但 radio 模块没用，靠硬件增益 + 静噪。
- 证据：`repos/sdrpp/decoder_modules/radio/radio_module.h:94-110`（chain 初始化无 AGC 节点）。
- 我方对照：我方加 audio AGC（`cpp/src/dsp/agc.cpp`）是合理的产品选择，不是"补 SDR++ 已有而我方缺失"。

### A2. 「我方 NFM 有鉴频后低通」——live chain 是关的
- 上游：鉴频后 Nuttall LPF 是真实可开关块（`fm.h:110-130`）。
- 我方：`NuttallLpf` 单测通过，但 `firEnabled_=false`，live chain 关闭（block-streaming 跟 channelizer 交互有问题）。
- 证据：我方 `cpp/src/dsp/demod.cpp:93-95,166-170`。
- 结论：这是"做了没跑通"的联调 bug，不是特性；邻道串扰目前靠 VFO 带宽兜底。见 gap-table §1 YAGNI 行。

### A3. 「SDR++ 立体声有自动单/立体声切换」——没有
- 上游：开了立体声就一直解，导频丢了直接出噪声，无切换。
- 证据：`repos/sdrpp/decoder_modules/radio/broadcast_fm.h:144-191`。
- 我方对照：`wfm_stereo.cpp:186-215` 有 quality/blend 平滑过渡，是我方优势，别被上游带偏。

### A4. 「SDR++ 静噪有迟滞」——没有
- 上游：PowerSquelch 整块平均幅度→dB→过门限，无迟滞、无 hangover；文件头自曝 `// TODO: Rewrite better!!!!!`。
- 证据：`repos/sdrpp/decoder_modules/radio/power_squelch.h:4,33-50`。
- 我方对照：`squelch.cpp:25-44` 快 attack 5ms/慢 decay 50ms + hangover 200ms（`squelch.h:42`），更细腻。

### A5. 「去加重在鉴频后立刻做」——SDR++ 放在 AF 链 48 kHz 重采样后
- 上游：去加重是音频域操作，在重采样到 48 kHz 之后做。
- 证据：`repos/sdrpp/decoder_modules/radio/radio_module.h:105,110`、`deephasis.h:58-94`。
- 我方对照：在 IF SR 下做（`demod.cpp:133-134,183-184`），数值差异不大，但上游位置更"正确"；重构时再挪。

### A6. 「NFM 有立体声」——没有
- 上游：NFM 输出 mono→stereo 只是复制左右声道。
- 证据：`repos/sdrpp/decoder_modules/radio/fm.h:87-94`。
- 结论：立体声是 WFM 广播独有，不要以为 NFM 也解立体声。

---

## B. dsp-core（L2）

### B1. 「SDR++ 有自己的 FFT」——错，完全外包 FFTW3f
- 上游：`math/` 下没有 fft 模块，FFT 直接 `fftwf_plan_dft_1d` + `FFTW_ESTIMATE`，VOLK 加窗/出功率谱。
- 证据：`repos/sdrpp/core/src/signal_path/iq_frontend.cpp:60-62,252-262`。
- 我方对照：`cpp/src/dsp/fft.cpp` 手写 radix-2 Cooley-Tukey、std::complex、无外部依赖。
- 结论：我方手写 FFT 并非"落后于 SDR++ 的自研 FFT"，而是不同依赖路线；**不要为"对齐 SDR++"而引入 FFTW**。

### B2. 「PowerSquelch 是范本」——不是，上游自带 TODO
- 上游：整块均值能量门、无迟滞/去抖，会在门限附近 chatter；作者标 `// TODO: Rewrite better!!!!!`。
- 证据：`repos/sdrpp/core/src/dsp/noise_reduction/power_squelch.h:4`。
- 我方对照：勿向它看齐，我方 hangover 静噪更好。

### B3. 「AGC 前瞻是免费的」——不是，上游最坏 O(n²)
- 上游：`loop/agc.h:93-101` 是逐样点预测 + 命中才内层扫整块，最坏 O(n²)。
- 证据：`repos/sdrpp/core/src/dsp/loop/agc.h:91-104`。
- 我方对照（W2 落地）：按块级一次性 `max_element` 峰值扫描，而非逐样点嵌套，避免复制上游的 O(n²) 写法。

### B4. 「生产路径干净」——上游留了调试打印
- 上游：`rational_resampler.h:144` `fprintf(stderr,...)`、`:162` `printf("[Resamp]...")` 每次 reconfigure 都打。
- 证据：`repos/sdrpp/core/src/dsp/multirate/rational_resampler.h:144,162`。
- 我方对照：落地走日志通道，不许 stdout 直打——这是上游可诟病处，不照抄。

### B5. 「多相相位索引要跟上游一致」——不必，两种约定都对
- 上游：`polyphase_bank.h:32` 反向 `(phaseCount-1)-(i%phaseCount)`。
- 我方：`rational_resampler.h:145-151` 正向切分 `e_p[n]=h[n*L+p]`，注释已写明标准形式。
- 结论：只是相位排布约定不同，均正确，非 bug，勿"对齐"成上游写法。

---

## C. scheduler-modem（L3）

### C1. 「SDR++ scheduler 是个调度器」——它根本没做时间调度
- 上游：倒计时列直接打印字面量 `"todo"`；触发条件硬编码一行 `"Every day at 00:00:00"`，无定时器、无重复周期、无下一次触发计算。它只是"动作序列编辑器"。
- 证据：`repos/sdrpp/misc_modules/scheduler/src/main.cpp:116`、`sched_task.h:63`。
- 我方对照：`task_orchestrator.h:76-77` 刻意不持有定时器/墙钟，触发时机交引擎——**不采纳上游这一半成品方向**。

### C2. 「Start Recorder 动作能用」——空实现冒充可用
- 上游：`StartRecorderClass::trigger(){}` 是空函数，UI 里却叫 "Start Recorder"。
- 证据：`repos/sdrpp/misc_modules/scheduler/src/actions/start_recorder.h:10-11`。
- 我方对照：坚持诚实空态/显式 TODO，不发空实现冒充可用。

### C3. 「shipped UI 里的占位是真功能」——字面量混进成品
- 上游：倒计时列 `"todo"`（`main.cpp:116`）、触发条件写死 `"Every day at 00:00:00"`（`sched_task.h:63`）。
- 我方对照：不把未实现项以成品形态进 UI。

### C4. 「基类 valid 状态是统一来源」——分裂成死代码
- 上游：`sched_action.h:24-27` 私有 `valid` 经 `isValid()` 暴露，但子类根本不写它，子类写的是 `showEditMenu(bool& valid)` 出参——`isValid()` 永远 false。
- 证据：`repos/sdrpp/misc_modules/scheduler/src/sched_action.h:19-27`、`actions/tune_vfo.h:94`。
- 我方对照：状态只留一处真实来源。

### C5. 「裸 new + map 管理 decoder」——不 delete / VFO 重复创建
- 上游：weather_sat `new SatDecoder(...)` 存裸指针 map 从不 delete（`main.cpp:49`）；m17 在构造函数（`:55`）和 enable()（`:100`）各 createVFO 一次、前者未删。
- 我方对照：`vfo_manager.h:78,96,105` 用 `unique_ptr` 化，已是正确姿势，继续保持。

### C6. 「领域 Action 可以直接画 UI」——不要，会把 GUI 拉回领域对象
- 上游：`ActionClass` 既管业务又管 ImGui 编辑菜单。
- 证据：`repos/sdrpp/misc_modules/scheduler/src/sched_action.h:13-14`。
- 我方对照：`frequency_scanner.h:21-22` 纯逻辑无 QObject，领域与 Qt/UI 分层，这条边界要守住。

---

## D. spectrum-render（L4）

### D1. 「色板 LUT 越大越好」——上游 1M 项是过度设计
- 上游：`WATERFALL_RESOLUTION 1000000`，4 MB 常驻 BSS。
- 证据：`repos/sdrpp/core/src/gui/waterfall.h:11,236`、`waterfall.cpp:944-958`。
- 我方对照：`spectrum_render.h:121-140` buildLut256 仅 256 项——8-bit 显示深度足够，YAGNI。

### D2. 「doZoom 用 float 累加没事」——长图终点漂移
- 上游：`factor = float(width/outSize)` 累加 id，长图漂移几个 bin。
- 证据：`repos/sdrpp/core/src/gui/waterfall.cpp:74,88`。
- 我方对照：`spectrum_render.h:82-107` 用 `double scale`，避免漂移——我方做法正确。

### D3. 「每帧 new 一行 scratch 没事」——堆分配抖动
- 上游：`updateWaterfallFb` 每调用 `new float[dataWidth]`。
- 证据：`repos/sdrpp/core/src/gui/waterfall.cpp:608`。
- 我方对照：`spectrum_display.cpp:779-780` 复用 `decScratch_` scratch 行，已避开。

### D4. 「onResize 手工旋环」——脆弱易越界
- 上游：onResize 用 malloc+memcpy 手工旋转环形行。
- 证据：`repos/sdrpp/core/src/gui/waterfall.cpp:742-749`。
- 我方对照：重分配整圈 ringDb_，无旋转。

### D5. 「锁越多越安全」——4 把递归 mutex 易死锁
- 上游：缓冲交接靠 4 把递归 mutex。
- 证据：`repos/sdrpp/core/src/gui/waterfall.h:249-252`。
- 我方对照：Qt 单线程 UI 摄入，无此问题。

### D6. 「GUI 只暴露 3 种窗就够」——我方更全
- 上游：IQFrontEnd 只接 RECT/BLACKMAN/NUTTALL，Hann/Hamming/Flattop 在 GUI 层没暴露。
- 证据：`repos/sdrpp/core/src/signal_path/iq_frontend.h:17-21`。
- 我方对照：`power_spectrum.cpp:79-104` Hann/Flattop/Blackman 全暴露，略优。

### D7. 「SNR 用 guard band 估计噪声底」——会被邻道污染
- 上游：选中 VFO 带内取 max、两侧 guard band 取平均当噪声底。
- 证据：`repos/sdrpp/core/src/gui/waterfall.cpp:558-598,576-587`。
- 我方对照：`setNoiseFloorDb` 注入真实测量噪声底，更诚实，不抄。

### D8. 「_fullUpdate 跳过瀑布重染」——平移后短暂条纹残留
- 上游：`_fullUpdate` 开关控制是否整圈重染，跳过时平移后有条纹残留。
- 证据：`repos/sdrpp/core/src/gui/waterfall.h:302`、`:1087`。
- 我方对照：`materialiseHistory` 每帧全量重渲，无残留。

---

## E. file-sink（L5）

### E1. 「sink_modules/ 下有 file_sink / wav_sink」——不存在
- 上游：`sink_modules/` 下只有 audio_sink / new_portaudio_sink / network_sink / android_audio_sink（音频类）；录制模块实际在 `misc_modules/recorder/`，是挂在流上的旁路模块而非 sink。
- 证据：`ls repos/sdrpp/sink_modules/`；`repos/sdrpp/misc_modules/recorder/main.cpp`。
- 结论：学习入口要改到 misc_modules/recorder，不要去 sink_modules 找 file_sink。

### E2. 「SDR++ 长录支持 RF64」——声明了但没实现
- 上游：`wav.h:21` 枚举 `FORMAT_RF64`，但 recorder UI 里被注释掉；`riff.cpp:99` 只写 uint32 size——录到 >4GB 时 chunk size 字段回绕、文件损坏。
- 证据：`repos/sdrpp/core/src/utils/wav.h:21`、`recorder/main.cpp:58`、`riff.cpp:99`。
- 我方对照：`recorder.cpp:155-156` SigMF 裸写无 4GB 限制。曾以为"我方要补 RF64"，实际不需要。

### E3. 「rtl_tcp_client 会读握手」——不读
- 上游：worker 直接 `sock->recv` 起读 IQ，没读 daemon 启动时发的 12 字节 `RTL0` magic + tuner_type + gain_count——前 12 字节被当成 6 个 IQ 样本（DC 尖刺）。
- 证据：`repos/sdrpp/source_modules/rtl_tcp_source/rtl_tcp_client.cpp:80-87`。
- 我方对照：`rtl_tcp_source.cpp:114-152` 正确 drain 握手 + poll 超时保护——我方比上游更兼容真实 rtl_tcp daemon。

### E4. 「我方有 iq_exporter 网络 IQ 导出」——没有
- 上游：`iq_exporter` 是连续 TCP/UDP IQ 流（Reshaper 定长切片、int8/16/32/f32）。
- 我方对照：`cpp/src/dsp/` 下无 `iq_exporter.*`；grep 仅命中 `spectrum_engine.h:125` 注释，实际函数是 one-shot 落盘的 `exportIqSegment`（`spectrum_engine.h:133`）。
- 结论：这是真差距，但按 gap-table §5 裁决 YAGNI（本机分析 + MCP dump 形态）。

### E5. 「SDR++ recorder 的 Ignore silence 是门控录制」——不是
- 上游：只是 `absMax < 10e-6` 时本块跳过写盘，无 pre-roll、无 hangover、无触发沿。
- 证据：`repos/sdrpp/misc_modules/recorder/main.cpp:28,535-545`。
- 我方对照：`gated_recorder.cpp` 是完整触发录制器（pre-roll ring + attack/release + hangover + 头尾修剪 + fade-in）。方向相反，勿误以为重复造轮子。

### E6. 「network_sink 是 IQ 流」——是音频 int16
- 上游：名字带 network 易误读，实际 wire format 是 mono/stereo int16 LE。
- 证据：`repos/sdrpp/sink_modules/network_sink/main.cpp:246-258`。
- 我方对照：与 `network_audio_sink` 同类，不是 IQ。

### E7. 「file_source 的 WavReader 是通用回放」——很脆
- 上游：假定固定 44 字节头，EOF `seekg(sizeof(WavHeader_t))` 回绕循环；不跳 chunk padding、不识别 fact/PEAK/afsp 等扩展 chunk、不识别 RF64。
- 证据：`repos/sdrpp/source_modules/file_source/wavreader.h:47,62-76`。
- 我方对照：不支持 WAV 回放反而是干净的（回放自己录的 SigMF），不要按它的脆弱实现抄。

### E8. 「文件名模板不会撞」——同秒同频会覆盖
- 上游：`genFileName` 用秒级时间戳，无碰撞检测。
- 证据：`repos/sdrpp/misc_modules/recorder/main.cpp:498-503`。
- 我方对照：`recorder.cpp:50-53` 有 `_2/_3` 碰撞规避，是真实改进点。

### E9. 「iq_exporter 断线探测可靠」——jank
- 上游：UI 线程用 `NONBLOCKING recv(1 byte)` 当心跳，会把对端发来的 1 字节命令吃掉。
- 证据：`repos/sdrpp/misc_modules/iq_exporter/main.cpp:386`。
- 我方对照：`network_audio_sink.cpp:198-213` 独立线程 recv 阻塞检测 FIN/RST，机制更干净。

### E10. 「baseband IQ 写 WAV 是标准 WAV」——是约定俗成
- 上游：baseband 模式写 WAV 时声道数=2，IQ 复数按 L=I/R=Q 写成立体声 WAV。
- 证据：`repos/sdrpp/misc_modules/recorder/main.cpp:179`。
- 我方对照：SigMF cf32_le 不存在这个歧义。

### E11. 「network_sink 采样率表是从设备能力查来的」——是工程经验表
- 上游：两个步长表合并去重再 sort（`12k–200k 步进 12k` ∪ `11025–192k 步进 11025`）。
- 证据：`repos/sdrpp/sink_modules/network_sink/main.cpp:68-76`。
- 我方对照：不暴露采样率下拉（链路采样率由 source 决定），不需要这张表。

### E12. 「nameTemplate 是 std::string」——是 1024 字节定长数组
- 上游：`char nameTemplate[1024]`，加载配置时 `substr(0, sizeof-1)` 截断。
- 证据：`repos/sdrpp/misc_modules/recorder/main.cpp:586`、`:100-103`。
- 我方对照：用 QString 无此截断问题。

---

## 收口

- 本清单每条均来自五篇笔记的"反例核查"节，未新增主张；与 `gap-table.md` 的 YAGNI/反超行一一对应。
- 最高频教训：**不要为"对齐 SDR++"而做补齐**——SDR++ 在 FFT（外包 FFTW）、scheduler（倒计时写 todo）、静噪（无迟滞）、rtl_tcp（不读握手）、RF64（声明未实现）、录制门控（只是静音跳过）等点上要么是半成品、要么走了不同路线，我方现状并不缺。
- W2 落地的两项（AGC 前瞻、maxHold 衰减）落地时须特别注意 **B3**：按块级峰值扫描实现，勿复制上游逐样点 O(n²) 嵌套。

> 红线：本文件只写 docs/learn/phase35/，不触碰 cpp/ tools/ mobile/；无虚构，所有 file:line 引自五篇笔记。
