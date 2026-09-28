# MBDSDR C++ — Native SDR Desktop Application

原生 C++ SDR 接收软件。UI 与 DSP 引擎同进程、同数据模型：QThread 引擎 → Qt 信号槽 → QPainter 直连，**不依赖 Python 运行时**。

## 架构

```
SpectrumEngine (QThread)
  TestSignalSource / RtlSdrSource / FileSource (ISource)
    → IQFrontend (DC blocker + IQ balance)
    → PowerSpectrum (Hann + FFT + fftshift + dBFS)  → spectrumReady signal
    → Demod (AM/NFM/WFM/SSB/CW) → Squelch → AGC → QAudioSink
    → CWDecoder / ADSBDecoder → decoded signals
SpectrumWidget::setSpectrum(frame) → QPainter（峰值检测 + 跨帧跟踪）
```

## 依赖

- Qt 6.8（Widgets / Multimedia / Network / Concurrent / Test）
- librtlsdr 2.x（找不到时编译为 stub，自动用测试信号）
- CMake 3.16+、C++17 编译器

Ubuntu 22.04 示例：
```bash
sudo apt-get install -y qt6-base-dev qt6-base-dev-tools qt6-multimedia-dev \
    librtlsdr-dev cmake build-essential
```

## 构建与运行

```bash
cd cpp
cmake -S . -B build
cmake --build build -j$(nproc)
./build/mbdsdr
```

没有 RTL-SDR 设备时自动切到测试信号（界面标注 `Test Signal — NOT HARDWARE`）。

## 测试

```bash
LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib:/home/user/.local/lib \
QT_QPA_PLATFORM=offscreen ctest --test-dir build --output-on-failure
```

8 个测试套件：fft / peak_detector / channelizer / chain / demod / recorder / decoder / agent。

## 目录结构

```
cpp/src/core/   tokens（颜色/尺寸/间距常量）、共享数据结构
cpp/src/dsp/    引擎、解调、录制、解码、TLE 过境预测、峰值检测
cpp/src/ui/     主窗口、频谱、瀑布、SkyView、世界地图、设置、关于
cpp/src/ai/     LLM / Agent 层
cpp/tests/      Qt Test 单元测试
```

## AI 配置

- 配置文件：`~/.config/MBDSDR/ai_config.json`（字段 `api_key` / `base_url` / `model`）
- 环境变量 `MBDSDR_API_KEY` 优先于配置文件
- 无 key 时走本地指令（频率 / 模式 / 录制）

## 已知限制

- 过境轨道传播是 J2 摄动近似（非完整 SGP4），LEO 过境时间误差在分钟级
- FC0012 调谐器收不到 1090 MHz，ADS-B 解码靠文件回放
- 云端 / 无音频设备环境自动禁用音频输出
- librtlsdr 缺失时编译 stub 版本，RTL 高级选项（直采 / Bias-T / PPM）优雅禁用
