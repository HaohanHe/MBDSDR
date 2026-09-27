# MBDSDR C++ — 原生 SDR 桌面应用（Phase 1）

这是一个**全新的原生 C++ 应用**，不是 Python 版本的移植。
旧 Python 工程（`desktop/`、`mbdsdr_ai/`）仅作算法与参数参考，**运行时不依赖任何 Python 运行时**。

## 架构定位

UI 与 DSP 引擎**同进程、同数据模型**：

```
SpectrumEngine (QThread)
   │  TestSignalGenerator → PowerSpectrum (Hann+FFT+fftshift+dBFS)
   ▼  spectrumReady(SpectrumFrame)  [Qt 信号槽, 跨线程 queued]
SpectrumWidget::setSpectrum(frame) → QPainter 绘制
```

没有两层胶水、没有中间数据模型、没有"先摆假数据以后再接"。
这条闭环从 Phase 1 就是真的：测试信号 → C++ FFT → 频谱控件实时绘制。

> **当前数据源是离线测试信号（TEST SIGNAL），不是硬件实时数据。**
> UI 顶栏、频谱角落水印、状态栏均有明确标注。

## 依赖

- CMake ≥ 3.16
- C++17 编译器（g++ 11+）
- Qt6 Base（Core / Gui / Widgets），Ubuntu 22.04 可 `apt install qt6-base-dev qt6-base-dev-tools`

## 构建

```bash
cd cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

产物：单个可执行文件 `build/mbdsdr`（动态链接系统 Qt6，因为系统 Qt6 本身是动态库）。

## 运行

```bash
./build/mbdsdr                              # 启动 GUI
./build/mbdsdr --help                       # 打印帮助
QT_QPA_PLATFORM=offscreen ./build/mbdsdr    # 无显示环境验证
```

## 当前状态（Phase 1）

- CMake + Qt6 Widgets 工程骨架
- 三栏平行视界主壳（顶栏 56px / 中 Splitter / 底 Dock 64px）
- 原生 C++ radix-2 FFT（不依赖 FFTW）
- 功率谱：Hann 窗 + FFT + fftshift + dBFS
- 测试信号引擎线程 → Qt 信号槽 → 频谱 QPainter 实时绘制（30fps）
- FFT size 切换：1024 / 2048 / 4096
- 设计 tokens 从 `desktop/tokens.py` 1:1 翻译为 C++ 常量 + 深色 QSS

**未接硬件。** Phase 2 将把 `TestSignalGenerator` 替换为 librtlsdr / SoapySDR 源，UI 侧无需改动。

## 目录

```
cpp/
├── CMakeLists.txt
├── README.md
├── ARCHITECTURE.md
└── src/
    ├── main.cpp
    ├── core/
    │   ├── tokens.h            # 设计 tokens + QSS 生成
    │   └── spectrum_frame.h     # 统一频谱数据结构
    ├── dsp/
    │   ├── fft.{h,cpp}          # radix-2 Cooley-Tukey FFT
    │   ├── iq_buffer.{h,cpp}   # 环形 IQ 缓冲
    │   ├── power_spectrum.{h,cpp}
    │   ├── test_signal.{h,cpp} # 离线测试信号（非硬件）
    │   └── spectrum_engine.{h,cpp}  # QThread 引擎
    └── ui/
        ├── main_window.{h,cpp}
        └── spectrum_widget.{h,cpp}
```
