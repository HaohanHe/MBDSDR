# Phase35 L5：SDR++ 文件/录制模块深读 + 反例核查

> 精读对象：`repos/sdrpp/misc_modules/recorder`、`misc_modules/iq_exporter`、`sink_modules/network_sink`、`core/src/utils/wav.{h,cpp}`、`core/src/utils/riff.{h,cpp}`、`source_modules/rtl_tcp_source`、`source_modules/file_source`。
> 我方对照：`cpp/src/dsp/recorder.*`、`gated_recorder.*`、`wav_writer.*`、`network_audio_sink.*`、`file_source.*`、`rtl_tcp_source.*`、`spectrum_engine.h::exportIqSegment`。
> 基线：HEAD b20bfb7；只学机制不抄代码（GPL 干净室）。

---

## 1. 机制总结

SDR++ 的"录制"不在 `sink_modules/`，而在 `misc_modules/recorder`——它是一个**挂在 VFO/sink 流上的旁路模块**，不是 sink。`sink_modules/` 下只有音频类 sink（audio_sink / new_portaudio_sink / network_sink / android_audio_sink），**没有 file_sink、没有 wav_sink**（反例 §5-1）。

两条录制输入路径：
- **Baseband 模式**：`sigpath::iqFrontEnd.bindIQStream(basebandStream)`（recorder/main.cpp:209）旁路整段中频 IQ，写复数 WAV。
- **Audio 模式**：从 `sigpath::sinkManager` 拿某条解调音频流（stereo 或 mono），经 `Volume → Splitter → PeakLevelMeter` 分两路（计量 + 写盘）（recorder/main.cpp:108-117）。

底层写盘统一走 `wav::Writer`（core/src/utils/wav.cpp），再委托 `riff::Writer`（core/src/utils/riff.cpp）做 RIFF chunk 栈式写入、关闭时回填 chunk size。

`iq_exporter` 是**连续 IQ 网络流**（TCP server / TCP client / UDP），与文件录制平行：Baseband 模式绑 IQ 流，VFO 模式自建一个指定采样率的 VFO，经 `dsp::buffer::Reshaper` 按包长切片后用 volk 转 int8/16/32/float32 发出（iq_exporter/main.cpp:487-528）。

`network_sink` 是**音频网络 sink**（int16 LE，TCP server / UDP），不是 IQ。

音频回放侧（`audio_sink` / `new_portaudio_sink`）走 RtAudio：枚举输出设备、按设备 preferredSampleRate 建采样率下拉（audio_sink/main.cpp:49-72, 112-120），`Packer` 按 512 样本切块后送 RtAudio 回调。我方对应 `audio_output.*`（Qt Multimedia / miniaudio），机制同构，无需深读。

生命周期要点：recorder 在 `start()` 里先配 writer 参数再 `writer.open()`，失败直接 return 不启动 DSP 链（recorder/main.cpp:178-190）；`stop()` 里先解绑流、再 `writer.close()`（recorder/main.cpp:219-235），顺序反了会丢尾块。ModCom 接口常量集中在 `recorder_interface.h:3-13`（`GET_MODE/SET_MODE/START/STOP` + `MODE_BASEBAND/MODE_AUDIO`）。

---

## 2. 关键算法（file:line）

### 2.1 WAV/RF64 写盘
- RIFF chunk 栈：`beginChunk` 占位写 8 字节头，`endChunk` 回填 size 并累加父 chunk（riff.cpp:83-106）。
- **size 字段是 `uint32_t`**（riff.h:12 `uint32_t size`；riff.cpp:99 写 `sizeof(desc.hdr.size)`）。
- WAV 容器枚举里声明了 `FORMAT_RF64`（wav.h:21），但 recorder UI 里被注释掉（recorder/main.cpp:58 `// containers.define("RF64", wav::FORMAT_RF64); // Disabled for now`）——**实际没有 RF64 实现**。
- PCM 量化用 volk：`volk_32f_s32f_convert_16i(..., 32767.0f, n)`（wav.cpp:166）、`convert_32i`（wav.cpp:170）；uint8 无 volk 路径，走标量循环 `(x*127)+128`（wav.cpp:160-162）。

### 2.2 录制控制
- 文件名模板：`$t_$f_$h-$m-$s_$d-$M-$y`，`std::regex_replace` 逐个替换（recorder/main.cpp:511-519）；`$r` 注入解调模式字符串（NFM/WFM/...，recorder/main.cpp:504-508）。
- 时区 local/UTC 切换（recorder/main.cpp:478）。
- "Ignore silence"：对每个 block 算 `fabsf` 最大值，小于 `SILENCE_LVL=10e-6`（recorder/main.cpp:28）就**本块直接丢弃、不写盘**（recorder/main.cpp:535-545）——不是 gate，只是静音跳过，无 pre-roll、无 hang、无头尾修剪。
- ModCom 接口对外暴露 `GET_MODE/SET_MODE/START/STOP`（recorder/main.cpp:563-581），供 scanner/scheduler 远程触发。
- 并发：`std::recursive_mutex recMtx` 包住 start/stop/stream 切换（recorder/main.cpp:167, 216, 377）。

### 2.3 IQ 网络导出
- 采样率档位表：3k–192k 步进 ×2、250k–1M 步进 250k、1M–10M 步进 500k、10M–100M 步进 5M（iq_exporter/main.cpp:54-65）。
- `Reshaper` 把连续 IQ 重组成 `packetSize/sampleSize()` 个样本的包（iq_exporter/main.cpp:133, 341）——UDP 友好的定长数据报。
- 发送热路径：`sockMtx.try_lock()`，锁不到就**整包丢弃**（iq_exporter/main.cpp:491-498）——不阻塞 DSP 线程。
- TCP server：`listenWorker` 线程阻塞 accept，新客户端替换旧 socket（iq_exporter/main.cpp:458-470）。
- 断线检测：UI 线程用 `NONBLOCKING recv(1 byte)` 探测对端是否已关（iq_exporter/main.cpp:386）。

### 2.4 rtl_tcp 客户端（残余模块扫）
- 命令格式：1 字节 cmd + 4 字节 big-endian 参数，`sendCommand`（rtl_tcp_client.cpp:70-73）。
- bufferSize = `sr/200`（rtl_tcp_client.cpp:35）。
- **worker 直接 `sock->recv(buffer, bufferSize*2, true)` 起读 IQ**（rtl_tcp_client.cpp:80），**没有读 librtlsdr rtl_tcp daemon 启动时发的 12 字节 `RTL0` magic + tuner_type + gain_count**（反例 §5-3）。
- uint8→float 标量转换 `(x-128)/128`（rtl_tcp_client.cpp:86-87）。

### 2.5 file_source WAV 回放（残余模块扫）
- `WavReader` 假定固定 44 字节头（wavreader.h:62-76），EOF 时 `seekg(sizeof(WavHeader_t))` 回绕循环（wavreader.h:47, 54）——不跳 chunk padding、不识别 RF64、不读 fact/其他 chunk。

---

## 3. 可借鉴点（机制层）

1. **RIFF chunk 栈 + 关闭回填 size**（riff.cpp:83-106）：我们的 `WavWriter::stop` 已经是 rewind + patch（wav_writer.cpp:93-97），机制等价，无需改。
2. **`try_lock` 丢包而非阻塞 DSP**（iq_exporter/main.cpp:491）：我们的 `NetworkAudioSink::sendMono` 已经用 `SO_SNDTIMEO=200ms` + 失败计数（network_audio_sink.cpp:75-78, 252-267），比 try_lock 更诚实（有错误计数）。
3. **Reshaper 定长切片**（iq_exporter/main.cpp:133）：若未来做 UDP IQ 流，这是标准做法；当前网络音频块已经是固定 DSP block，无需引入。
4. **ModCom 远程 start/stop**（recorder/main.cpp:563）：我们已有 MCP tool 暴露 `startRecording/stopRecording/exportIqSegment`，等价。
5. **文件名模板 `$f/$h/$m/$r`**（recorder/main.cpp:511-519）：可借鉴的是"频率+时间+模式"自描述命名；我们的 `stamp_freqHz` 已经做到（recorder.cpp:45-46），且带碰撞规避 `_2/_3`（recorder.cpp:50-53），比 SDR++ 的模板更稳（模板在同秒同频会覆盖）。
6. **流注册/反注册事件回调**（recorder/main.cpp:422-450）：sinkManager 流列表动态维护，新流自动接管。我方 demod 链是静态连接，不需要这套动态路由。
7. **PeakLevelMeter 表尾衰减显示**（recorder/main.cpp:452-462）：UI 用自然对数 `10*logf(rawLvl)` 转 dB，再按帧时长以 50/s 速率衰减——比我方 `audio_link_health` 的瞬时峰值显示更有"表针回弹"手感。可在 v0.9 音量表借鉴。
8. **autoStart 持久化**（iq_exporter/main.cpp:117-119, 140）：配置里存 `running` 标志，模块加载时自动重连。我方 `NetworkAudioSink` 不持久化，靠 UI/MCP 手动 start——与产品形态一致。

---

## 4. 我方差距判定（补齐 vs YAGNI）

| 维度 | SDR++ | 我方 | 裁决 |
|---|---|---|---|
| 录制容器 | WAV (u8/i16/i32/f32) | SigMF cf32_le + JSON sidecar（recorder.cpp:91-111）；音频 WAV i16（gated_recorder.cpp:183-200） | **不补**。SigMF 无 4GB 限制、自带元数据，研究场景优于 WAV；音频 i16 WAV 已是交换标准。 |
| >4GB 长录 | 实际会坏（uint32 size，RF64 未实现） | SigMF 裸写无上限（recorder.cpp:155-156） | **我们更好**，反例 §5-2。 |
| 自动分段 | 无 | 按样本数分段 + 碰撞规避文件名（recorder.cpp:161-168, 134-151） | **我们更好**。 |
| 静音门控录制 | 粗糙 abs-max 跳过（recorder/main.cpp:535） | pre-roll ring buffer + attack/release 包络 + hangover + 头尾修剪 + fade-in + 归一化（gated_recorder.cpp:113-117, 28-29, 67-88） | **我们更好**。 |
| 连续 IQ 网络导出 | TCP srv / TCP cli / UDP，int8/16/32/f32（iq_exporter/main.cpp:68-83） | **无**。只有 one-shot `exportIqSegment` 落盘（spectrum_engine.h:133） | **YAGNI**。产品形态是本机分析 + MCP 按需 dump，不对外做 IQ 服务器；外部 SDR 客户端接入不在 v0.8 范围。若后续要做 SDR++ 兼容的 IQ stream server，再按 Reshaper+volk 机制干净室重写。 |
| 音频网络 sink | TCP/UDP int16（network_sink/main.cpp:241-259） | UDP connected + TCP server + keepalive + RCVTIMEO + 单客户端替换 + EPIPE 检测（network_audio_sink.cpp:59-220） | **我们更鲁棒**。 |
| WAV 多比特深 | u8/i16/i32/f32 可选（recorder/main.cpp:59-62） | i16 only（wav_writer.cpp:75-83） | **YAGNI**。i16 WAV 覆盖 99% 回放场景；f32 WAV 用 SigMF cf32 代替。 |
| VFO 级 IQ 导出 | 可建指定采样率 VFO 后导出（iq_exporter/main.cpp:433-438） | 仅 baseband 整段导出（spectrum_engine.h:126） | **YAGNI**。VFO 重采样导出等价于 demod 后再采样，与现有 demod 链重叠。 |
| rtl_tcp 握手 | **不读 RTL0**（rtl_tcp_client.cpp:80） | 读 12 字节 `RTL0` magic + tuner_type + gain_count，poll 超时保护（rtl_tcp_source.cpp:114-152） | **我们更正确**，反例 §5-3。 |
| file_source 回放 | WAV 固定头循环（wavreader.h） | SigMF cf32 回放（file_source.cpp:29-60） | **YAGNI**。回放自己录的 SigMF 即可；WAV 回放用 Audacity/external tool。 |

---

## 5. 反例核查汇总（"我方以为有但实际没有/不同"）

1. **任务书说 `sink_modules/file_sink`、`wav_sink`——这两个模块在 SDR++ 里不存在。** `sink_modules/` 下只有 audio_sink / new_portaudio_sink / network_sink / android_audio_sink（`ls sink_modules/`）。录制模块实际在 `misc_modules/recorder/`。学习入口要改到 misc_modules。
2. **SDR++ 声明了 RF64 但没实现。** `wav.h:21` 枚举 `FORMAT_RF64`，但 `recorder/main.cpp:58` 注释掉了 UI 选项，`riff.cpp:99` 只写 uint32 size——录到 >4GB 时 chunk size 字段回绕，文件损坏。我们曾以为"SDR++ 长录支持 RF64，我方要补"，实际不需要：SigMF 裸写无此问题。
3. **SDR++ rtl_tcp_client 不读 RTL0 握手。** `rtl_tcp_client.cpp:80` 直接 recv IQ，前 12 字节 magic 被当成 6 个 IQ 样本（DC 尖刺）。我方 `rtl_tcp_source.cpp:114-152` 正确 drain 了握手——**我方比 SDR++ 更兼容真实 rtl_tcp daemon**。
4. **任务书说对照"我方 iq_exporter"——我方没有网络 IQ 导出模块。** `cpp/src/dsp/` 下无 `iq_exporter.*`；grep 仅命中 `spectrum_engine.h:125` 的注释，实际函数是 one-shot 落盘的 `exportIqSegment`（spectrum_engine.h:133）。SDR++ 的 `iq_exporter` 是连续 TCP/UDP 流，这是真差距，但按 §4 裁决 YAGNI。
5. **SDR++ recorder 的 "Ignore silence" 不是门控录制。** 它只是 `absMax < 10e-6` 时跳过写盘（recorder/main.cpp:535-545），无 pre-roll、无 hangover、无触发沿。我方 `gated_recorder` 是完整的触发录制器——不要误以为"SDR++ 已有 gated recorder，我方重复造轮子"，方向相反。
6. **SDR++ network_sink 是音频 int16，不是 IQ。** 名字带 "network" 容易误以为是 IQ stream；实际 wire format 是 mono/stereo int16 LE（network_sink/main.cpp:246-258），与我方 `network_audio_sink` 同类。
7. **SDR++ file_source 的 WavReader 很脆。** `wavreader.h:47` EOF 回绕到 `sizeof(WavHeader_t)=44`，不处理 chunk padding（WAV data chunk 若奇数字节会补 1 字节），不识别 `data` chunk 前的 `fact/PEAK/afsp` 等扩展 chunk。我方不支持 WAV 回放反而是干净的——不要按它的脆弱实现抄。
8. **SDR++ recorder 文件名模板在同秒同频会覆盖。** `genFileName` 用秒级时间戳（recorder/main.cpp:498-503），无碰撞检测；我方 `recorder.cpp:50-53` 的 `_2/_3` 碰撞规避是真实改进点。
9. **SDR++ iq_exporter 的状态探测是 jank。** UI 线程 `NONBLOCKING recv(1 byte)` 当心跳（iq_exporter/main.cpp:386）——会把对端发来的 1 字节命令吃掉。我方 `NetworkAudioSink::acceptLoop` 用独立线程 `recv` 阻塞检测 FIN/RST（network_audio_sink.cpp:198-213），机制更干净。
10. **SDR++ recorder baseband 模式写 WAV 时声道数=2。** `writer.setChannels((recMode == AUDIO && !stereo) ? 1 : 2)`（recorder/main.cpp:179）——IQ 复数按 L=I/R=Q 写成立体声 WAV，这是 SDR++ 的约定俗成，不是标准 WAV。我方 SigMF cf32_le 不存在这个歧义。
11. **SDR++ network_sink 采样率表是两个步长表合并去重。** `12k–200k 步进 12k` ∪ `11025–192k 步进 11025`，再 sort（network_sink/main.cpp:68-76）——这是音频设备常用采样率的工程经验表，不是从设备能力查询来的。我方不暴露采样率下拉（链路采样率由 source 决定），不需要这张表。
12. **SDR++ recorder 的 `nameTemplate` 是 1024 字节定长数组。** `char nameTemplate[1024]`（recorder/main.cpp:586），加载配置时 `substr(0, sizeof-1)` 截断（recorder/main.cpp:100-103）——这是 C 风格 UI 缓冲，我方用 QString 无此问题。

---

## 6. 本轮动作

- 不写新代码。差距表落到 `gap-table.md` 时，"连续 IQ 网络导出" 与 "VFO 级 IQ 导出" 两项标记 YAGNI 并引用本文件 §4/§5-4。
- 反例 §5-1/§5-2/§5-3/§5-4 直接进 `counterexamples.md`。
- 若 v0.9 要做外部 IQ stream server（如给 GQRX/SDRangel 当源），按 iq_exporter/main.cpp:487-528 的 Reshaper+volk+try_lock 机制干净室重写，不要照抄。

## 7. 范围与未决

- **未深读**：`new_portaudio_sink` 与 `audio_sink` 的差异仅在 RtAudio 版本兼容层（audio_sink/main.cpp:34-60 的 `RTAUDIO_VERSION_MAJOR >= 6` 宏分支），机制同构，按 §1 末尾处理。
- **未深读**：`misc_modules/scanner`（306 行，自动扫频触发录制）——它通过 ModCom 调用 recorder 的 `START/STOP`（recorder_interface.h:3-8），机制已被 §2.2 覆盖；我方 `frequency_scanner.*` 是独立实现，无需对照。
- **未验证**：SDR++ iq_exporter 在 UDP 模式下是否真的走 connected UDP（`net::openudp(host, port, "0.0.0.0", 0, true)`，iq_exporter/main.cpp:212 的最后一个 `true` 参数语义未读 utils/net 实现）。不影响我方裁决。
- **红线遵守**：本文件只引 file:line 证据，不复制 GPL 代码片段；所有"借鉴"均为机制描述，落地时需干净室重写。
