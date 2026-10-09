# CTCSS 亚音静噪 DSP 核心（Phase 63）

落地文件：`cpp/src/dsp/ctcss.{h,cpp}`，接入 `SpectrumEngine` 的 NFM 解调后
48 kHz 单音音频链路。本页只描述 DSP 机制与如实参数；三通道与 UI 由并行会话
消费冻结接口。

## 1. 机制：Goertzel 单 bin 能量比检测

CTCSS（Continuous Tone-Coded Squelch System）是 67.0–254.1 Hz 的连续单音，
叠加在 NFM 解调音频之下。检测目标是回答："当前 48 kHz 单音里，目标频率
f0 处是否有一个远强于噪声底的单音？"

检测器在每个 N 采样窗口上对 f0 做一次 Goertzel 变换：

- 递归：`s[n] = x[n] + coeff·s[n-1] - s[n-2]`，`coeff = 2·cos(2πk/N)`；
- 窗口结束时 bin 能量：`power = s1² + s2² - coeff·s1·s2`；
- 同窗口总能量：`energy = Σ x[n]²`；
- 判据：`ratio = power / energy`，`ratio > 阈值` 记一次命中。

**为什么是比值而不是绝对能量**：接收机增益、AGC、信号强弱都同时缩放
`power` 和 `energy`，比值与幅度无关。纯对齐单音 `ratio ≈ N/2`（本实现
N=9600 → ~4800）；宽带噪声的能量铺满 ~N/2 个 bin，目标 bin 只分到
`O(1)`。两者之间留了几十个数量级的裕度。语音能量（300–3000 Hz）只增大
分母、几乎不进低频 bin，因此只会让判据更保守（更少误开），不会误触发。

**窗口边界必须重置递归**：Goertzel 极点在单位圆上，递归有无限记忆。若跨
窗口保留 `s1/s2`，上一窗口的 bin 输出会持续振铃，单音已经消失仍被判为
存在。实现中每个 N 窗口结束后 `s1=s2=0`，即每个窗口是独立的块 DFT。这是
本实现与"连续流式 Goertzel"描述的关键差异点，已由确定性测试（单音消失后
hangover 内必须释放）锁定。

## 2. 去抖（debounce / hangover）

- `kCtcssDetectHits = 1`：一个测量窗口命中即置位（~200 ms 开）；
- `kCtcssDetectMisses = 3`：连续 3 个窗口未命中才释放（~600 ms  hangover）。

这避免了噪声抖动造成的门限 chatter，同时把开启延迟压在人耳可接受范围。

## 3. 如实参数

| 参数 | 值 | 说明 |
|---|---|---|
| 音频采样率 | 48000 Hz | VFO resampler 输出固定 48 kHz |
| Goertzel 通带 | 5.0 Hz | N = round(48000/5) = 9600 采样/窗口 |
| 窗口时长 | 200 ms | N/48000 |
| 最低 bin 能量比 | 50.0 | 纯单音 ~4800 / 噪声 ~O(1) 之间 |
| 置位命中数 | 1 窗口 | ~200 ms |
| 释放未命中数 | 3 窗口 | ~600 ms hangover |
| 合法频率域 | 67.0–254.1 Hz | 越界请求 clamp 回域内，不编造 |
| 默认频率 | 88.5 Hz | 最常用 PL 码 |
| 默认状态 | disabled | 未使能时 `ctcssPresent()` 恒 false |

## 4. 与 SDR++ `ctcss_squelch.h` 的机制对照（干净室）

SDR++ 的 `core/src/dsp/noise_reduction/ctcss_squelch.h`（GPL）实现了同类
亚音静噪。本实现只参考其**机制概念**——"在解调音频上对目标频率做窄带
能量估计，并与总能量/噪声底比较以判断单音存在"——未抄任何代码、变量名、
阈值常量或控制流。本仓库为 MIT 许可，以下为机制层面的中立对照：

- 共同点：两者都把目标频率视为一个窄带能量峰，用"峰能量 vs 背景"的
  比值判据，并用连续命中/未命中计数做滞回，避免门限抖动。
- 差异点：本实现用固定长度 N=9600 的块 Goertzel（每窗口独立 DFT），
  阈值取 bin 能量占总能量的比值 50.0；SDR++ 参考实现的具体窗口长度、
  阈值与状态机细节未沿用。
- 许可立场：本目录所有代码为独立撰写，不包含 GPL 来源文件的任何片段；
  机制对照仅用于设计说明，不构成代码引用。

## 5. 常用 CTCSS 频率表（节选，67.0–254.1 Hz）

标准 PL 单音序列（部分常用值）：

```
67.0  69.3  71.9  74.4  77.0  79.7  82.5  85.4  88.5  91.5
94.8  97.4 100.0 103.5 107.2 110.9 114.8 118.8 123.0 127.3
131.8 136.5 141.3 146.2 151.4 156.7 162.2 167.9 173.8 179.9
186.2 192.8 203.5 210.7 218.1 225.7 233.6 241.8 250.3 254.1
```

频率合法性由 `tokens::kCtcssToneHzMin=67.0` / `kCtcssToneHzMax=254.1` 约束；
引擎 `setCtcssFreqHz()` 对越界请求 clamp 而非编造一个频率。

## 6. 冻结接口（供三通道/UI 消费）

`CtcssToneDetector`（`dsp/ctcss.h`）：

- `configure(double sampleRateHz, double toneHz)`
- `reset()`
- `setEnabled(bool)` / `enabled()`
- `process(const float* audio, int n)` → 返回处理后检测状态
- `tonePresent() const`
- `toneHz()` / `binBandwidthHz()`

`SpectrumEngine` 对外：

- `setCtcssEnabled(bool)` / `setCtcssFreqHz(double)`（clamp）
- `ctcssEnabled()` / `ctcssFreqHz()` / `ctcssPresent()`（诚实空态）

检测器在引擎线程消费"选中 VFO 的 post-ANR 48 kHz 单音"；desired 状态
（enabled/freq）可从任意线程写入，run 循环每个块在引擎线程应用到检测器，
避免 Goertzel 状态被 UI 线程竞态。

## 7. 未实现 / 诚实边界

- **CDCSS/DCS**（134.4 bps 3-of-8 数字码）未实现；本周期只落地模拟 CTCSS。
- 检测器目前只挂"选中 VFO"的音频；未选中 armed 通道的亚音不在本周期范围。
- 未做亚音触发的自动静噪门控（只做存在性检测读回）；门控联动由后续会话决定。
