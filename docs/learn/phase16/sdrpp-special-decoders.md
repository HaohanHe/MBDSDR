# Wave3 / P3 — SDR++ 专用 decoder 流式框架精读笔记（m17 / pager / vor / dab）

> 范围：`repos/sdrpp/decoder_modules/` 下
> `m17_decoder/`、`pager_decoder/`(POCSAG/FLEX)、`vor_receiver/`、`dab_decoder/`。
> 重点**不是**各协议的业务细节，而是「一个数字协议是怎样被组织成 source→同步→解调→解码的模块」这套通用流式框架。
>
> **版权声明**：SDR++ 为 GPLv3。下列引用仅为**机制学习**的注释性短片段（≤3 行，锚定 file:line），**不复制实现代码进 MBDSDR**；MBDSDR 落地走干净室（自有命名/结构，MIT）。
>
> **一句话结论**：四个 decoder 共享同一套「**建 VFO → 把 vfo->output 接进一条 DSP 链 → hier_block 拼装同步/解调/FEC/解码 → 回调或 sink 出结果**」骨架。MBDSDR 已用**自己的 pull 式（feed→take）状态类**覆盖了其中大部分；真正通用、可云内确定性验证、值得学的增量只有「接收连续性/超时状态机」和「测量-置信度平均」两样。

---

## 1. 真读 file:line + 注释性短片段

### 1.0 四者共享的模块骨架（先讲共性）

以 `m17_decoder/src/main.cpp` 为模板：

- `m17/main.cpp:55-56`：decoder 模块**自己向 VFO 管理器申请一个窄带 VFO**（而不是假设已有），决定自己要占多少带宽/采样率：
  ```cpp
  vfo = sigpath::vfoManager.createVFO(name, ImGui::WaterfallVFO::REF_CENTER, 0,
                                      9600, INPUT_SAMPLE_RATE, 9600, 9600, true);
  vfo->setSnapInterval(250);
  ```
- `m17/main.cpp:59-62`：把 `vfo->output` 接进解码链，链末端分两路（音频 + 星座诊断）：
  ```cpp
  decoder.init(vfo->output, INPUT_SAMPLE_RATE, lsfHandler, this);
  resamp.init(decoder.out, 8000, audioSampRate);          // 音频出
  reshape.init(decoder.diagOut, 480, 0);                  // 星座诊断出
  ```
- `m17/main.cpp:98-124`：`enable()` 时**重建 VFO 再 setInput**，`disable()` 时**删 VFO 停链**——VFO 生命周期绑在模块启用态上。
- `m17/main.cpp:142-147`：跨线程 UI 更新用**互斥锁 + 陈旧超时失效**（解码线程写 LSF，UI 线程读后 1 秒无更新就显示 "--"）：
  ```cpp
  auto now = std::chrono::high_resolution_clock::now();
  if (duration_cast<milliseconds>(now - _this->lastUpdated).count() > 1000)
      _this->lsf.valid = false;
  ```
- dsp block 通用契约（见 `m17dsp.h:119-134`）：每个 block 的 `run()` 都是 `_in->read() → 处理 → out.swap(n) → _in->flush()` 这套读/交换/冲刷三元组；`setInput()` 一律 `recursive_mutex + tempStop/tempStart` 热换输入。

### 1.1 M17 —— `m17_decoder/src/m17dsp.h`（最完整的「同步→解调→解码」范例）

- `m17dsp.h:642-679`：`M17Decoder : public hier_block`，在 `init()` 里**把一串 elementary block 串成流图并逐个 registerBlock**：
  ```cpp
  demod.init(input, M17_BAUDRATE, sampleRate, ...);   // GFSK 解调+时钟恢复
  doubler.init(&demod.out);                             // 分两路：比特 / 星座
  slice.init(&doubler.outA);                            // 4FSK 切片
  demux.init(&slice.out);                               // 帧同步+解复用
  lsfFEC.init(&demux.linkSetupOut, handler, ctx);       // LSF 卷积解码
  payloadFEC.init(&demux.streamOut); decodeAudio.init(&payloadFEC.out);
  ```
  机制总结：**一个协议解码器 = 一个 hier_block，公开 `out`/`diagOut` 是 stream 指针，内部组合若干 block**。
- `m17dsp.h:177-260` `M17FrameDemux::run()`：**同步字搜索状态机**——自由跑 bit 流，`memcmp` 三种同步字命中后 `detect=true; outCount=0; type=...`，然后按交织表 `M17_INTERLEAVER` + 加扰表解出整整一帧，再 `swap()` 扇出到 4 条语义分支（linkSetup / lich / stream / packet，`m17dsp.h:262-265`）：
  ```cpp
  if (!memcmp(&delay[i], M17_LSF_SYNC, M17_SYNC_SIZE)) { detect = true; outCount = 0; type = 0; }
  ```
  机制总结：**位流帧同步 = 滑动移位窗 + 同步字匹配 + 锁相后按固定帧长抽帧 + 类型扇出**。
- `m17dsp.h:479-499` `M17Codec2Decode::run()`：**接收连续性/超时状态机**——按帧号 `fn` 是否连续判断「正在接收」，断流后超过 `M17_STREAM_TIMEOUT`(500ms) 才降回 idle（`m17dsp.h:32`）：
  ```cpp
  bool consecutive = ((((int)fn - (int)lastFn + M17_END_FN) % M17_END_FN) == 1);
  else if (receiving && !consecutive && timedOut()) { receiving = false; }
  ```

### 1.2 Pager —— `pager_decoder/src/{main,pocsag/*}`（协议策略可换 + 模糊同步锁）

- `pager_decoder/src/decoder.h:4-11`：抽象 `Decoder` 基类（`setVFO/start/stop/showMenu`），模块持有 `std::unique_ptr<Decoder> decoder`（`main.cpp:148`）。
- `pager_decoder/src/main.cpp:86-114` `selectProtocol()`：运行时 `decoder.reset()` 再 `make_unique<POCSAGDecoder/FLEXDecoder>`——**同一模块内换协议实现**。
- `pocsag/dsp.h:39-45`：模拟解调链 = 正交鉴频 → FIR 低通 → Mueller-Muller 时钟恢复 → 二值切片，另出一路 `soft` 给星座图。
- `pocsag/pocsag.cpp:43-52`：**位级同步用模糊汉明距离锁**（不是精确 memcmp），更鲁棒：
  ```cpp
  syncSR = (syncSR << 1) | s;
  synced = (distance(syncSR, POCSAG_FRAME_SYNC_CODEWORD) <= POCSAG_SYNC_DIST);
  ```
- `pocsag/pocsag.cpp:61-66`：锁相后攒满一个 batch 码字 → `decodeBatch()` → 立即 `synced=false` 重新搜下一批。
- **诚实**：`pocsag.cpp:79-83` `correctCodeword()` 是 TODO 空壳（纠错没实现）；`flex/flex.cpp` 整个文件只有 `// TODO`，`flex/decoder.h` 接的是 dummy 流。**FLEX 协议未实现**。

### 1.3 VOR —— `vor_receiver/src/{vor_receiver,vor_decoder}.{h,cpp}`（模拟相位测量 + 置信度）

- `vor_receiver.h:45-77` `Receiver::process()`：**相位比较接收机**——AM 外调制包络作参考、FM 副载波解调作可变相，两路各取 30Hz 分量后「FM 取共轭 × AM → atan2」得到方位角差：
  ```cpp
  volk_32fc_conjugate_32fc(...fmv.out..., ...fmv.out..., rcount);
  volk_32fc_x2_multiply_32fc(...amv.out..., ...amv.out..., ...fmv.out..., rcount);
  volk_32fc_s32f_atan2_32f(out, ...amv.out..., 1.0f, rcount);
  ```
- `vor_decoder.cpp:32-49`：瞬时相位差经 `Reshaper` 积成一块（`round(1000*integrationTime)` 个），**块内算 mean + stddev**，`quality = 1 - stddev/归一化常数`，再 `onBearing(mean, quality)` 回调：
  ```cpp
  volk_32f_stddev_and_mean_32f_x2(&stddev, &mean, data, count);
  float quality = std::max<float>(1.0f - (stddev / STDDEV_NORM_FACTOR), 0.0f);
  ```
  机制总结：这不是帧同步协议，而是「**窄带相关测一个标量物理量，再做时间块平均 + 用离散度当置信度**」的仪器型接收机。

### 1.4 DAB —— `dab_decoder/src/dab_dsp.h`（OFDM 符号定时 + 频偏校正，半成品）

- `dab_dsp.h:58-104` `CyclicSync::run()`：**用循环前缀自相关做 OFDM 符号定时**——滑窗内 `x[i]*conj(x[i+symbolSamps])` 递推求和找相关峰，峰后按 `symbolSamps` 个样本 `swap()` 出一个 OFDM 符号，平均电平作自适应门限：
  ```cpp
  dsp::complex_t prod = val.conj()*delayBuf[i+symbolSamps];
  if (samplesSincePeak >= symbolSamps) { out.swap(symbolSamps); ... }
  ```
- `dab_dsp.h:214-253` `FrameFreqSync`：已知相位参考 × 接收符号 → FFT → 找最高功率 bin → 整数+小数频偏 → NCO rotator 控制环校正中心频偏；电平掉到平均一半以下判定帧边界重置符号计数。
- **诚实**：`dab_decoder/src/main.cpp:38,124` 还在写调试文件 `sync4.f32`，类名误抄成 `M17DecoderModule`，链末端只到星座图——**只做到同步前端，未真正解码音频/数据**。`dab_phase_sym.h`(2052 行) 就是一张 2048 点 DAB 相位参考常量表。

---

## 2. 机制总结：一个数字协议模块的通用五段式

综合四个 decoder，SDR++ 把任意数字协议组织成同一条流水线：

```
[Source]  VFO::createVFO() 自己申请窄带切片（模块自描述带宽/采样率）
   │
[同步]    位/符号/帧同步：同步字 memcmp 或模糊汉明锁（m17/pocsag）；
   │      循环前缀自相关峰（OFDM/dab）；滑动归一化互相关（行同步）
[解调]    GFSK/正交鉴频/AM 包络 → 软符号；MM/Gardner 时钟恢复
   │
[解码]    解交织/解扰 → 卷积 Viterbi / Golay / CRC 校验 → 协议帧
   │
[出口]    ① 音频流 → SinkManager  ② 元数据回调(互斥锁+陈旧超时)  ③ 星座/诊断 sink
```

两个**跨模块通用**的小设计：
- **接收健康状态机**：靠「连续帧号 + 超时」而非「有样本就叫接收中」（m17dsp.h:487-499）。
- **测量置信度**：把一串瞬时估计做时间块平均，离散度即质量（vor_decoder.cpp:38-41）。

---

## 3. MBDSDR 现状对照（读真实代码，file:line）

> 路径前缀 `cpp/src/`。MBDSDR 采用**pull 式纯 C++**（`feed(块)` → `take*()` 取队列），**不是** SDR++ 的「每 block 一线程 + 无锁 stream」图。各 decoder 都是带内部同步状态机的普通类。

| SDR++ 段 | MBDSDR 对应 | file:line |
|---|---|---|
| VFO 切片 | 多 VFO 扇出（宽带给每个 VFO 一份，独立 Channelizer+解调） | `dsp/vfo_manager.h:1-20` |
| GFSK/正交解调+MM 时钟恢复 | `FskDemod`：鉴频器→Nuttall LPF→MM TED→符号切片（**已干净室注明学自 SDR++ gfsk.h 行号**） | `dsp/fsk_demod.h:8-17,43-45` |
| Costas/载波恢复 | `DigitalDemod`：AGC→Costas NCO→分数插值→MM→差分解码，输出决策/恢复点/比特队列 | `dsp/digital_demod.h:12-21,84-89` |
| 同步字搜索状态机 | `ADSBDecoder`：前导码 `checkPreamble` 滑动搜 + PPM 采样 + `processFrame` 校验 | `dsp/adsb_decoder.h:78-85` |
| 行/帧同步锁相 | `AptDecoder`：滑动归一化互相关锁行 + 分数像素累加器抽帧，`SyncState{Search,Locked}` | `dsp/apt_decoder.h:114-132` |
| 时长/单位估计解码 | `CWDecoder`：包络→自适应门限→最短 mark 当 1 单位→间隔分词 | `dsp/cw_decoder.h:14-24,65-72` |
| 元数据出口 | `feed()→takeNewAircraft()/takeText()` 队列 pull | `adsb_decoder.h:57-58` / `cw_decoder.h:31-32` |

**对照判定**：MBDSDR 已经用自己的 pull 风格把「source(VFO)→同步→解调→解码→出口」五段式**全部实现了一遍**，且 `fsk_demod.h`、`apt_decoder.h` 的注释里已经做过干净室机制引用。SDR++ 的 thread-per-block stream 图对 MBDSDR **没有照搬价值**（MBDSDR 刻意用更简单、可单测的 pull 模型，引擎 run 线程统一驱动）。

---

## 4. 差距判定（只提通用价值高、云内可确定性验证的，不硬抄）

| 通用模式 | MBDSDR 现状 | 通用价值 | 判定 |
|---|---|---|---|
| 同步→解调→解码五段式 | ✅ ADSB/APT/CW/FSK/BPSK 各自已有 | — | **已实现**，无需学 |
| 协议策略可换（unique_ptr decoder） | 各 decoder 已是独立类由引擎选 | 低 | 现阶段不需要统一 Decoder 接口 |
| **接收连续性/超时状态机** | ⚠️ 各 decoder 自带零散锁标志（`AptDecoder.locked_`、ADSB 前导扫描），无统一「连续帧判定+超时降 idle」件 | **中高** | **可干净室抽一个通用 LinkTracker**：输入「本块是否有连续有效帧」+ 超时阈值，输出 receiving/idle，纯逻辑可单测。m17dsp.h:487-499 是机制原型 |
| **测量-置信度平均** | ⚠️ 有 `SignalWatch` 快攻慢释平滑（`signal_watch.h:34`），但无「标量估计的块平均+离散度→质量分」件 | **中** | 可复用为未来仪器类读数（锁定质量/信号质量）；纯统计、可云内验证。vor_decoder.cpp:38-41 是原型 |
| OFDM 定时/频偏同步（DAB） | ❌ 无任何 OFDM 接收计划 | 高但**条件触发** | 仅当 MBDSDR 未来做 DAB/DRM/宽带 OFDM 才值得；**本轮不落地**，先记下机制（CP 自相关 + 相位参考 FFT 频偏） |
| 跨线程 UI 陈旧失效 | MBDSDR 引擎单线程 pull，跨线程需求弱 | 低 | 暂不需要 |

### 建议（P3 只判定、不强落地）

1. **若后续要做通用数字接收健康度**：干净室写一个 `LinkTracker`（连续有效帧计数 + 可注入时钟的超时判定），替换各 decoder 里 ad-hoc 的 `locked_/receiving` 标志；输入输出纯数值，ctest 可确定性覆盖「连续→保持、断流→超时后降 idle」。机制学自 `m17dsp.h:487-499`。
2. **若未来要加仪器型读数**（如锁定质量、方位/频偏质量）：干净室加一个「滑动块 mean/stddev → 置信度」小工具，机制学自 `vor_decoder.cpp:38-41`。
3. **DAB/OFDM 暂缓**：机制已记（循环前缀自相关找符号峰 + 已知参考 FFT 找频偏），但 MBDSDR 无 OFDM 路线图，硬抄无价值。
4. 明确**不抄**：thread-per-block 流图、GPL 的具体同步字/交织表/相位参考常量表（这些是协议专有数据，不是通用架构）。

---

## 5. 未读透清单（如实）

- **逐行精读**：m17 `main.cpp`+`m17dsp.h` 全部、pager `main.cpp`+`decoder.h`+`pocsag/{decoder,dsp,pocsag}.{h,cpp}`、vor `main.cpp`+`vor_receiver.h`+`vor_decoder.{h,cpp}`、dab `main.cpp`+`dab_dsp.h`。
- **只看头部/未逐行**：`m17_decoder/src/{lsf_decode.cpp,golay24.h,crc16.h,base40.cpp}`（LSF 字段/Golay/CRC 等业务解码细节，与通用框架无关）；`vor_fm_filter.h`(2018 行，确认是 2011-tap FIR 常量表)、`dab_phase_sym.h`(2052 行，确认是 2048 点相位参考常量表)——两个大文件均为纯数据表，未逐个数。
- **未读**：`pager_decoder/src/flex/*`（4 行 TODO 空壳，已确认无逻辑）；其余 decoder（radio/atv/meteor/weather_sat/kg_sstv/falcon9/ryfi）不在 P3 指定范围。
- MBDSDR 侧对照读的是各 decoder **头文件接口与注释**；`digital_demod.cpp`/`adsb_decoder.cpp`/`apt_decoder.cpp` 的具体实现未逐行精读，仅据接口语义判定架构归属。
