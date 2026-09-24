# SDRangel 真实源码移植笔记

> 仓库：`repos/sdrangel`（GPLv3, Edouard Griffiths F4EXB 等）
> 移植产物：`mbdsdr_ai/sdrangel_adapter.py`
> 往返验证：`tests/sdrangel_roundtrip.py`（18/18 通过）

本笔记记录**真读 .cpp/.h 源码**后提取的架构与常量，以及向 MBDSDR 的移植映射。
所有常量均标注 `file:line`。

---

## 1. 采样流管道：DSPDeviceSourceEngine

来源：`sdrbase/dsp/dspdevicesourceengine.cpp`

数据流（`work()` :288-337）：

```
DeviceSampleSource -> SampleSinkFifo -> DC/IQ 校正 -> 广播给所有 BasebandSampleSink
```

- 状态机（:339-341）：`notStarted -> idle -> init(ready) -> running`
- `work()`（:294）单轮最多处理 `m_sampleRate` 个样本，避免饿死消息队列。
- DC 偏移校正 `dcOffset()`（:227-237）：滑动均值 `m_iBeta/m_qBeta` 逐样本减去。
- I/Q 不平衡校正 `iqCorrections()`（:138-225）：相位（`<IQ>/<II>`）+ 幅度（sqrt(<II>/<QQ>)）两级环路。
- 多通道：`addSink()`（:105）向 `m_basebandSampleSinks` 列表追加，`work()` 里逐个 `feed()`。

移植：`sdrangel_adapter.py::DSPDeviceEngine`
- `work(iq)` 做 DC 滑动校正后分发给每个 `ChannelSink`，每个 sink 自带 `DownChannelizer`。

---

## 2. 下变频通道化：DownChannelizer

来源：`sdrbase/dsp/downchannelizer.cpp` / `.h`

- **半带滤波器阶数 `DOWNCHANNELIZER_HB_FILTER_ORDER = 48`**（downchannelizer.h:31）。
- 每级半带滤波后 2:1 抽取（feed() :61-85）。
- 通道采样率 `m_channelSampleRate = m_basebandSampleRate / 2^stages`（:136）。
- 三种半带模式：Center / LowerHalf / UpperHalf（:182-191），Lower/Upper 附带 ±fs/4 频率旋转。
- `createFilterChain()`（:230-272）递归选半带直到信道落入通带，剩余残余频偏 `ofs` 最后由 NCO 搬移（:269）。

移植：`DownChannelizer`
- 用「NCO 复数混频到通道中心 + `log2(D)` 级半带 2:1 抽取」等价实现。
- 半带系数 `_design_halfband(order=48)`：偶下标（除中心）置零、中心=0.5、Kaiser 窗。

---

## 3. FFT 滤波器：fftfilt（overlap-add 快速卷积）

来源：`sdrbase/dsp/fftfilt.cpp` / `.h`

- `flen2 = flen >> 1`（:76）——块长 = FFT 长的一半。
- 冲激响应 `fsinc(fc, i, len)`（fftfilt.h:89-94）：
  `i==len2 ? 2*fc : sin(2π fc (i-len2))/(π (i-len2))`。
- `create_filter(f1,f2)`（:144-186）：
  `h[i] = fsinc(f2) - fsinc(f1)`；带阻时 `h[flen2/2] += 1`（:165）；加 Blackman 窗（:167-169）→ FFT → 最大模归一化到单位增益（:177-185）。
- `runFilt()`（:436-457）：攒满 `flen2` 输入 → FFT → 乘 H → IFFT → `out=ovlbuf+前半`，`ovlbuf=后半`（:449-452）。
- `runSSB()`（:460-531）：USB 保留正频率 bin（`data[i]*=filter[i]`，`data[flen2+i]=0`），LSB 反之；DC 单独处理（:470）。

移植：`FFTFilter`（`filter_all` overlap-add 批处理）、`SSBFilter`（`filter_ssb` 边带选择）。

---

## 4. AGC：MagAGC

来源：`sdrbase/dsp/agc.cpp` / `.h`

- `m_stepLength = min(2400, historySize/2)`（:60）——@48kHz 最长 50ms 攻击/释放。
- `m_stepDelta = 1/m_stepLength`（:61）。
- 增益 `m_u0 = m_R / sqrt(mean(|x|²))`（:117）。
- 阈值门控 + `smootherstep` 包络（:153,167）；硬限幅输出幅度≤1（:104-111）。
- SSB 默认 `MagAGC(history=12000, target=3276, threshold=1e-2)`（ssbdemodsink.cpp:40）。

移植：`MagAGC`（滑动均值环 + smootherstep + 硬限幅）。
`dsp.py::AGC` 类已加标定常量（`SDRANGEL_HISTORY/TARGET_I16/STEP_LEN_MAX/THRESHOLD`）。

---

## 5. 通道解码器（移植 2 个）

### 5.1 NFM 窄带 FM（`NFMDemodSink`）
来源：`plugins/channelrx/demodnfm/`

| 参数 | 值 | 来源 |
|---|---|---|
| FFT_FILTER_LENGTH | 1024 | nfmdemodsink.cpp:35 |
| 默认 rfBandwidth | 12500 Hz | nfmdemodsettings.cpp:57 |
| 默认 afBandwidth | 3000 Hz | nfmdemodsettings.cpp:58 |
| 默认 fmDeviation | 5000 Hz | nfmdemodsettings.cpp:59 |
| RF 带通 | [-dev,+dev]/chSR | nfmdemodsink.cpp:297-299 |
| FM 缩放 | audioSR/fmDev | nfmdemodsink.cpp:321,390 |
| 鉴频 | 相位差分 unwrap 到[-1,1] | phasediscri.h:75-92 |
| 音频带通 | 300Hz ~ afBW | nfmdemodsink.cpp:326 |

信道间隔表（Carson 规则，nfmdemodsettings.cpp:33-44）：
5/6.25/7.5/8.33/12.5/25/40 kHz。

### 5.2 SSB 单边带（`SSBDemodSink`）
来源：`plugins/channelrx/demodssb/`

| 参数 | 值 | 来源 |
|---|---|---|
| m_ssbFftLen | 2048 | ssbdemodsink.cpp:31 |
| m_agcTarget | 3276 (-10dB) | ssbdemodsink.cpp:32 |
| 默认 Bandwidth | 5000 Hz | ssbdemodsink.cpp:52 |
| 默认 LowCutoff | 300 Hz | ssbdemodsink.cpp:53 |
| 滤波器 | f1=Low/audioSR, f2=BW/audioSR | ssbdemodsink.cpp:73,301 |
| 检波 | audio=(I+Q)*0.7 | ssbdemodsink.cpp:208 |

---

## 6. 设备源参数

| 设备 | 默认采样率 | 其他 | 来源 |
|---|---|---|---|
| RTL-SDR | 1.024 Msps | 低段 225k-300k；高段 900k-3.2M；增益0=自动 | rtlsdrinput.cpp:51-54; rtlsdrsettings.cpp:29 |
| HackRF | 2.4 Msps | — | hackrfinputsettings.cpp:45 |
| bladeRF1 | 3.072 Msps | LNA0/VGA1=20/VGA2=9/BW1.5M | bladerf1inputsettings.cpp:33-37 |

POCSAG 寻呼（demodpager）：channelSR=38400（lcm(512,2400)，pagerdemodsettings.h:118），
1200 baud FSK，BCH(31,21) 生成多项式 0x769（pagerdemodsink.cpp，x^10+x^9+x^8+x^6+x^5+x^3+1）。

---

## 7. 往返验证

`python3 tests/sdrangel_roundtrip.py` → **18/18 通过**：

1. FFTFilter：通带增益≈1，阻带抑制>20dB
2. DownChannelizer：1.024M→64k（4 级半带），输出率严格 64000
3. MagAGC：幅度阶跃后稳态收敛到 target
4. DSP 管道：源→DC校正→下变频→解调 不崩溃
5. NFM 往返：1kHz 消息 5kHz 频偏 → 恢复 999.8 Hz
6. SSB-USB 往返：2kHz 边带 → 1999.9 Hz
7. 三设备默认采样率正确

## 8. 已注册工具

- `dsp_engine_create`：创建 DSP 采样流引擎
- `fft_filter`：设计 overlap-add FFT 滤波器
- `downchannelize`：整数 2^N 下变频通道化
