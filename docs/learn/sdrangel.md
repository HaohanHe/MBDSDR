# SDRangel 设备源插件链与信道解调插件组织方式 — 设计笔记

> 仓库：`https://github.com/f4exb/sdrangel`（depth=1 clone）
> 笔记日期：2026-09-24
> 目的：读懂 SDRangel 的设备源插件链 + 信道解调插件组织方式，对照 MBDSDR 给出架构迁移建议。

---

## 1. 设备源插件链：数据从硬件流到多个信道

### 1.1 整体链路图

```
硬件(RTL-SDR USB)
  │
  ▼
RTLSDRThread (QThread 子类，独立线程)
  │  rtlsdr_read_async() 回调 → callbackIQ/callbackQI()
  │  → DecimatorsU 硬件端抽取(可选 log2Decim)
  ▼
DeviceSampleSource::m_sampleFifo (SampleSinkFifo 环形缓冲)
  │
  │  signal: dataReady()
  ▼ (QueuedConnection, 在 GUI/主 DSP 线程)
DSPDeviceSourceEngine::handleData()
  │  → work()
  │    → DC offset / I/Q imbalance 校正
  │    → 遍历 m_basebandSampleSinks 列表，逐个 feed()
  ▼
每个 BasebandSampleSink (信道解调器 / 频谱显示 / 文件写入)
  │
  ▼
(信道内部) DownChannelizer → ChannelSampleSink → 实际解调
```

### 1.2 各环节详解

#### (1) 硬件线程：RTLSDRThread

- 文件：`plugins/samplesource/rtlsdr/rtlsdrthread.cpp:76-114`
- `run()` 循环调用 `rtlsdr_read_async()`，注册回调 `callbackHelper`
- 回调最终走到 `callbackIQ()`（`rtlsdrthread.cpp:118-237`）：
  - 原始 8-bit I/Q 字节流 → `m_decimatorsIQ.decimateN_inf/sup/cen()` 做硬件端抽取
  - 抽取后的 Sample 写入 `m_convertBuffer`
  - **第 231 行**：`m_sampleFifo->write(m_convertBuffer.begin(), it)` —— 数据写入 FIFO
- FIFO 是 `SampleSinkFifo*` 类型，由 `DeviceSampleSource` 基类持有（`devicesamplesource.h:173`）

#### (2) DeviceSampleSource — 设备源基类

- 文件：`sdrbase/dsp/devicesamplesource.h:39-176`
- 纯虚接口类，每个硬件源插件（RTL-SDR、HackRF、BladeRF…）都继承它
- 关键成员：
  - `m_sampleFifo`（`devicesamplesource.h:173`）：`SampleSinkFifo` 类型，硬件数据的出口
  - `m_inputMessageQueue`（`devicesamplesource.h:174`）：控制消息入口
  - `m_guiMessageQueue`（`devicesamplesource.h:175`）：发往 GUI 的消息队列
- 关键纯虚方法：
  - `start()` / `stop()` / `init()`（`devicesamplesource.h:57-59`）
  - `serialize()` / `deserialize()`（`devicesamplesource.h:61-62`）
  - `getSampleRate()` / `getCenterFrequency()`（`devicesamplesource.h:65,67`）
  - `handleMessage()`（`devicesamplesource.h:70`）

#### (3) RTLSDRInput — RTL-SDR 源插件的核心实现

- 文件：`plugins/samplesource/rtlsdr/rtlsdrinput.h:39-196`
- 继承 `DeviceSampleSource`（`rtlsdrinput.h:39`）
- 构造函数 `rtlsdrinput.cpp:56-77`：
  - 第 65 行：`m_sampleFifo.setLabel(m_deviceDescription)`
  - 第 66 行：`openDevice()` —— 打开 USB 设备
  - 第 68 行：`m_deviceAPI->setNbSourceStreams(1)`
- `start()`（`rtlsdrinput.cpp:230-254`）：
  - 第 244 行：`m_rtlSDRThread = new RTLSDRThread(m_dev, &m_sampleFifo, ...)` —— 把 FIFO 指针传给硬件线程
  - 第 246 行：`m_rtlSDRThread->startWork()` —— 启动硬件读取线程

#### (4) DSPDeviceSourceEngine — 设备采样引擎

- 文件：`sdrbase/dsp/dspdevicesourceengine.h:37-149`
- 这是设备源侧的"数据分发引擎"，运行在 GUI 主 DSP 线程
- 关键成员：
  - `m_deviceSampleSource`（`dspdevicesourceengine.h:84`）：指向当前硬件源
  - `m_basebandSampleSinks`（`dspdevicesourceengine.h:87-88`）：`std::list<BasebandSampleSink*>` —— 所有信道解调器 + 频谱 sink 的列表
- 连接 FIFO → 引擎：`dspdevicesourceengine.cpp:495`
  ```cpp
  connect(m_deviceSampleSource->getSampleFifo(), SIGNAL(dataReady()),
          this, SLOT(handleData()), Qt::QueuedConnection);
  ```
- 数据分发核心：`work()`（`dspdevicesourceengine.cpp:288-337`）
  - 第 290 行：从 source 拿 FIFO 指针
  - 第 301 行：`sampleFifo->readBegin()` 读取环形缓冲数据（分两段处理 wrap）
  - 第 307-309 行：DC offset / I/Q 校正
  - **第 312-314 行**：遍历所有 sink，逐个 `(*it)->feed(part1begin, part1end, positiveOnly)`
  - 第 334 行：`sampleFifo->readCommit(count)` 提交读指针
- 添加 sink：`addSink()`（`dspdevicesourceengine.cpp:105-110`）—— 发 `DSPAddBasebandSampleSink` 消息到队列，实际 push_back 在 `handleMessage()` 第 618 行

#### (5) BasebandSampleSink — 基带 sink 接口

- 文件：`sdrbase/dsp/basebandsamplesink.h:28-42`
- 极简接口：
  ```cpp
  virtual void start() = 0;
  virtual void stop() = 0;
  virtual void feed(const SampleVector::const_iterator& begin,
                    const SampleVector::const_iterator& end,
                    bool positiveOnly) = 0;
  virtual void pushMessage(Message *msg) = 0;
  virtual QString getSinkName() = 0;
  ```
- 所有信道解调器（AMDemod、FTDemod…）都实现这个接口

---

## 2. 信道解调插件组织

### 2.1 目录结构（以 demodam 为例）

```
plugins/channelrx/demodam/
├── amdemodplugin.h/cpp      # 插件入口：继承 PluginInterface，注册到 PluginManager
├── amdemod.h/cpp            # 信道顶层类：继承 BasebandSampleSink + ChannelAPI
├── amdemodbaseband.h/cpp    # DSP 工作线程体：持有 DownChannelizer + 解调器 Sink
├── amdemodsink.h/cpp        # 实际解调算法：AM 解调、音量、静噪
├── amdemodsettings.h/cpp    # 设置类：所有参数 + serialize/deserialize
├── amdemodgui.h/cpp/ui      # GUI 面板：继承 ChannelGUI
├── amdemodwebapiadapter.h/cpp # REST API 适配
└── CMakeLists.txt
```

### 2.2 三层架构

| 层 | 类 | 线程 | 职责 |
|---|---|---|---|
| 插件层 | `AMDemodPlugin` | 主线程 | 注册、创建实例、创建 GUI |
| 信道控制层 | `AMDemod` | 主线程 | 实现 `BasebandSampleSink` 接口，转发 feed 到 baseband；管理消息队列；持有 `m_basebandSink` 指针 |
| DSP 工作层 | `AMDemodBaseband` | 独立 QThread | 持有 `DownChannelizer` + `AMDemodSink`，实际跑 DSP |

#### (1) 插件层：AMDemodPlugin

- 文件：`plugins/channelrx/demodam/amdemodplugin.cpp:29-93`
- 继承 `PluginInterface`（通过 `amdemodplugin.h`）
- 注册：`initPlugin()` 第 50-56 行
  ```cpp
  void AMDemodPlugin::initPlugin(PluginAPI* pluginAPI) {
      m_pluginAPI = pluginAPI;
      m_pluginAPI->registerRxChannel(AMDemod::m_channelIdURI, AMDemod::m_channelId, this);
  }
  ```
- 创建信道实例：`createRxChannel()` 第 58-72 行
  ```cpp
  void AMDemodPlugin::createRxChannel(DeviceAPI *deviceAPI, BasebandSampleSink **bs, ChannelAPI **cs) const {
      AMDemod *instance = new AMDemod(deviceAPI);
      if (bs) *bs = instance;
      if (cs) *cs = instance;
  }
  ```
  —— 注意：`AMDemod` 同时继承 `BasebandSampleSink` 和 `ChannelAPI`，所以一个实例同时赋给两个指针
- 创建 GUI：`createRxChannelGUI()` 第 84-88 行 → `AMDemodGUI::create(...)`

#### (2) 信道控制层：AMDemod

- 文件：`plugins/channelrx/demodam/amdemod.h:42-186`
- 多继承：`class AMDemod : public BasebandSampleSink, public ChannelAPI`（`amdemod.h:42`）
- 构造函数 `amdemod.cpp:53-82`：
  - 第 66 行：`m_deviceAPI->addChannelSink(this)` —— 把自己注册为设备的信道 sink
  - 第 67 行：`m_deviceAPI->addChannelSinkAPI(this)`
- `feed()` 方法 `amdemod.cpp:116-120`：
  ```cpp
  void AMDemod::feed(const SampleVector::const_iterator& begin, const SampleVector::const_iterator& end, bool firstOfBurst) {
      m_basebandSink->feed(begin, end);  // 转发给工作线程
  }
  ```
- `start()` 方法 `amdemod.cpp:122-148`：
  - 第 129 行：`m_thread = new QThread()`
  - 第 130 行：`m_basebandSink = new AMDemodBaseband()`
  - 第 132 行：`m_basebandSink->moveToThread(m_thread)` —— DSP 移到独立线程
  - 第 139 行：`m_thread->start()`

#### (3) DSP 工作层：AMDemodBaseband

- 文件：`plugins/channelrx/demodam/amdemodbaseband.h:34-102`
- 关键成员（`amdemodbaseband.h:85-90`）：
  ```cpp
  SampleSinkFifo m_sampleFifo;      // 信道自己的输入 FIFO
  DownChannelizer m_channelizer;    // 下变频信道化器
  AMDemodSink m_sink;               // 实际解调器
  ```
- 数据流：`feed(begin, end)` → `m_sampleFifo.write()` → `handleData()` slot → `m_channelizer.feed()` → `m_sink.feed()`

### 2.3 DownChannelizer — 信道下变频

- 文件：`sdrbase/dsp/downchannelizer.h:33-96`
- 继承 `ChannelSampleSink`（`downchannelizer.h:33`）
- 原理：多阶半带滤波器（half-band filter）级联，每阶 2 倍抽取
  - `m_filterStages`（`downchannelizer.h:76`）：`std::list<FilterStage*>`
  - `feed()` 实现 `downchannelizer.cpp:47-90`：
    - 无滤波级时直接转发（第 57 行）
    - 有滤波级时逐 sample 过每阶半带滤波，通过的 sample 存入 `m_sampleBuffer`（第 83 行），最后批量喂给下游 sink（第 87 行）
- 频率偏移通过数字混频实现：`setChannelization(requestedSampleRate, requestedCenterFrequency)`（`downchannelizer.h:41`）

### 2.4 ChannelSampleSink — 信道内 sink 接口

- 文件：`sdrbase/dsp/channelsamplesink.h:30-36`
- 极简接口，只有一个 `feed()` 纯虚函数
- DownChannelizer 继承它，AMDemodSink 也继承它

---

## 3. 多信道并行：一个设备源怎么同时喂多个信道

### 3.1 数据分发模型

核心在 `DSPDeviceSourceEngine::work()`（`dspdevicesourceengine.cpp:288-337`）：

```cpp
// 第 312-314 行：遍历所有已注册的 baseband sample sinks
for (BasebandSampleSinks::const_iterator it = m_basebandSampleSinks.begin();
     it != m_basebandSampleSinks.end(); ++it) {
    (*it)->feed(part1begin, part1end, positiveOnly);
}
```

- **一个 FIFO，N 个 sink，广播式分发**
- 每个 sink 收到的都是**完整带宽的基带采样**（设备采样率 / log2Decim）
- 每个信道自己用 `DownChannelizer` 做信道选择和抽取

### 3.2 中心频率 / 偏移 / 采样率分配

#### 设备侧（全局）
- `m_centerFrequency`：设备本振频率（`dspdevicesourceengine.h:91`）
- `m_sampleRate`：设备基带采样率（`dspdevicesourceengine.h:90`）= `devSampleRate / (1 << log2Decim)`
  - 参考 `rtlsdrinput.cpp:318-322`：`getSampleRate()` 返回 `m_settings.m_devSampleRate / (1<<m_settings.m_log2Decim)`

#### 信道侧（每个信道独立）
- 每个信道的频率偏移由 `AMDemodSettings::m_inputFrequencyOffset` 控制（`amdemodsettings.cpp:37`）
- 信道通过 `DownChannelizer::setChannelization(channelSampleRate, requestedCenterFrequency)` 设置：
  - `requestedCenterFrequency` = 信道在基带中的偏移（相对于中心频率的偏移量）
  - DownChannelizer 内部做数字混频把目标信道搬到基带中心，再做半带滤波抽取
- 信道采样率由 `m_channelSampleRate` 决定（`downchannelizer.h:82`），通常等于解调器需要的速率（如 48k、12k）

#### 示例
假设 RTL-SDR 设备采样率 2.4 MS/s，本振 100 MHz：
- 信道 A：偏移 +5 kHz → AM 解调，带宽 6 kHz → DownChannelizer 抽取到 12 kS/s
- 信道 B：偏移 -100 kHz → 另一个 AM 台 → DownChannelizer 抽取到 12 kS/s
- 信道 C：偏移 +1 MHz → FM 广播台 → DownChannelizer 抽取到 200 kS/s

所有信道共享同一个 2.4 MS/s 基带流，各自做自己的下变频和抽取。

### 3.3 消息通知机制

设备采样率/中心频率变化时，引擎发 `DSPSignalNotification` 广播给所有 sink：
- `dspdevicesourceengine.cpp:544-581`：`handleMessage(DSPSignalNotification)` 中遍历 `m_basebandSampleSinks`，每个 sink push 一份副本
- 信道侧 `AMDemod::handleMessage()`（`amdemod.cpp:172-191`）收到后，再转发给自己的 baseband 工作线程

---

## 4. 设置 / 序列化

### 4.1 序列化方式：SimpleSerializer（不是 QSettings）

- 文件：`sdrbase/util/simpleserializer.h:30-80`
- 自定义二进制序列化格式，不是 QSettings（INI/JSON）
- 原理：TLV（Type-Length-Value）格式，每个字段有唯一 ID
  ```cpp
  SimpleSerializer s(1);  // version=1
  s.writeS32(1, m_inputFrequencyOffset);  // id=1
  s.writeS32(2, m_rfBandwidth/100);       // id=2
  s.writeString(9, m_title);              // id=9
  return s.final();
  ```
- 参考：`amdemodsettings.cpp:62-101`

### 4.2 反序列化与版本兼容

- `amdemodsettings.cpp:103-179`：
  - 第 107 行：`if(!d.isValid())` → 无效则 resetToDefaults
  - 第 113 行：`if(d.getVersion() == 1)` —— 版本检查
  - 每个字段都有默认值：`d.readS32(1, &m_inputFrequencyOffset, 0)` —— 读失败用默认值
  - 新增字段追加新 ID，旧版本读不到就用默认值，实现向后兼容

### 4.3 谁调用序列化

- 设备源：`DeviceSampleSource::serialize()` / `deserialize()`（`devicesamplesource.h:61-62`）
  - RTL-SDR 实现：`rtlsdrinput.cpp:286-311` → 委托给 `RTLSDRSettings`
- 信道：`ChannelAPI::serialize()` / `deserialize()`（`channelapi.h:72-73`）
  - AMDemod 实现：`amdemod.cpp:265-285` → 委托给 `AMDemodSettings`
- 顶层 Preset 保存时，DeviceUISet 遍历所有信道实例，收集每个信道的 `serialize()` 结果
  - `deviceuiset.cpp:455-471`：`saveRxChannelSettings()` → `preset->addChannel(uri, channelGUI->serialize())`

### 4.4 设置更新机制

- 不是直接改成员变量，而是通过消息：
  - GUI → 信道：`MsgConfigureAMDemod` 消息（`amdemod.h:44-68`）
  - 信道 → baseband 线程：`MsgConfigureAMDemodBaseband` 消息（`amdemodbaseband.h:38-62`）
- `applySettings()` 支持部分更新（`settingsKeys` 列表指定哪些字段变了）：`amdemodsettings.cpp:181-252`
  ```cpp
  if (settingsKeys.contains("inputFrequencyOffset")) {
      m_inputFrequencyOffset = settings.m_inputFrequencyOffset;
  }
  ```

---

## 5. 可迁移到 MBDSDR 的点

> 对照文件：`mbdsdr_ai/plugin_system.py`、`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/demod.py`

### 5.1 设备源 → 信道的数据流架构

**SDRangel 做法**：
- 硬件线程独立跑（RTLSDRThread），数据写入环形 FIFO
- 引擎线程从 FIFO 读数据，**广播式**分发给所有已注册的 sink
- 每个信道自己做下变频 + 解调

**MBDSDR 建议**：
- `sdr_backend.py` 里应该有一个 `DeviceSource` 基类（对应 `DeviceSampleSource`），持有一个 `sample_fifo`
- 一个 `DeviceEngine` 循环从 FIFO 读数据，遍历 `baseband_sinks` 列表逐个 `feed()`
- 不要在硬件回调里直接解调，硬件回调只做"读数据 + 入 FIFO"

### 5.2 插件注册机制

**SDRangel 做法**：
- `PluginInterface` 是抽象基类，定义了 `createRxChannel()`、`createRxChannelGUI()` 等纯虚方法
- `PluginManager` 扫描插件目录，用 Qt Plugin Loader 动态加载 `.so`/`.dll`
- 每个插件在 `initPlugin()` 里调用 `pluginAPI->registerRxChannel(uri, name, this)` 注册自己

**MBDSDR 建议**：
- `plugin_system.py` 应该定义一个 `PluginInterface` 抽象基类（Python ABC）
- 每个信道插件是一个 Python 模块/包，实现：
  - `get_plugin_descriptor()` → 返回名称、版本等元信息
  - `create_channel(device_api, settings)` → 返回信道实例（BasebandSampleSink 子类）
- `PluginManager` 扫描 `plugins/channelrx/` 目录，动态 import 模块，调用 `register()`
- 用 entry_points 或文件扫描均可，但要保证**插件与核心解耦**

### 5.3 信道插件目录结构

**SDRangel 做法**：每个信道一个目录，文件分工明确
```
demod_am/
├── plugin.py        # 插件注册入口
├── channel.py      # 信道顶层类（BasebandSampleSink 子类）
├── baseband.py     # DSP 工作线程
├── settings.py     # 设置类 + 序列化
├── gui.py          # GUI（如果有）
└── webapi.py       # REST API（可选）
```

**MBDSDR 建议**：
- `demod.py` 应该拆分成上述结构，不要把所有逻辑塞一个文件
- `channel.py` 只做接口实现和消息转发，DSP 放 `baseband.py`
- 设置独立成 `settings.py`，带 serialize/deserialize

### 5.4 多线程模型

**SDRangel 做法**：
- 硬件线程：QThread 子类，跑 `rtlsdr_read_async`
- 引擎线程：GUI 主线程，FIFO dataReady 信号触发
- 每个信道的 DSP 工作线程：独立 QThread，`moveToThread`
- 线程间通信全部走消息队列（MessageQueue），**不直接共享数据**

**MBDSDR 建议**：
- 硬件读取用 `threading.Thread` 或 `asyncio`
- 信道 DSP 用单独线程，通过 `queue.Queue` 传递数据和消息
- 不要在硬件回调里直接调解调函数，必须通过队列解耦

### 5.5 序列化格式

**SDRangel 做法**：自定义 TLV 二进制格式，带版本号，字段有 ID 和默认值

**MBDSDR 建议**：
- Python 生态直接用 JSON + 版本号即可，不需要搞二进制
- 每个信道 settings 类实现 `to_dict()` / `from_dict()`
- 顶层 preset 是一个 JSON：`{device: {...}, channels: [{uri: "...", config: {...}}, ...]}`
- 字段加默认值，新增字段不破坏旧配置加载

### 5.6 信道频率偏移模型

**SDRangel 做法**：
- 设备本振频率是全局的
- 每个信道有自己的 `inputFrequencyOffset`（相对于中心频率的偏移）
- DownChannelizer 根据偏移量做数字混频 + 抽取

**MBDSDR 建议**：
- 每个信道实例保存自己的 `frequency_offset` 和 `sample_rate`
- `Channelizer` 类负责：混频（把目标频点搬到基带 0）→ 低通滤波 → 抽取
- 信道之间独立设置频率偏移，互不干扰

### 5.7 关键设计模式总结

| 模式 | SDRangel 中的体现 | MBDSDR 对应 |
|---|---|---|
| 策略模式 | PluginInterface 抽象，各硬件插件实现 | PluginBase ABC |
| 观察者模式 | FIFO dataReady 信号 → 引擎；引擎 → 各 sink feed() | queue + callback |
| 命令模式 | MessageQueue 传递配置消息 | dict 消息 + queue.Queue |
| 工厂模式 | PluginManager::createRxChannel() | PluginManager.create_channel() |
| 外观模式 | DeviceAPI 统一管理设备+信道 | DeviceBackend 类 |

---

## 附录：关键文件索引

| 文件 | 作用 |
|---|---|
| `sdrbase/dsp/devicesamplesource.h:39` | 设备源抽象基类 |
| `sdrbase/dsp/dspdevicesourceengine.cpp:288` | 数据分发引擎 work() 主循环 |
| `sdrbase/dsp/basebandsamplesink.h:28` | 基带 sink 接口（信道必须实现） |
| `sdrbase/dsp/downchannelizer.cpp:47` | 信道下变频 feed() |
| `sdrbase/dsp/channelsamplesink.h:30` | 信道内 sink 接口 |
| `sdrbase/plugin/plugininterface.h:61` | 插件接口基类 |
| `sdrbase/plugin/pluginmanager.cpp:68` | 插件加载入口 |
| `sdrbase/device/deviceapi.h:43` | 设备 API（管理信道列表） |
| `plugins/samplesource/rtlsdr/rtlsdrplugin.cpp:62` | 源插件注册示例 |
| `plugins/samplesource/rtlsdr/rtlsdrinput.cpp:230` | 源 start() 启动硬件线程 |
| `plugins/samplesource/rtlsdr/rtlsdrthread.cpp:231` | 硬件回调写 FIFO |
| `plugins/channelrx/demodam/amdemodplugin.cpp:50` | 信道插件注册示例 |
| `plugins/channelrx/demodam/amdemod.cpp:42` | 信道类多继承 BasebandSampleSink + ChannelAPI |
| `plugins/channelrx/demodam/amdemodbaseband.h:86` | DSP 工作层持有 DownChannelizer |
| `plugins/channelrx/demodam/amdemodsettings.cpp:62` | 设置序列化示例 |
| `sdrgui/device/deviceuiset.cpp:344` | 信道创建 + 注册到 DeviceUISet |
