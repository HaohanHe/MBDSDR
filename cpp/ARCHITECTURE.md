# MBDSDR C++ — 统一架构说明（ARCHITECTURE）

## 为什么是统一架构

旧 Python 版本把 UI 层和信号引擎层拆成两层、靠 Qt 信号胶水连接，
导致"面板能亮但电机不转"：UI 有自己的占位状态机，引擎数据通路要后期才接。

C++ 版本从第一天起就避免这个反模式：

1. **同进程**：UI 线程和 DSP 引擎线程在同一个可执行文件里，不跨进程、不 IPC。
2. **同数据模型**：引擎产出、UI 消费，用的是**同一个 `SpectrumFrame` 结构体**，
   没有中间表、没有代理模型、没有"UI 先缓存一份假的"。
3. **引擎线程 → UI 信号槽直连**：引擎在 `QThread::run()` 里算完一帧就
   `emit spectrumReady(frame)`；UI 线程用 `Qt::QueuedConnection` 接到
   `SpectrumWidget::setSpectrum(frame)` 直接 `update()`。跨线程安全、无锁共享。
4. **数据源可替换**：引擎内部的数据源（Phase 1 = `TestSignalGenerator`）
   是引擎的私有成员。Phase 2 把它换成 librtlsdr / SoapySDR 抓取循环时，
   `spectrumReady` 的签名不变，UI 一行都不用改。

## 数据流（Phase 1）

```
┌────────────────────────────── QThread (SpectrumEngine::run) ──────────────────────────────┐
│                                                                                          │
│  TestSignalGenerator.next(iq, N)                                                         │
│        │  (TEST SIGNAL: 4 tones ±200k/±500k + Gaussian noise, fs=2.4MHz, f0=98.5MHz)     │
│        ▼                                                                                 │
│  powerSpectrumDbfs(iq, dbfs)   Hann window + radix-2 FFT + fftshift + 20*log10           │
│        │                                                                                 │
│        ▼                                                                                 │
│  SpectrumFrame { dbfs[], centerFreqHz, sampleRateHz, fftSize, isTestSignal=true }        │
│        │                                                                                 │
│        └── emit spectrumReady(frame) ──┐                                                 │
└─────────────────────────────────────────┼─────────────────────────────────────────────────┘
                                          │  Qt::QueuedConnection (跨线程, 自动拷贝)
                                          ▼
┌────────────────────────────── GUI Thread ────────────────────────────────────────────────┐
│                                                                                          │
│  SpectrumWidget::setSpectrum(frame)  ── 拷贝 frame 到成员 ── 调 update()                │
│        │                                                                                 │
│        ▼                                                                                 │
│  paintEvent()  ── QPainter 画网格/频率轴/dBFS 轴/accent 谱线/TEST SIGNAL 水印           │
│                                                                                          │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 线程模型

| 线程 | 职责 |
|---|---|
| GUI 线程 (main) | 所有 QWidget 绘制、事件、布局；只调用 `setSpectrum` 拷贝一帧 |
| SpectrumEngine thread | 取 IQ → FFT → 组 frame → emit。约 30fps，33ms pacing |

引擎和 UI 之间**不共享可变状态**：frame 按值传递（QMetaType 自动注册），
GUI 拿到的是自己的副本。FFT size 由 UI 通过 `fftSizeRequested(int)` 信号回传，
引擎用 `std::atomic<int>` 读取，下帧生效。

## 目录与职责

```
src/
├── core/
│   ├── tokens.h            # 设计 tokens（颜色/圆角/尺寸/比例）+ 深色 QSS 字符串
│   └── spectrum_frame.h    # 统一频谱数据结构（引擎→UI 唯一交换类型）
├── dsp/
│   ├── fft.{h,cpp}          # 自包含 radix-2 Cooley-Tukey，in-place，无 FFTW
│   ├── iq_buffer.{h,cpp}   # 环形 IQ 样本缓冲（后续硬件接入用）
│   ├── power_spectrum.{h,cpp}  # Hann 窗 + FFT + fftshift + dBFS
│   ├── test_signal.{h,cpp} # 离线测试信号（明确标注 NOT HARDWARE）
│   └── spectrum_engine.{h,cpp}  # QThread 引擎，封装上面四个 DSP 模块
└── ui/
    ├── main_window.{h,cpp}  # 三栏主壳 + 顶栏/底 Dock + 引擎生命周期
    └── spectrum_widget.{h,cpp}  # 纯视图：setSpectrum 槽 + QPainter
```

## 演进路线（新 C++ 应用的阶段规划，不是"从 Python 迁移"）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **Phase 1（当前）** | 工程骨架 + Qt6 主壳 + 引擎线程→UI 直连 + FFT 频谱（测试数据） | 编译出 `mbdsdr`，offscreen 启动不崩，频谱有真曲线 |
| Phase 2 | 硬件接入层：librtlsdr / SoapySDR 抽象接口；把引擎里的 TestSignalGenerator 换成真 IQ 流 | 接 RTL-SDR 看到真实空中信号，UI 零改动 |
| Phase 3 | 解调引擎：AM / FM / SSB、静噪、AGC；新增 `DemodFrame` 结构 + 音频输出 | 听到 FM 广播 |
| Phase 4 | 多 VFO、录制/回放、基带文件 I/O（.wav / .cs8 / .cs16） | 录一段再回放一致 |
| Phase 5 | 数字解码面板：ADS-B、APRS、SSTV 等，C++ 实现 | 对应解码可见 |
| Phase 6 | AI Agent 层：C++ 直接调 LLM API / MCP 客户端，替代 Python `mbdsdr_ai/` | 在右侧"任务/AI"面板里对话 |
| Phase 7 | 打包：Linux AppImage / Windows 单文件 / macOS .app | 目标机器上双击即用 |

## 开源 C++ SDR 生态对接建议

- **librtlsdr** — RTL-SDR 设备驱动（Phase 2 首选硬件源）
- **SoapySDR** — 多设备统一抽象（Phase 2 备选，覆盖 BladeRF / HackRF / USRP）
- **FFTW** — 可选高性能 FFT，替换自实现 radix-2（当前自实现已够 Phase 1 用）
- **参考架构**：
  - [SDR++](https://github.com/AlexandreRouma/SDRPlusPlus) 的 DSP 模块组织
  - [sigutils](https://github.com/BatchDrake/sigutils) 的信号处理原语
  - [SatDump](https://github.com/SatDump/SatDump) 的卫星解码架构

## 旧 Python 模块 → C++ 参考映射（仅作算法参考，不是迁移目标）

| Python 文件 | C++ 对应 | 说明 |
|---|---|---|
| `desktop/tokens.py` | `src/core/tokens.h` | 颜色/圆角/尺寸常量 + QSS |
| `desktop/spectrum_widget.py` | `src/ui/spectrum_widget.cpp` + `src/dsp/power_spectrum.cpp` | FFT 参数（Hann 窗、dBFS 归一化）对齐 |
| `desktop/receive_pipeline.py` | `src/dsp/spectrum_engine.cpp` | 数据流组织，但 C++ 用 QThread 直连 |
| `desktop/main_window.py` | `src/ui/main_window.cpp` | 三栏布局比例 0.19:0.62:0.19 |
| `mbdsdr_ai/` 内核 | Phase 6 才在 `src/ai/` 实现 | 当前不动 |

## 红线

- 不 embed Python、不 subprocess 调 Python。
- 测试数据必须在 UI 上明确标注 "TEST SIGNAL - NOT HARDWARE"。
- 不造假硬件值（不伪造设备名、频率、信号强度）。
- 不修改 `desktop/`、`mbdsdr_ai/`、`tests/` 下任何 Python 文件。
