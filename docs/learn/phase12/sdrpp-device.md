# SDR++ 设备抽象与后端子系统 — 源码精读笔记（Phase12 Wave1-B）

> 上游：`repos/sdrpp/`（GPLv3，见 `license` 目录）。本笔记**只学机制/契约/交互范式**，
> 不逐字复制代码；落地到 MBDSDR 时一律用自有干净室实现。
> 所有上游引用均为 `repos/sdrpp/<path>:<line>`；MBDSDR 引用均为 `cpp/<path>:<line>`。
>
> 范围：源插件模型（`core/src/module.{h,cpp}`、`core/src/signal_path/source.h`、
> `core/src/dsp/stream.h`）+ 真实源插件 `source_modules/rtl_sdr_source/src/main.cpp`。
> 对照 MBDSDR：`cpp/src/dsp/source.h`、`rtl_sdr_source.{h,cpp}`、`device_capabilities.{h,cpp}`、
> `device_lister.h`、`audio_output.h`、`spyserver_server.h`。

---

## 1. 上游真实做法（GPLv3，仅机制）

### 1.1 没有 `IDevice` 抽象类——设备即"注册到 SourceManager 的回调表"

SDR++ 并没有一个形如 `class IDevice { virtual open()=0; virtual read()=0; ... }`
的设备基类。设备抽象是一张**按名字注册的回调函数表**：

- `core/src/signal_path/source.h:13-22` —
  ```cpp
  struct SourceHandler {
      dsp::stream<dsp::complex_t>* stream;
      void (*menuHandler)(void* ctx);
      void (*selectHandler)(void* ctx);
      void (*deselectHandler)(void* ctx);
      void (*startHandler)(void* ctx);
      void (*stopHandler)(void* ctx);
      void (*tuneHandler)(double freq, void* ctx);
      void* ctx;
  };
  ```
  七个 C 函数指针 + 一个 `ctx` + 一个指向输出 `stream` 的指针。
  Core 只认这张表，不知道底下是 RTL-SDR / SDRplay / SpyServer / 文件回放。

- `core/src/signal_path/source.h:29-35` — `SourceManager` 对外只暴露
  `registerSource / unregisterSource / selectSource / start / stop / tune`。
  内部 `std::map<std::string, SourceHandler*> sources`（:48）按插件名索引。

- 关键含义：**"切换设备"= 换一张回调表**。`selectSource(name)` 时 core 先
  `selectedHandler->stopHandler`，再切到新 handler 的 `stream`，再
  `newHandler->selectHandler` + `startHandler`。设备之间无继承关系、无虚函数表。

### 1.2 插件即 `.so`：五个 C 符号 + 元数据

- `core/src/module.h:17-29` — Windows/Linux/macOS 下 `MOD_EXPORT = extern "C"`，
  模块后缀 `.dll/.so/.dylib`。
- `core/src/module.h:33-41` — `ModuleInfo_t { name, description, author,
  versionMajor/Minor/Build, maxInstances }`。`maxInstances=1` 表示单实例
  （RTL-SDR 这种硬件只能开一个）。
- `core/src/module.h:43-50` — `Instance` 抽象基类只有四个虚函数：
  `postInit / enable / disable / isEnabled`。**没有 `read()`、没有 `open()`**——
  那些走 §1.1 的 `SourceHandler`。
- `source_modules/rtl_sdr_source/src/main.cpp:17-23` — RTL-SDR 插件导出的元数据：
  ```cpp
  SDRPP_MOD_INFO{
      "rtl_sdr_source", "RTL-SDR source module for SDR++", "Ryzerth",
      0, 1, 0,   // version
      1          // maxInstances
  };
  ```
- `source_modules/rtl_sdr_source/src/main.cpp:587-607` — 五个 C 入口：
  `_INIT_ / _CREATE_INSTANCE_ / _DELETE_INSTANCE_ / _END_`。
  `_CREATE_INSTANCE_` 返回 `new RTLSDRSourceModule(name)`，该类继承
  `ModuleManager::Instance`（main.cpp:57）。

### 1.3 设备枚举 / 打开 / 配置持久化（RTL-SDR 真实流程）

- **枚举**：`source_modules/rtl_sdr_source/src/main.cpp:117-159` —
  `rtlsdr_get_device_count()` → 对每个 idx 调 `rtlsdr_get_device_name(i)` 与
  `rtlsdr_get_device_usb_strings(i, vendor, product, serial)`，拼成
  `"Vendor Product [Serial]##idx"` 字符串（:134）。Android 走 `backend::getDeviceFD`
  拿 USB fd（:149），与桌面走同一套 `rtlsdr_open`。
- **打开（探测模式）**：`main.cpp:177-258` — `selectById(id)` 里**先 `rtlsdr_open`
  再 `rtlsdr_get_tuner_gains` 读增益表再 `rtlsdr_close`**（:181/:194/:257）。
  即"选中设备"只是为了读能力 + 加载该设备专属 JSON 配置，并不保持句柄。
- **按设备名持久化**：`main.cpp:200-210` — 配置 key 是 `config["devices"][selectedDevName]`，
  每个 dongle 独立存 `sampleRate / directSampling / ppm / biasT / offsetTuning /
  rtlAgc / tunerAgc / gain`。多根 RTL-SDR 各自记得自己的档位。
- **真正开流**：`main.cpp:286-330` `start()` — 重新 `rtlsdr_open`（:295），按顺序下发：
  `set_sample_rate`(:307) → `set_center_freq`(:308) → `set_freq_correction`(:309) →
  `set_tuner_bandwidth(dev, 0)`(:310, **0=驱动自动**) → `set_direct_sampling`(:311) →
  `set_bias_tee`(:312) → `set_agc_mode`(:313, RTL2832 基带 AGC) →
  tuner AGC 二选一(:315-321) → `set_offset_tuning`(:322)。

### 1.4 流处理：异步回调 + 双缓冲 swap

- `core/src/dsp/stream.h:9` — `STREAM_BUFFER_SIZE = 1'000'000`（1 MSample）。
- `core/src/dsp/stream.h:24-141` — 模板 `dsp::stream<T>`：
  - 双缓冲 `writeBuf / readBuf`（:125-126），`buffer::alloc` 对齐分配。
  - `swap(size)`（:43-68）：生产者写完一块后**阻塞等消费者 `flush()`**，
    再交换指针、置 `dataReady`、`rdyCV.notify_all()`。
  - `read()`（:70-76）：消费者阻塞等 `dataReady` 或 `readerStop`，返回 `dataSize`。
  - `stopWriter / stopReader`（:94-116）：退出协议——写者停时 `swap()` 返回 false
    让回调立刻退出，避免 join 死锁。
- RTL-SDR 实际接线（`main.cpp:526-539`）：
  ```cpp
  void worker() {
      rtlsdr_reset_buffer(openDev);
      rtlsdr_read_async(openDev, asyncHandler, this, 0, asyncCount);  // :528
  }
  static void asyncHandler(unsigned char* buf, uint32_t len, void* ctx) {
      // uint8 -> float, 写入 stream.writeBuf                        // :535-536
      _this->stream.swap(sampCount);                                 // :538
  }
  ```
  即 **librtlsdr 线程 = DSP 生产者线程**；core 的 IQ frontend 在自己的线程
  `stream.read()` 阻塞拿数据。零拷贝、无 per-block `new`。

### 1.5 采样率 / 带宽 / AGC 切换语义

- **采样率**：固定表 `main.cpp:27-39`（250k…3.2M，11 档）。
  `menuHandler`（:364）在 `running==true` 时 `SmGui::BeginDisabled()`——
  **采样率 combo 开流期间灰掉**，改 SR 必须 stop → 改 → start。
  改完调 `core::setInputSampleRate(sr)`（:369/:379/:393）通知 DSP 链重配置。
- **带宽**：RTL-SDR 只在 `start()` 里调一次 `rtlsdr_set_tuner_bandwidth(dev, 0)`
  （:310），`0` = 驱动自动。**没有运行时 BW 滑条**（R82xx/E4000 的 BW 由
  librtlsdr 按 SR 自动选）。
- **AGC（两路独立开关）**：
  - `rtlAgc`（RTL2832 基带 AGC）：`rtlsdr_set_agc_mode`（:313/:498）。
  - `tunerAgc`（调谐器 AGC）：`true → rtlsdr_set_tuner_gain_mode(dev, 0)`（自动），
    `false → mode 1 + rtlsdr_set_tuner_gain(dev, gainList[gainId])`（手动）
    （:315-321/:508-516）。UI 上 tunerAgc 打开时灰掉 Gain 滑条（:438/:472）。
- **Tune 重试**：`main.cpp:344-359` — 运行中改频率最多试 10 次，每次
  `rtlsdr_set_center_freq` 后 `rtlsdr_get_center_freq` **回读校验**，
  超过 1 次重试打 warning。这是对 RTL2832 PLL 偶发写丢的真实防御。

---

## 2. MBDSDR 现状（自有 MIT 代码）

- `cpp/src/dsp/source.h:16-79` — `class ISource` 抽象基类：
  `start/stop/readIQ` 纯虚；`setCenterFreq/setSampleRate/setGain` 纯虚；
  七个可选前端开关 `setDirectSampling/setOffsetTuning/setRtlAgc/setTunerAgc/
  setBiasTee/setPpm`（:38-48）给默认空实现；`capabilities()` 返回
  `DeviceCapabilities`（:65）。**比 SDR++ 的 C 回调表更 OOP、更类型安全**。
- `cpp/src/dsp/rtl_sdr_source.h:23-91` — `RtlSdrSource : ISource`，
  `dev_` 在 `HAVE_RTLSDR` 宏下编译；无该宏时整文件退化成 stub（.cpp:266-280）。
- `cpp/src/dsp/rtl_sdr_source.cpp:131-204` — `start()`：
  `rtlsdr_open(&dev_, 0)`（:132）→ 顺序下发 center_freq/sample_rate/gain_mode/
  agc/direct_sampling/offset_tuning/bias_tee/ppm（:141-154）→
  `rtlsdr_get_tuner_gains` 读离散增益表 → snap + `rtlsdr_get_tuner_gain` 回读
  真值（:180-185）→ `rtlsdr_reset_buffer`（:196）。
- `cpp/src/dsp/rtl_sdr_source.cpp:216-250` — `readIQ()`：**拉模型**，
  `rtlsdr_read_sync` 到一个 per-call `std::vector<unsigned char> raw(n*2)`
  （:222/:224），再 uint8→float 转换到 `out`。带 `StreamWatchdog`（:230-238）
  连续失败 20 次就关设备、`isConnected()` 翻 false。
- `cpp/src/dsp/rtl_sdr_source.cpp:252-259` — **运行中** `setCenterFreq` /
  `setSampleRate` 直接 push 到 `dev_`（无 stop/start，无重试，无回读）。
- `cpp/src/dsp/device_capabilities.h:52-60` — `DeviceCapabilities{ connected,
  deviceName, tunableMin/MaxHz, sampleRateMin/MaxHz, provenance }`，
  空态全 0，**绝不猜**。`buildSampleRateOptions(caps)`（:83）按真实接受区间派生 combo。
- `cpp/src/dsp/device_lister.h:22-40` — `IRtlDeviceEnumerator` 注入式枚举缝，
  生产包 `rtlsdr_get_device_count/name`，测试塞假枚举器；diff 出新增/移除。
- `cpp/src/dsp/audio_output.h:23-54` — `AudioOutput : IAudioSink`，瘦 facade
  委托给 `QtAudioSink`，带 `audioHealthStatus()` 诚实健康态。
- `cpp/src/dsp/spyserver_server.h:1-25` — SpyServer **服务端**干净室重写
  （对外推 IQ），`SpyServerTuner` 三个回调桥到 engine。

---

## 3. 差距判定片段（供 phase12 整合）

| # | 上游 file:line | MBDSDR file:line | 判定 | 落地价值 | 云内可确定性验证 |
|---|---|---|---|---|---|
| G1 | `repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:364` `if (_this->running) SmGui::BeginDisabled();` — SR combo 开流期间灰掉，改 SR 必须 stop→start | `cpp/src/dsp/rtl_sdr_source.cpp:256-259` `setSampleRate()` 在 `dev_` 打开时直接 `rtlsdr_set_sample_rate(dev_, fs_)` | **已实现但缺深度**：MBDSDR 允许运行中改 SR，但没验证下游 channelizer/resampler/audio 是否同步重配；SDR++ 用"停启"避免半更新的 DSP 链 | 防止运行中切 SR 后 VFO/音频采样率错位；UI 契约更清晰 | 是——注入 fake `rtlsdr_*` + 假 ISource 下游 spy，`setSampleRate(4e6)` 后断言 engine 广播了 SR-change 事件并触发 resampler 重建；不通过则按 SDR++ 语义改为"运行中禁用 combo" |
| G2 | `repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:310` `rtlsdr_set_tuner_bandwidth(openDev, 0);` — 显式下发 0=自动 | `cpp/src/dsp/rtl_sdr_source.cpp` 全文 grep 无 `set_tuner_bandwidth` | **已实现但缺深度**：MBDSDR 依赖 librtlsdr 默认（实际也是自动），但没显式表达意图；未来换 SoapySDR/BladeRF 后端时 BW 语义会漂移 | 一行显式调用，固化"BW=驱动自动"契约；为未来后端留 seam | 是——fake `rtlsdr_set_tuner_bandwidth` 注入计数，断言 `start()` 里被调一次且参数=0 |
| G3 | `repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:344-359` tune 最多 10 次重试 + `rtlsdr_get_center_freq` 回读校验 | `cpp/src/dsp/rtl_sdr_source.cpp:252-255` 单次 `rtlsdr_set_center_freq`，无回读无重试 | **未实现**：RTL2832 PLL 偶发写丢时 MBDSDR 会静默停在错频率 | 真机调谐鲁棒性；UI 显示频率与硬件实际一致 | 是——fake `set_center_freq` 前 K 次返回的值与 `get_center_freq` 不一致，断言重试循环在 ≤10 次内收敛并打 warn |
| G4 | `repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:200-255` 配置按 `config["devices"][selectedDevName]` 持久化（每根 dongle 独立） | `cpp/src/dsp/rtl_sdr_source.cpp` 无 per-device 配置 key（grep `devices[` 无命中）；设置全局 | **未实现**（单 dongle 时无感）：插第二根 RTL-SDR 时两机共用同一份 gain/PPM/biasT | 多 dongle UX；`DeviceCapabilities.deviceName` 已经是天然 key | 是——注入两个假 `RtlDeviceInfo`，切换后断言设置 JSON 按 serial 分桶 |
| G5 | `repos/sdrpp/core/src/dsp/stream.h:43-68` + `source_modules/rtl_sdr_source/src/main.cpp:526-539` — librtlsdr 异步回调直写双缓冲 writeBuf，`swap()` 阻塞等消费者，零 per-block 分配 | `cpp/src/dsp/rtl_sdr_source.cpp:216-250` — 每帧 `rtlsdr_read_sync` 到新 `vector<uint8_t> raw(n*2)`，再 uint8→float 拷贝到 `out` | **已实现但缺深度**：拉模型对 Qt 友好，但每 ~20ms 一次堆分配 + 一次额外拷贝；SDR++ 推模型零拷贝 | CPU/延迟收益（量化后再定）；Qt 主线程集成成本是真实代价 | 是——benchmark `readIQ` 循环 10k 次，统计堆分配次数与 P50/P99 耗时；若收益 <5% 则不动，记为"已实现且足够" |

---

## 4. 落地建议（按价值/可验证性排序）

1. **G3 tune 重试 + 回读**（最高优先）：~20 行自有实现，真机鲁棒性直接收益，
   且 fake 驱动可 100% 确定性单测。
2. **G2 显式 `set_tuner_bandwidth(dev, 0)`**：一行，固化契约，顺手。
3. **G1 SR 运行时语义**：先补"SR 变更后 engine 是否广播下游重配"的单测；
   若不通过，按 SDR++ 语义把 combo 在 running 时禁用，而不是冒险 live-push。
4. **G4 per-device 配置**：等真有第二根 dongle 再做；当前用 `deviceName` 做 key
   的 JSON 分桶即可。
5. **G5 零拷贝推模型**：benchmark 先量化；不达标就保持拉模型（Qt 友好）。

## 5. 未完成项 / 环境限制

- 未读 `sdrplay_source` / `airspy_source` / `spyserver_source` 等其它后端——
  本笔记以 RTL-SDR 为切片证明机制；多后端共性结论（§1.1-§1.4）已稳定。
- 未读 `core/src/signal_path/iq_frontend.{cpp,h}`（下游如何消费 `stream.read()`），
  留给 Wave1-D（信号处理）/ Wave1-F（插件架构）补。
- 未在真机跑 RTL-SDR；所有"真机待验"条目均以 fake 驱动单测为云内确定性验证手段。
