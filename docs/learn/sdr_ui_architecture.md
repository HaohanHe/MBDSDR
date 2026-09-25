# SDR UI / 接收机架构学习笔记（4 项目边学边做）

> 学习目标：clone → 真读源码 → 当场把核心能力移植到 Python(numpy)。
> 所有移植见 `mbdsdr_ai/{sdrsharp,openwebrx,cubicsdr,hdsdr}_adapter.py`，
> 往返验证见 `tests/sdr_ui_roundtrip.py`（20 用例全绿）。

---

## 0. 总览：四种 SDR UI 范式对比

| 项目 | 形态 | 前端抽象 | 频谱/waterfall | 调谐交互 | 多 VFO |
|---|---|---|---|---|---|
| **SDR#** | WinForms 桌面 C# | IFrontendController.Open/Close + 插件 DLL | FFT 窗→功率谱→字节缩放 | 数字频率输入 | 单 VFO |
| **OpenWebRX** | Python web 服务器 | SdrSource ABC + SoapySDR | FFT 链+平均+ADPCM→WebSocket | 浏览器点选 | 单 VFO/会话 |
| **CubicSDR** | wxWidgets C++ 桌面 | SoapySDR + SessionMgr | GL waterfall + JET/turbo | 数字拖拽(drag-tune) | **多 VFO** |
| **HDSDR** | Win32 闭源 | ExtIO DLL | 传统频谱+waterfall | 旋钮/键盘 | 单 VFO |

共同数据流：
```
射频前端(IQ) → 变频/抽取 → FFT窗 → FFT → |X|²(dB) → 映射色彩/字节 → 频谱/waterfall
                          ↓
                     VFO 信道选择 → 解调 → 音频
```

---

## 1. SDR# (SDRSharp, airspy) — 插件平台架构

**仓库**：`repos/sdrsharp`（SDRSharpR/SDRSharp Build 1632）

### 1.1 插件接口契约（核心设计）

SDR# 把"前端硬件"和"功能插件"都做成可插拔 DLL，主程序只持一个 `ISharpControl` 句柄：

```
┌──────────────────────── MainForm ────────────────────────┐
│  iqSourceComboBox (Source 下拉)                           │
│     ├─ IFrontendController.Open()/Close()  ← 前端硬件      │
│     └─ ISharpPlugin.Initialize(ISharpControl) ← 功能插件  │
│              ▲                                            │
│              └─ 插件只拿 ISharpControl 属性句柄，不碰硬件  │
└───────────────────────────────────────────────────────────┘
```

| 接口 | 成员 | 源文件:行号 |
|---|---|---|
| `ISharpPlugin` | `Gui`, `DisplayName`, `Initialize(ISharpControl)`, `Close()` | `SDRSharp.Common/SDRSharp.Common/ISharpPlugin.cs:5-20` |
| `IFrontendController` | `Open()`, `Close()` | `SDRSharp.Radio/SDRSharp.Radio/IFrontendController.cs:3-8` |
| `ISharpControl` | `CenterFrequency`, `Frequency`, `FilterBandwidth`, `AudioGain`, `SquelchThreshold`, `FmStereO`… | `SDRSharp.Common/SDRSharp.Common/ISharpControl.cs:10-111` |

### 1.2 设备枚举表

`MainForm.cs:3258-3272` 硬编码 `LoadSource(name, controller, access)` 序列，末尾 `3282-3283` 追加 `IQ File (*.wav)` 与 `IQ from Sound Card`。`access=INT_MAX` 静态链接，`10` 可选 ExtIO，`0` 第三方 Plugins.xml。
移植：`SDRSHARP_DEVICE_TABLE`（17 项）。

### 1.3 FFT 窗函数系数

`FilterBuilder.MakeWindow` (`FilterBuilder.cs:9-79`)，窗系数：

| 窗 | a0 | a1 | a2 | a3 | 源行号 |
|---|---|---|---|---|---|
| Hamming | 0.54 | 0.46 | – | – | :20-24 |
| Blackman | 0.42 | 0.50 | 0.08 | – | :29-33 |
| BH4 | 0.35875 | 0.48829 | 0.14128 | 0.01168 | :38-42 |
| BH7 | 0.2710514… | …7 项 | | | :47-54 |
| HannPoisson | 0.5·(1+cos)·exp(-0.01·\|v\|/L) | | | | :59-61 |
| Youssef | BH4 外形 × HannPoisson 指数 | | | | :66-73 |

功率谱/字节缩放：`Fourier.SpectrumPower` = `10log10(1e-60+\|X\|²)` (`Fourier.cs:44-51`)；
`ScaleFFT` 钳位 [min,max] 映射 0..255 (`Fourier.cs:53-69`)。
默认窗下标=3 → BH4 (`MainForm.cs:3237`)。

### 移植对照
| SDR# 源码 | mbdsdr_ai |
|---|---|
| `ISharpPlugin.cs:5-20` | `SdrSharpControl` / `SdrSharpFrontendController` |
| `MainForm.cs:3258-3283` | `SDRSHARP_DEVICE_TABLE` |
| `FilterBuilder.cs:9-79` | `make_sdrsharp_window()` |
| `Fourier.cs:44-69` | `sdrsharp_spectrum_power()` / `sdrsharp_scale_fft()` |

---

## 2. OpenWebRX — web SDR 服务器架构

**仓库**：`repos/openwebrx`（jketterl/openwebrx）

### 2.1 频谱流水线

```
SdrSource(复IQ buffer) → SpectrumThread
   → FftChain:  Fft(size) → FftAverager → FftSwap → [FftAdpcm] → 推 WebSocket
```
来源：`owrx/fft.py:40-46`，`csdr/chain/fft.py:25-49`。

关键公式（`csdr/chain/fft.py:75-85`）：
```
fft_averages = round( samp_rate / fft_size / fft_fps / (1 - v_overlap_factor) )
block_size   = samp_rate / fft_fps / fft_averages     (averages>0)
             = samp_rate / fft_fps                     (averages==0 → LogPower)
add_db = -70   (fft.py:20-22)
```
averages==0 用 `LogPower`，否则 `LogAveragePower`。移植：`owx_plan_fft()` / `OwxFftAverager`。

### 2.2 waterfall 色彩映射

`owrx/waterfall.py:298-302` 四种主题：
- **Google Turbo**（默认，256 项长表 `:13-275`）
- **Teejeez**：黑→蓝→青→绿→黄→红→品红→白（8 停 `:279`）
- **HA7ILM**：8 停 `:284`
- **Custom**：用户配置 `:287-295`

移植：`owx_waterfall_colormap()`（线性插值 stops → n×3 RGB）。

### 2.3 设备抽象 + 客户端带宽自适应

`owrx/source/__init__.py` `SdrSource` ABC；每设备实现 `getSampleRateRanges()`。
RTL-SDR：`owrx/source/rtl_sdr.py` → `Range(250000, 3200000)`。
半带宽 = `samp_rate/2`（`owrx/connection.py:202`）。
Soapy 配置串解析：`owrx/soapy.py` `SoapySettings.parse`。
移植：`owx_client_bandwidth_adapt()` / `owx_soapy_settings_parse()`。

---

## 3. CubicSDR — 多 VFO / 视觉调谐

**仓库**：`repos/CubicSDR`（cjcliffe/CubicSDR）

### 3.1 多 VFO 状态机

`src/demod/DemodulatorMgr.h:14-92`：
```
DemodulatorMgr
  ├─ vector<DemodulatorInstancePtr> demods   (每个=一个 VFO)
  ├─ DemodulatorInstancePtr activeContextModem
  ├─ DemodulatorInstancePtr currentModem     ← 正在调谐的 VFO
  └─ DemodulatorInstancePtr activeVisualDemodulator
```
关键方法：`addThread/newThread`(:19)、`getDemodulatorsAt(freq,bw)`(:25)、
`setActiveDemodulator`(:36)、`getNext/PreviousDemodulator`(:27-28)。
移植：`CubicVFOManager` + `CubicVFO`。

### 3.2 waterfall 调色板

`src/visual/ColorTheme.h:14-22` 8 主题：DEFAULT/JET/BW/SHARP/RAD/TOUCH/HD/RADAR。
JET 色标（`ColorTheme.cpp:76-80`）：黑→蓝→绿→黄→橙红(1,0.2,0)。
状态色（`:51-53`）：new=绿(0,1,0)、hover=黄(1,1,0)、destroy=红(1,0,0)。
移植：`cubic_waterfall_colormap()`。

### 3.3 视觉调谐 drag-tune 参数模型

`src/visual/TuningCanvas.cpp`：
- 拖拽累积（`:275`）：`dragAccum += 5.0*deltaMouseX`，每满 ±1.0 → `StepTuner` 一次。
- 步进量（`:174`）：`amount = ±10^digit`。
- PPM 钳位 ±2000（`:259-265`）。
- 带宽钳位 `CHANNELIZER_RATE_MAX=500000`（`CubicSDRDefs.h:63`，`TuningCanvas.cpp:224`）。
- 数字位数：freq=11、bw=7、center=11（`:302,309,317`）。
- VFO 超出采样率半带 → 平移中心频率（`:197-199`）。
移植：`cubic_step_tuner()` / `cubic_drag_to_steps()` / `cubic_clamp_bw()` / `cubic_clamp_ppm()`。

---

## 4. HDSDR — 传统 SDR UI / RF 前端

**仓库**：`repos/hdsdr/NOTES.md`（闭源免费软件，无公开 git；参数来自公开手册与 ExtIO 接口）

### 4.1 RF 前端三级增益（LNA / Mixer / VGA）

HDSDR 通过 ExtIO DLL 控制前端三级增益（UI 上 "RF" / "IF" 滑块）。
经典 R820T 调谐器档位（移植：`HDSDR_LNA/MIXER/VGA_GAIN_DB`，各 16 档）：
- LNA：0.0 … 23.1 dB
- Mixer：-4.0 … 7.5 dB
- VGA/IF：0 … 24 dB
总增益 = 三级求和；越界档位钳位 0..15。移植：`hdsdr_total_gain()` / `hdsdr_gain_clamp_stage()`。

### 4.2 解调模式 / 带宽预设

模式：AM / FM(NFM) / WFM / USB / LSB / CW（`hdsdr.de/wnew.html`）。
带宽预设（F6 滑块常用档）：
| 模式 | 带宽 |
|---|---|
| CW | 500 Hz |
| USB/LSB | 2400 Hz |
| AM | 6000 Hz |
| NFM | 12500 Hz |
| WFM | 120000 Hz |

移植：`HDSDR_BANDWIDTH_PRESETS` / `hdsdr_bandwidth_for_mode()`。

---

## 5. 移植对照表（一图汇总）

| 能力 | 源项目 | 源文件:行号 | mbdsdr_ai 实现 |
|---|---|---|---|
| 插件接口契约 | SDR# | `ISharpPlugin.cs:5-20`, `IFrontendController.cs:3-8` | `sdrsharp_adapter.SdrSharpControl/Frontend` |
| 设备枚举表 | SDR# | `MainForm.cs:3258-3283` | `SDRSHARP_DEVICE_TABLE` |
| FFT 窗系数 | SDR# | `FilterBuilder.cs:9-79` | `make_sdrsharp_window()` |
| 功率谱/字节缩放 | SDR# | `Fourier.cs:44-69` | `sdrsharp_spectrum_power/scale_fft` |
| FFT 平均推导 | OpenWebRX | `csdr/chain/fft.py:75-85` | `owx_plan_fft()` |
| waterfall 调色板 | OpenWebRX | `waterfall.py:277-302` | `owx_waterfall_colormap()` |
| 设备带宽钳位 | OpenWebRX | `rtl_sdr.py`, `connection.py:202` | `owx_client_bandwidth_adapt()` |
| 多 VFO 状态机 | CubicSDR | `DemodulatorMgr.h:14-92` | `CubicVFOManager` |
| waterfall 主题 | CubicSDR | `ColorTheme.cpp:76-111` | `cubic_waterfall_colormap()` |
| drag-tune 步进 | CubicSDR | `TuningCanvas.cpp:173-285` | `cubic_step_tuner/drag_to_steps` |
| RF 三级增益 | HDSDR | ExtIO/R820T 手册 | `hdsdr_total_gain()` |
| 带宽预设 | HDSDR | hdsdr.de 手册 | `hdsdr_bandwidth_for_mode()` |

## 6. 工具注册一览（每 adapter ≥2 个，共 14 个）
- sdrsharp：`sdrsharp_list_devices` / `sdrsharp_make_window` / `sdrsharp_spectrum_fft` / `sdrsharp_plugin_contract`
- openwebrx：`owrx_plan_fft` / `owrx_waterfall_colormap` / `owrx_fft_average` / `owrx_device_bandwidth_adapt`
- cubicsdr：`cubicsdr_waterfall_colormap` / `cubicsdr_vfo_state_machine` / `cubicsdr_drag_tune`
- hdsdr：`hdsdr_rf_gain` / `hdsdr_bandwidth_preset` / `hdsdr_gain_table`

测试：`python3 tests/sdr_ui_roundtrip.py` → 20 passed。
