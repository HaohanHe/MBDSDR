# B4 缺口学习笔记：实时声卡 / 多VFO / ANR / 热插拔 / 分段增益

> 审计对象：MBDSDR 根 `/home/user/Doubao/chats/38438160041798146`。
> 方法：每个专题先读上游真实源码 `file:line`，再读我方 `file:line`。上游 sdrpp=GPLv3、librtlsdr=GPLv2、gnuradio=GPLv3，**只学机制、不逐字照抄**（我方为 MIT 干净室）。拿不准处标「推断」。
> 云 VM 无硬件；凡依赖真机/USB 声卡的项，建议里明确标注，不写空壳。

---

## 专题 1 · 实时声卡播放

### ① 上游真实做法（sdrpp，GPLv3）
sdrpp 把"声卡"做成一个 **sink module**，核心是**回调拉取（pull）+ 缓冲打包**：
- `repos/sdrpp/sink_modules/audio_sink/src/main.cpp:222-230`：RtAudio 回调里 `stereoPacker.out.read()` 向上游 DSP 要一帧，`memcpy` 给声卡。声卡是主时钟，DSP 线程往 Packer 里填。
- `main.cpp:30-32`：`stereoPacker.init(_stream->sinkOut, 512)`——解调链的 `sinkOut` 流经一个 `dsp::buffer::Packer` 解耦 DSP 线程与声卡中断线程，防 underrun。
- `main.cpp:138`：选设备后 `_stream->setSampleRate(sampleRate)` **通知整条上游链重采样**到声卡首选采样率，而不是只在最后硬塞。
- `main.cpp:170-182`：`errorCallback` 专门处理 `RTAUDIO_DEVICE_DISCONNECT`——声卡被拔/掉线时记录并降级，而不是静默崩。
- `core/src/dsp/audio/volume.h:31`：音量用 `powf(volume,2)` 做感知电平，`volk_32f_s32f_multiply` 批量乘。

### ② 我方现状
- `cpp/src/dsp/iaudio_sink.h:31-70`：干净的 `IAudioSink` 抽象（write/writeStereo/setVolume/setMuted/isAvailable），`writeStereo` 自带降混默认实现（`:46-54`）。
- `cpp/src/dsp/qt_audio_sink.cpp:188-205`：**push 模型**——解调线程主动 `io_->write(bytes)` 推进 `QAudioSink`；`:169-186` 用朴素线性插值把 48k 重采样到设备率（无抗混叠）。
- 接线已通：`cpp/src/dsp/spectrum_engine.cpp:1040`（WFM 立体声 `writeStereo`）/`:1042`（单声道 `write`），前面串了 ANR→squelch→AGC（`:967/:1000/:1005`）。`memory_audio_sink` 是测试替身，`spectrum_engine.cpp:412-417` 可注入。
- 设备热切换：`qt_audio_sink.cpp:67-92` UI 线程只排队请求、worker 线程重建 sink。

### ③ 差距判定：**已有（接线完成），健壮性为半实现**
- 解调→声卡确实通了，不是空壳。
- 差距：(a) push 进 `QAudioSink` 内部缓冲，**没有 sdrpp 那种显式 Packer / underrun 保护与固定延迟档位**（bufferFrames=sr/60）；(b) 声卡**断开/掉线无检测与重连**（只在 `stateChanged` 打 warning，`qt_audio_sink.cpp:135-139`）；(c) 跨采样率用线性插值，高频镜像未滤；(d) 无"整条链重采样到设备率"的协商，靠最后硬转。

### ④ 最小真实实现建议
- **改** `cpp/src/dsp/qt_audio_sink.cpp`：在 `buildSink` 里把 `sink_->bytesFree()/processedUSecs()` 暴露成 `underrunCount()`（读 `QAudioSink::processedUSecs` 与已写字节差），供 UI 显示"声卡欠载"。云内可用 `MemoryAudioSink` + 注入假时钟做确定性单测。
- **改** `qt_audio_sink.cpp:135` 的 `stateChanged`：进入 `StoppedState` 且 error 为 `FatalError`/`IOError` 时，置 `available_=false` 并发 `deviceDisconnected()` 信号（真机项，云内只能测"信号发出"，不能测真拔插）。
- **改** 重采样：把 `resampleToDevice`（`:169`）换成与 `audio_resampler.*` 同一套多相 FIR（项目里已有 `audio_resampler.h`），消除线性插值镜像——云内可喂已知正弦、测阻带衰减，确定性可验。
- 真机标注：声卡拔插自动重连、真实延迟测量必须真机；云内只验信号路径与无设备降级。

---

## 专题 2 · 多 VFO 实战

### ① 上游真实做法（sdrpp，GPLv3）
- `core/src/signal_path/vfo_manager.h:10-40`：每个 VFO 是一个对象，持有自己的 `RxVFO* dspVFO` + `WaterfallVFO* wtfVFO`；`vfos` 是 `map<string,VFO*>`（`:66`）。
- `core/src/dsp/channel/rx_vfo.h:89-100`：**每 VFO 一条独立信道化链** = `FrequencyXlator`（频移 -offset）→ `RationalResampler`（in SR→out SR）→ `FIR` 低通（带宽）。宽带 IQ 被扇出给每个 VFO 各自做 NCO 偏移+抽取+滤波。
- `rx_vfo.h:72-77`：`setOffset` 只改 NCO 相位，**不重调谐 tuner**（带内移动）；`vfo_manager.h:42` `createVFO` 动态增删。
- 每个 VFO 可挂**自己的 sink**（各自出声）。

### ② 我方现状
- `cpp/src/dsp/vfo_manager.h:64-130`：`VfoChannel` 自带独立 `Channelizer` + `IDemod` + `AudioResampler`，甚至每通道 `DigitalDemod`/`RdsDecoder`/`WfmStereoDecoder`（`:92-114`）。
- 已端到端接线：引擎槽 `spectrum_engine.cpp:547-634`（vfoAdd/Remove/Select/SetFreq/SetOffset/SetBandwidth/SetMode/SetColor）；UI 按钮 `main_window.cpp:1968-2001`、瀑布带框 `spectrum_display.cpp:453-479`。
- `spectrum_engine.cpp:586-612` `vfoSetOffset`：实现了 SDR++ 式"带内只动 NCO、超出可用半带宽才重调 tuner"，其他 VFO 绝对频率不变、offset 自动重算。
- **设计约束**（`vfo_manager.h:10-13` 注释）：squelch/AGC/声卡/录音只作用于**选中 VFO**，其余 VFO 只做扇出解调+标记，不同时出声。

### ③ 差距判定：**已有（且相当完整）**
- 信道化扇出、带内移频、动态增删、UI 全部打通，非空壳。
- 与上游唯一实质差距：**多 VFO 不能同时监听**（只有选中者进声卡）。这是头文件明确写定的取舍，不是 bug。

### ④ 最小真实实现建议
- 若 Wave2 要"多 VFO 同时听"：在 `VfoChannel` 增加一个可选 `IAudioSink* monitorSink`，`VfoManager::process` 末尾把每个非选中通道的 `audio48k` 按小比例 mix 进一个共享 monitor 总线。云内用 `MemoryAudioSink` 断言两路都被写入、增益不溢出即可确定性验证。
- 其余保持现状即可，不建议重写。

---

## 专题 3 · ANR（降噪）

### ① 上游真实做法
- sdrpp `core/src/dsp/noise_reduction/noise_blanker.h:38-57`（GPLv3）：**IQ 域脉冲消除**——单极点 IIR 跟踪平均幅度 `amp`，`excess=inAmp/amp > level` 时把该样本乘 `1/excess` 软衰减。O(n)，无中值。
- gnuradio（GPLv3）的音频降噪经典路线是 STFT 谱减 / Wiener（`gr-audio`+`gnuradio-runtime` 的 FFT 块组合），机制与我方 ANR 同宗。

### ② 我方现状
- `cpp/src/dsp/anr.h:23-40`：**后解调音频域** STFT-Wiener WOLA（Hann 50% 重叠、decision-directed a-priori SNR、`G=xi/(xi+1)`、谱底+跨 bin 平滑抑音乐噪声），默认关（`:56-59` identity）。接线：`spectrum_engine.cpp:967` `anr_.process(raw)`。
- `cpp/src/dsp/noise_blanker.cpp:10-39`：**IQ 域脉冲消除**——滑动窗 mean+3.5σ 阈值，超阈样本用**本地中值幅度+原相位**替换。接线：`spectrum_engine.cpp:810` `noiseBlanker_.process(iq)`，UI 复选 `main_window.cpp:563`。

### ③ 差距判定：**已有（两路都真实且已接线）**
- ANR 算法完整度高于 sdrpp core（sdrpp core 无音频 ANR）。
- 小差距：`noise_blanker.cpp:19-37` 每样本重算窗统计 + 每脉冲 `nth_element`，复杂度 **O(n·W)**（宽带 2.4MSPS、W≈2400 时每块千万级运算），阈值 `kSigma=3.5` 硬编码（`:15`），无 level/窗宽旋钮。

### ④ 最小真实实现建议
- **改** `noise_blanker.cpp`：把滑动窗均值/方差换成**递推**（或环形缓冲 + 增量和），降到 O(n)，对齐 sdrpp 的廉价包络；把 `kSigma` 提为 `setLevel(float)` 暴露给 UI。云内确定性测试：喂"纯正弦 + 已知位置脉冲"，断言脉冲位置被压低、正弦主体 RMS 不变。
- ANR 保持现状；可选把 `anr.h:88-90` 的 strength 映射到 UI 滑条（引擎已有 `setAnrStrength`，`spectrum_engine.cpp:644`）。

---

## 专题 4 · 设备热插拔

### ① 上游真实做法（librtlsdr，GPLv2）
- **librtlsdr 本身不用 libusb hotplug 回调**。`src/librtlsdr.c:1297-1324` `rtlsdr_get_device_count` 是**拉取式轮询**：`libusb_get_device_list` 后按 `find_known_device(VID,PID)` 数一遍。
- 即"热插拔"在应用层 = **定时重数设备列表并 diff**，或应用自己挂 `libusb_hotplug_register_callback`（不在 librtlsdr C API 内）。

### ② 我方现状
- `cpp/src/dsp/rtl_sdr_source.cpp:116-145` `start()` 永远 `rtlsdr_open(&dev_,0)`（写死设备 0），无设备列表/多选。
- `:156-165` `readIQ`：`rtlsdr_read_sync` 失败时**仅 `return 0`**，`dev_` 仍非空 → `isConnected()`（`:188`）永远报 true（陈旧），断流后静默出零，无重连、无信号。
- `cpp/src/dsp/device_capabilities.h:52-60`：是**静态能力读回表**（从 rtl_tcp 握手解析 tuner 类型/可调范围），`:26` 自述 `enumerate()/probeDevice() are not used`——**不是设备枚举/热插拔检测器**。

### ③ 差距判定：**真缺**
- 无"设备插入"检测（无枚举列表、无轮询）。
- 无"设备拔出"检测（read_sync 失败被吞，连接状态不翻转）。
- 无自动重连。

### ④ 最小真实实现建议
- **改** `rtl_sdr_source.cpp:156`：连续 N 次 `read_sync<0` 后置 `running_=false`、`dev_=nullptr`、`isConnected()` 翻 false，并 `emit deviceLost()`。云内用 stub 源注入"read_sync 返回负"可确定性测到状态翻转与信号。
- **新增** `cpp/src/dsp/device_lister.{h,cpp}`：封装 `rtlsdr_get_device_count()/get_device_name()`（HAVE_RTLSDR 下），返回 `vector<{index,name}>`；stub 模式返回空。引擎加一个 ~1Hz 定时器 diff 列表，变化时 `emit deviceListChanged()`。云内 stub 可测 diff 逻辑。
- **真机标注**：真实 USB 拔插、自动 `open` 重连必须真机；云内只验"列表 diff 状态机"与"断流→状态翻转"。

---

## 专题 5 · 分段增益

### ① 上游真实做法（librtlsdr，GPLv2）
- `src/librtlsdr.c:956-1008` `rtlsdr_get_tuner_gains`：按 tuner 类型返回**离散合法增益档位表**（0.1dB 单位）。R82xx 29 档 `{0,9,14,27,...,480,496}`（`:966-969`），E4000 14 档（`:959-960`）。
- `src/librtlsdr.c:1028-1047` `set_tuner_gain`：传**一个总增益**，派发给 tuner 内部 `set_gain`。
- `src/tuner_r82xx.c:967-977`：R82xx 内部有 LNA/mixer/VGA 三级增益步表；`:1007-1017` 手动模式下**逐级累加直到 ≥ 目标总增益**，由驱动自动分配到 LNA/mixer。
- `librtlsdr.c:1057` `set_tuner_if_gain(dev,stage,gain)` 才是真·每级接口，但 R82xx 多未实现，主要 E4000 用。

### ② 我方现状
- `cpp/src/dsp/rtl_sdr_source.cpp:23-30` `setGain(gainDb)`：直接 `rtlsdr_set_tuner_gain(dev, gainDb*10)`——**连续值取整，未对齐设备离散档表**。
- **从未调用** `rtlsdr_get_tuner_gains`（全仓 grep 无）。UI 是连续滑条 `main_window.cpp:408-409` `setRange(0,50)` dB。
- `rtl_sdr_source.cpp:79-89` `setGainStage`：仅 stage 0 映射到总增益，其余 stage 警告忽略；`source.h:70` 基类默认空。
- 读回 telemetry `spectrum_engine.cpp:937-939` 报的是**请求值** `gainDb_`，不是驱动实际取整后的值。

### ③ 差距判定：**半实现**
- 总增益能用，但 (a) 未用离散档表，UI 显示"20dB"而驱动实际落在相邻档（如 19.7dB）；(b) 无真实 LNA/mixer/VGA 选档（librtlsdr C API 本就把这三级抽象掉了，真每级需 SoapySDR，而我方 Soapy 只在 TX）。

### ④ 最小真实实现建议
- **改** `rtl_sdr_source`：`start()` 后调 `rtlsdr_get_tuner_gains(dev,NULL)` 取档数、再取表，存 `std::vector<int> gainTableDb10_`；`setGain` 里**吸附到最近档**（就近，非向上）。新增 `std::vector<double> availableGainsDb()` 供 UI 画**步进 combo** 而非连续滑条。
- **改** `setGain` 后用 `rtlsdr_get_tuner_gain(dev)` 读回真实值，更新 `gainDb_`，使 telemetry 报真值。
- 云内确定性测试：构造一张假档表 `{0,140,290,496}`（0.1dB），断言 `setGain(20.0)` 吸附到 14.0dB、`availableGainsDb()` 长度=4。真机标注：真实档位表来自硬件，云内用注入表测吸附逻辑。
- 真·每级（LNA/mixer/VGA 独立）标注为**真机+SoapySDR 项**，Wave2 不做。

---

## Wave2 建议优先级清单（价值 ÷ 可云内验证性排序）

| 优先级 | 专题 | 事项 | 云内可验证性 |
|---|---|---|---|
| P0 | 5 分段增益 | `setGain` 吸附 `get_tuner_gains` 离散档 + 读回真值 + UI 步进 combo | 高（注入假档表单测吸附） |
| P0 | 4 热插拔 | read_sync 断流→`isConnected` 翻 false + `deviceLost` 信号 | 高（stub 注入失败码） |
| P1 | 4 热插拔 | `device_lister` 枚举 diff 状态机（~1Hz 定时器） | 高（stub 空列表/假列表 diff） |
| P1 | 3 ANR | NoiseBlanker 滑动窗改递推 O(n) + level 旋钮 | 高（脉冲抑制单测） |
| P2 | 1 声卡 | 线性重采样换多相 FIR（复用 audio_resampler） | 高（正弦阻带衰减测） |
| P2 | 1 声卡 | 声卡断开检测信号 + underrun 计数 | 中（信号可测，真拔插真机） |
| P3 | 2 多VFO | 非选中 VFO 监听 mix 总线 | 高（MemoryAudioSink 断言双路） |
| P3 | 5 分段增益 | 真·LNA/mixer/VGA 每级 | **真机+SoapySDR，云内不可验** |

> 一句话结论：**专题 1/2/3 主体已落地**（接线真实，非空壳），Wave2 只做健壮性打磨；**专题 4 是唯一"真缺"**（断流静默、无枚举）；**专题 5 是"半实现"**（总增益能用但未对齐离散档表）。P0 两项都能在云内用 stub/注入表做确定性测试，不依赖真机。
