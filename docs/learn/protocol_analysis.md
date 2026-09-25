# 协议分析 / 频谱测量学习笔记：URH · inspectrum · SigDigger

> 本笔记对应 MBDSDR 学习流水线的 3 个协议/频谱分析项目。
> 源码克隆在 `repos/urh`、`repos/inspectrum`、`repos/SigDigger`、`repos/sigutils`。
> 移植产物：`mbdsdr_ai/urh_adapter.py`、`inspectrum_adapter.py`、`sigdigger_adapter.py`、
> `protocol_stack.py`，以及对 `spectrum_processor.py` 的追加方法。
> 测试：`tests/protocol_spectrum_roundtrip.py`（全绿）。

---

## 1. Universal Radio Hacker (URH)

仓库：<https://github.com/jopohl/urh>

URH 的核心价值是把「一段 IQ 录制 → 可读协议报文」的过程**显式拆成三层**，
让用户在每一层都能可视化、调参、纠错：

```
IQ 样本 ──afp_demod──▶ 实值基带(QAD) ──grab_pulse_lens/digitize──▶ 比特 ──ProtocolAnalyzer──▶ 报文
  (Signal)              (正交解调)              (位/符号切片)                (协议字段)
```

### 1.1 正交解调 `afp_demod`
源码：`repos/urh/src/urh/cythonext/signal_functions.pyx:333`

| 调制 | 输出 | 公式 | 源码行 |
|------|------|------|--------|
| ASK  | 包络 | `sqrt(re^2+im^2)/max_mag` | :371-372 |
| FSK  | 瞬时频偏 | `atan2( conj(c[i-1])*c[i] )` | :374-376 |
| PSK  | Costas 环 | 二阶 PLL 鉴相 | :252 `costa_demod` |

移植：`urh_adapter.py::afp_demod()` / `costa_demod()`。

### 1.2 位/符号切片 `grab_pulse_lens`
源码：`signal_functions.pyx:392`

思路：先按阈值把每个样本归类到 `0..order-1` 个符号状态（`get_center_thresholds` :380），
再把连续同状态段聚合成 `[state, length]` 脉冲段；噪声样本（`NOISE` 常量）
被标为 `-1`（`PAUSE_STATE`，:28）。判决阈值取相邻符号中心的中点。

移植：`urh_adapter.py::grab_pulse_lens()`。在已知 `samples_per_symbol` 时，
`URHDecoder.decode()` 用符号中点 ±10% 窗采样 + 双半均值中点阈值，比脉冲长度聚合更稳健
（往返测试已验证 ASK/FSK 位流完全一致）。

### 1.3 调制器
源码：`signal_functions.pyx:81 __modulate`、`Modulator.py:215 modulate`

- ASK：`a = parameters[index]`（:147）
- FSK：`f = parameters[index]`，相位连续积分防尖刺（:122-137）
- PSK：`phi = parameters[index]`（度→弧度，:155）
- GFSK：先对频率序列做高斯 FIR（`gauss_fir` :228），再积分相位

移植：`urh_adapter.py::URHModulator`，支持 ASK/FSK/PSK/GFSK。

### 1.4 同步字检测
URH 的 awre / ProtocolAnalyzer 用比特相关找帧同步。移植：`sync_word_correlate()`
（汉明距离，可容错）。

---

## 2. inspectrum

仓库：<https://github.com/miek/inspectrum>

inspectrum 是一个「频谱游标测量」工具：它不帮你解码，而是让你用一对游标
在瀑布图/频谱上**精确测量**带宽、频率、周期、占空比。

### 2.1 坐标换算
- 时间 ↔ 样本：`time = sample / sampleRate`（`src/plotview.cpp:526-527`）
- 频率分辨率：`bwPerPixel = sampleRate / plotHeight`（`src/spectrogramplot.cpp:94`）
  即每 bin = `sample_rate / fft_size`。
- 游标区间长度：`range_t.length() = maximum - minimum`（`src/util.h`）。

移植：`inspectrum_adapter.py::InspectrumMeasurer`，含 `bin_to_hz/hz_to_bin`、
`measure_cursor_range`、`measure_bandwidth_half_power`（-3dB 半功率带宽）、
`measure_period`（脉冲到达间隔均值）、`measure_duty_cycle`。

### 2.2 测量精度
在解析已知高斯谱峰（P(f)=exp(-(f/σ)²)，-3dB 全宽 = 2σ√ln2）上测试，
测得带宽误差 2.6% < 5% 要求。

---

## 3. SigDigger + sigutils

仓库：<https://github.com/BatchDrake/SigDigger>、<https://github.com/BatchDrake/sigutils>

### 3.1 信道发现阈值（`detect.h:37-47`）
```c
SU_CHANNEL_DETECTOR_MIN_SNR_DB   = 6 dB    // 检出 SNR 门限
SU_CHANNEL_DETECTOR_MIN_BW_HZ    = 10 Hz   // 最小带宽
SU_CHANNEL_DETECTOR_ALPHA         = 1e-2    // PSD 平均系数
SU_CHANNEL_DETECTOR_BETA          = 1e-3    // spmax/spmin 跟踪系数
SU_CHANNEL_DETECTOR_GAMMA         = 0.5     // 峰值跟踪
SU_PD_THRES_SIGMAS               = 2       // 峰值检测器 σ 倍数
SU_PD_SIGNIF_DB                  = 10 dB   // 最小显著性
```
移植：`sigdigger_adapter.py::SigutilsChannelDetector`（能量检测 + SNR 门限 + 最小带宽）。

### 3.2 滑动窗峰值检测器（`detect.c:48`）
维护长度 `size` 的历史缓冲与累加器。缓冲填满后：
```
mean   = accum / size
var    = Σ(xi-mean)² / size
thresh = thres_sigmas² * var      // detect.c:83
(x-mean)² > thresh → 上峰(+1)/下峰(-1)
```
移植：`sigdigger_adapter.py::SigutilsPeakDetector`。

### 3.3 自动调制识别（AMR）
SigDigger/suscan 用瞬时特征判别调制：
- 包络变异系数小 → 恒包络（CW/FM/PSK）；大 → AM/ASK/QAM
- 频偏小 → CW；频偏大 → FM/FSK
- 频谱平坦度高 → 噪声

移植：`sigdigger_adapter.py::identify_modulation()`。

---

## 4. 跨切面：URH 式三层解码框架 `protocol_stack.py`

把 MBDSDR 现有的一堆「一把梭」解码器（adsb/aprs/ax25/acars…）统一成：

| 层 | 职责 | 对应 URH | 抽象基类 |
|----|------|----------|----------|
| BitLayer | IQ → 实值基带 → 0/1 | afp_demod + digitize | `BitLayer` |
| SymbolLayer | 比特流 → 同步字/帧切分 | ProtocolAnalyzer 帧同步 | `SymbolLayer` |
| ProtocolLayer | 帧字节 → 结构化报文 | ProtocolAnalyzer 字段解析 | `ProtocolLayer` |

### 4.1 设计要点
- **零侵入**：现有解码器文件一行不改。用 `FunctionProtocolAdapter(name, decode_fn)`
  把 `decode_frame(bytes)->dict` 包成 `ProtocolLayer`，再 `registry.register_protocol_layer()`。
- 内置 `ASKBitLayer`、`FSKBitLayer`、`FixedFrameSymbolLayer` 可直接用。
- `register_protocol_stack_tools(registry)` 会自动惰性导入并注册 ax25/aprs 适配器
  （导入失败自动跳过，不影响其它）。

### 4.2 挂一个现有解码器进来（示例）
```python
from mbdsdr_ai.protocol_stack import FunctionProtocolAdapter, GLOBAL_STACK
from mbdsdr_ai.acars_decoder import ACARSDecoder

dec = ACARSDecoder()
GLOBAL_STACK.register_protocol_layer(
    FunctionProtocolAdapter("acars", lambda frame: dec.decode(frame))
)
```

---

## 5. 跨切面：频谱峰值标注 / 测量游标

在 `spectrum_processor.py` **只追加**两个方法（不动已有接口）：

- `peak_detect(spectrum, prominence_db, distance, ...)`：局部极大值 + 突出度门限
  （默认 6dB，对齐 sigutils `MIN_SNR`）+ -3dB 半功率带宽估计。
- `measure_cursors(spectrum, left_freq_hz, right_freq_hz)`：一对水平游标之间测
  游标带宽/中心/峰频/SNR/信号实际 -3dB 带宽。

---

## 6. 交付物清单

| 文件 | 说明 |
|------|------|
| `mbdsdr_ai/urh_adapter.py` | URH 调制/afp_demod/切片/同步字 + `register_urh_tools` |
| `mbdsdr_ai/inspectrum_adapter.py` | inspectrum 游标测量引擎 + `register_inspectrum_tools` |
| `mbdsdr_ai/sigdigger_adapter.py` | sigutils 信道检测/峰值检测/AMR + `register_sigdigger_tools` |
| `mbdsdr_ai/protocol_stack.py` | 三层抽象 + 注册表 + 现有解码器适配器 + `register_protocol_stack_tools` |
| `mbdsdr_ai/spectrum_processor.py` | 追加 `peak_detect()` / `measure_cursors()` |
| `tests/protocol_spectrum_roundtrip.py` | 16 项全绿 |

## 7. 完成标准核对
- [x] 所有模块可 import
- [x] 4 个 `register_*_tools` 可调用（共注册 12 个工具）
- [x] URH ASK/FSK 符号判决往返位流一致
- [x] inspectrum -3dB 带宽测量误差 2.6% < 5%
- [x] protocol_stack 注册并调用 ax25/aprs 两个现有解码器
- [x] peak_detect 在 3-tone 合成信号上检出 3 个峰
- [x] 未修改 `tool_registry.py` / `agent.py`
- [x] 纯 numpy，未引入 PyQt/URH 运行时
