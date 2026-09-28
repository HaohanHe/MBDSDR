# MBDSDR C++ — Native SDR Desktop Application

原生 C++ SDR 接收软件。UI 与 DSP 引擎同进程、同数据模型：QThread 引擎 → Qt 信号槽 → QPainter 直连，**不依赖 Python 运行时**。

## 架构

```
SpectrumEngine (QThread)
  TestSignalSource / RtlSdrSource / RtlTcpSource / FileSource (ISource)
    → IQFrontend (DC blocker + IQ balance + NoiseBlanker)
    → PowerSpectrum (Hann/Flattop/Blackman + FFT + 帧平均) → spectrumReady
    → Channelizer → Demod (AM/NFM/WFM/USB/LSB/CW) → Squelch → AGC → QAudioSink
    → CWDecoder / ADSBDecoder → decoded signals
    → Recorder (基带 IQ / 解调音频 WAV)
SpectrumWidget (拖拽调谐 / 滚轮光标锚定 zoom / VFO 带宽框 / 峰值检测 / max-hold)
Waterfall (滚动瀑布 / 双调色板 / 速度控制)
SkyView (极坐标天空 / 多卫星实时跟踪 / 标签避让)
WorldView (等距圆柱投影地图 / 飞机+卫星+本站 / 拖拽平移 / 滚轮缩放)
```

## 功能清单

### 接收与解调
- 6 种解调模式：AM / NFM / WFM / USB / LSB / CW，模式切换自动带宽预设
- 静噪（Off / AlwaysOpen / Gate 三模式，门限可调）
- AGC（载波 AGC，attack/decay 参数）
- NoiseBlanker（滑动窗 3.5σ 脉冲抑制，采样率自适应窗口）
- 音频输出设备枚举+热切换，主音量控制

### 频谱与瀑布
- 实时频谱 + 瀑布图，FFT 大小可调（512–8192）
- 窗函数：Hann / Flattop / Blackman；帧平均：Off / Slow / Fast
- max-hold 包络线（可开关+复位）
- 峰值检测（局部极大+中位门限+NMS 去重+3dB 带宽+绝对 dBFS 下限）
- 跨帧峰跟踪（稳定 ID，持续 3 帧才显示）
- VFO 带宽框（左右拖柄改带宽，snap 到预设）
- 拖拽调谐 / 滚轮光标锚定 zoom / 右键复位 / 十字线读数
- 瀑布速度（1x/2x/4x）+ 双调色板（经典/单色）

### 硬件支持
- RTL-SDR（librtlsdr 2.x，全选项：直采/偏移/RTL AGC/Tuner AGC/Bias-T/PPM）
- rtl_tcp 远程源（host:port，连不上诚实报错不降级假数据）
- 测试信号源（无硬件时自动切换，界面标注"测试信号（非硬件）"）
- FileSource 回放（SigMF / 录制文件）

### 录制
- 基带 IQ 录制 / 解调音频 WAV
- 文件名模板 / 立体声 / 忽略静噪 / 分段录制
- 录制中状态栏红点+REC 计时，按钮红色高亮

### 书签与扫描
- 频率书签（QSettings 持久化，备注，双击直跳）
- 频段扫描（起止频率+步进，结果列表双击直跳）

### CW 解码
- 莫尔斯电文解码，WPM 显示，只读文本区+清空

### ADS-B 解码
- DF17 呼号/高度解码
- DF17 airborne position CPR 解码（全局 even/odd 对 + 本地单帧参考站）
- DF17 type 19 地速/航向/垂直速率
- 飞机上世界地图（呼号标签避让）
- 表格：ICAO/呼号/高度/速度/航向/垂直速率/距离/时间

### 星时空（卫星过境）
- 真实 TLE 拉取（celestrak stations+weather）
- J2 摄动平均根数传播（非完整 SGP4）
- 24h 过境预报列表（AOS/LOS/最大仰角/倒计时/列排序）
- TLE 磁盘缓存（48h 过期）+ 30min 周期刷新
- 多卫星实时跟踪（天空极坐标+世界地图，选中星 accent 强调）
- AOS 前 2 分钟"即将过顶"提醒
- 本站位置热更新（设置保存后重拉 TLE+重算）

### 世界地图
- 等距圆柱投影，简化陆地块
- 本站绿点 / 飞机点 / 卫星点
- 拖拽平移 / 滚轮 1–8x 缩放 / 右键复位

### AI 助手
- OpenAI 兼容接口（base_url/key/model，QSettings 持久化）
- 异步 QNetworkAccessManager，不卡 UI
- 工具调用：调谐频率 / 切换模式 / 开始录制 / 查询状态
- 无 key 时输入框禁用，不造假回复

### 设置
- 本站位置（纬/经/海拔，热触发卫星重算）
- 音频（输出设备+主音量）
- 外观（UI 缩放 0.7/1.0/1.25/1.5 实时，主题"默认"）
- AI（Key 密码框+Base URL+Model）

### 快捷键
- ← / →：步进调谐（Shift 细调）
- ↑ / ↓：带宽 ×2 / ÷2
- Space：静音切换
- Ctrl+R：录制/停止

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

没有 RTL-SDR 设备时自动切到测试信号（界面标注"测试信号（非硬件）"）。

## 测试

```bash
LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib:/home/user/.local/lib \
QT_QPA_PLATFORM=offscreen ctest --test-dir build --output-on-failure
```

16 个测试套件：fft / peak_detector / channelizer / chain / demod / demod_e2e /
recorder / decoder / agent / bookmark / noise_blanker / engine_integration /
spectrum_interaction / shortcuts / geo / adsb_cpr。

## 目录结构

```
cpp/src/core/   tokens（颜色/尺寸/间距常量，唯一设计源）、共享数据结构
cpp/src/dsp/    引擎、解调、录制、解码、TLE 过境预测、峰值检测、噪声抑制
cpp/src/ui/     主窗口、频谱、瀑布、SkyView、世界地图、设置、关于、快捷键
cpp/src/ai/     LLM / Agent 层
cpp/tests/      Qt Test 单元测试
```

## AI 配置

- 配置文件：`~/.config/MBDSDR/ai_config.json`（字段 `api_key` / `base_url` / `model`）
- 无 key 时 AI 助手输入框禁用，不造假回复

## 已知限制

- 过境轨道传播是 J2 摄动近似（非完整 SGP4/BSTAR），LEO 过境时间误差在分钟级
- FC0012 调谐器收不到 1090 MHz，ADS-B 需 1090 MHz 专用 dongle 或文件回放
- 云端 / 无音频设备环境自动禁用音频输出
- librtlsdr 缺失时编译 stub 版本，RTL 高级选项优雅禁用
- rtl_tcp 源未实现 direct sampling / offset / PPM 等可选命令
- 所有功能在 offscreen + 测试信号下验证，真实硬件联调需真机环境
