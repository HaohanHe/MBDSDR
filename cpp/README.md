# MBDSDR C++ — Native SDR Desktop Application

统一架构原生 C++ SDR 接收应用。UI 与 DSP 引擎同进程、同数据模型，QThread 引擎 → Qt 信号槽 → QPainter 直连，**不依赖 Python 运行时**。

## 架构

```
SpectrumEngine (QThread)
  TestSignalSource / RtlSdrSource / FileSource (ISource)
    → IQFrontend (DC blocker + IQ balance)
    → PowerSpectrum (Hann+FFT+fftshift+dBFS)  → spectrumReady signal
    → Demod (AM/NFM/WFM/SSB/CW) → Squelch → AGC → QAudioSink
    → CWDecoder / ADSBDecoder → decoded signals
SpectrumWidget::setSpectrum(frame) → QPainter
```

## 依赖

Ubuntu 22.04:
```bash
sudo apt-get install -y qt6-base-dev qt6-base-dev-tools qt6-multimedia-dev librtlsdr-dev cmake build-essential
```

## 构建

```bash
cd cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
./build/mbdsdr
```

## 测试

```bash
ctest --test-dir build --output-on-failure
```

5 个测试套件：fft / demod / recorder / decoder / agent。

## AI 配置

- 配置文件：`~/.config/MBDSDR/ai_config.json`
- 字段：`api_key`, `base_url`（默认 `https://api.siliconflow.cn/v1`）, `model`
- 环境变量 `MBDSDR_API_KEY` 优先级高于配置文件
- 无 key 时走本地指令（频率 / 模式 / 录制）

## 面板

- **左面板**：频率 / 采样率 / 增益控制、解调模式、带宽、录制
- **中面板**：实时频谱（FFT 热路径，QPainter 绘制）
- **右面板 QTabWidget**：任务 / AI 助手对话 / CW 解码 / ADS-B 飞机列表
- **底坞**：Home / 温控 / 应用坞 / NowPlaying / 音量

## 当前状态

Phase 1-6 完成：工程骨架 + RTL-SDR 接入 + 解调链（AM/NFM/WFM/SSB/CW）+ 静噪 + AGC + IQ 前端校正 + SigMF 录制/回放 + 触发式 WAV 分段 + CW 摩尔斯解码 + ADS-B 1090 解码 + AI Agent 层。

无硬件时自动降级为测试信号（TEST SIGNAL — NOT HARDWARE）。
