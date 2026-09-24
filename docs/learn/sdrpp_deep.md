# SDR++ 深度移植笔记（sdrpp_deep）

> 任务：把 SDR++ 的模块图 / 设备选择 UI / demod / decoder / sink 真正搬进 MBDSDR 桌面端。
> 本笔记记录：**学到了什么、移植了什么、源 SDR++ file:line、对照 MBDSDR 哪个文件**。

---

## 0. 核心结论

SDR++ 不是"一个大解调器"，而是一张**可插拔模块图**：
`Source(复数流) → IQFrontEnd(抽取/DC阻塞/分路) → VFO(信道) → Demod → Sink(音频)`。
设备选择、解调、解码、落盘全是独立 .so 模块，靠命名 stream 连起来。
MBDSDR 之前"基本没看着啥"，就是因为缺这层可视化的模块图与真实设备下拉。

---

## 1. 读到的 SDR++ 源码（真读 .cpp/.h，非 README）

| SDR++ 文件 | 学到的关键架构/常量 |
|---|---|
| `core/src/module.h:43-50` | `ModuleManager::Instance` 接口：postInit/enable/disable/isEnabled |
| `core/src/core.cpp:166-222` | 默认模块实例表（Source/Radio/Audio Sink/Network Sink…） |
| `core/src/core.cpp:41-57` | `setInputSampleRate()` → iqFrontEnd → waterfall |
| `core/src/signal_path/source.h:13-22` | SourceHandler：stream + menu/select/start/stop/tune 回调 |
| `core/src/signal_path/sink.h:17-23,25-66` | Sink/Sink.Stream：start/stop/volume/sampleRate |
| `core/src/signal_path/iq_frontend.h:66-84` | preproc 链：PowerDecimator→Conjugate→DCBlock→Splitter→VFO |
| `core/src/dsp/filter/deephasis.h:58-94` | 去加重 IIR：`alpha=dt/(tau+dt)`，`out=alpha*in+(1-alpha)*last` |
| `core/src/dsp/demod/broadcast_fm.h:43,49,52-53` | WFM：导频BP 18.75-19.25k；音频LP 15k/4k；RDS -57k→重采样5k |
| `decoder_modules/radio/src/demodulators/wfm.h:268,270,278` | WFM IF=250k，默认BW=150k，默认去加重=50μs |
| `decoder_modules/radio/src/demodulators/nfm.h:56,58,66` | NFM IF=50k，默认BW=12.5k，默认去加重=none |
| `decoder_modules/radio/src/demodulators/am.h:76,78,98-99` | AM IF=15k，BW=10k，AGC attack=50/decay=5 |
| `decoder_modules/radio/src/demodulators/usb.h:70,72,92-93` | USB IF=24k，BW=2.8k，maxBW=IF/2=12k，AGC 50/5 |
| `decoder_modules/radio/src/radio_module.h:25-28,105` | deempTaus={22,50,75}μs；音频SR=48000 |
| `decoder_modules/radio/src/rds_demod.h:25-31` | RDS：Costas<2> α=0.005，BP 0-2375，bps=1187.5，差分order=2 |
| `decoder_modules/pager_decoder/src/pocsag/dsp.h:25-29` | POCSAG：FSK偏±4.5k，10-tap矩形，MM恢复 decim=sr/baud |
| `decoder_modules/pager_decoder/src/pocsag.cpp:6,pocsag.h:7` | POCSAG sync=0x7CD215D8，每批16×32bit |
| `source_modules/soapy_source/src/main.cpp:332-355` | 启动顺序：setSR→Antenna→BW→GainMode→Gains→Freq→setupStream(CF32)→activate |
| `source_modules/soapy_source/src/main.cpp:101-115,501` | auto BW=最小≥sr；blockSize=sr/200 |

---

## 2. 移植了什么（MBDSDR 文件 ← SDR++ 来源）

| MBDSDR 新/改文件 | 内容 | 来源 |
|---|---|---|
| `mbdsdr_ai/module_graph.py`（新） | Module 基类 + ModuleGraph（register/connect/topo start/stop）+ 14 个内置模块 | module.h / source.h / sink.h / soapy main.cpp:52 |
| `desktop/module_panel.py`（新） | 左设备下拉 / 中 QPainter 信号流图 / 右参数 / 底 sink | core.cpp:138-157 菜单结构；soapy main.cpp:403 设备下拉 |
| `desktop/main_window.py`（改） | 加"模块/信号流"标签页 + `_on_module_tune` | — |
| `mbdsdr_ai/analog_demod.py`（改） | `DeemphasisFilter` + SDRPP_MODE_PARAMS + wfm 模式接入去加重 | deephasis.h:91-94；wfm/nfm/am/usb.h |
| `mbdsdr_ai/sdrpp_decoders.py`（新） | RDS/APT/ADS-B/POCSAG 壳 + ToolRegistry 注册 | rds_demod.h / weather_sat / pocsag |
| `mbdsdr_ai/sdr_backend.py`（改） | `set_antenna/list_antennas/select_bandwidth_by_samplerate/recommended_block_size` | soapy main.cpp:334,101-115,501 |

---

## 3. 关键校准值（已写进代码并验证）

- 去加重 `alpha = dt/(tau+dt)`：50μs@48k → **0.2941**，75μs@48k → **0.2174**（手算一致）。
- WFM：IF 250k / 默认带宽 150k / 默认去加重 50μs；导频 19k；音频 15k；RDS 57k。
- NFM：IF 50k / 默认 12.5k / 不去加重。
- AM：IF 15k / 10k；SSB(USB)：IF 24k / 2.8k。
- 音频链统一 48000Hz（radio_module.h:105）。
- Soapy 流格式 CF32；块大小 sr/200；auto BW = 最小 ≥ sr。

## 4. 红线遵守

- 无设备：`enumerate_all_sdr_devices()` 返回 `[]`，面板显示"未发现SDR设备"、连接按钮禁用，**不写死假设备**。
- 所有移植常量/算法都注释了 `来源: SDR++ <file>:<line>`。
- 配色用日式低饱和 `#F5F3EF / #5B7B8C / #C4845C`。

## 5. 验证

```
from mbdsdr_ai.module_graph import ModuleGraph → graph ok
14 个内置模块全部注册成功
50μs 去加重 alpha=0.2941（与公式一致）
无设备面板 → "未发现SDR设备"，不崩
```
