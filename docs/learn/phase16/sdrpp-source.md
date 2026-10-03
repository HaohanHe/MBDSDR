# Phase16-Wave1-BatchD：SDR++ 统一 Source 抽象精读笔记

> 本笔记只落 `docs/learn/phase16/sdrpp-source.md`，不改生产代码、不 commit/push。
> 对照目标：`repos/sdrpp`（**SDR++，GPLv3**，源码只学机制、不入库）。凡引用 SDR++ 原文片段均标注 `[GPLv3]` 与 file:line；片段为注释性短引用，未复制成段逻辑。
> 对照基线：MBDSDR 自身代码（MIT）位于 `cpp/src/dsp/`。ctest 基线 **104/104**。
> 范围：rtl_sdr_source 全文真读 + 对照 airspy_source / airspyhf_source / file_source（≥2 个，实读 4 个）+ 共同遵循的 `core/src/signal_path/source.h` 注册约定 + `core/src/dsp/stream.h` 流控。

---

## 0. 真读文件清单（全部逐行读过，非仅目录/README）

| 文件 | 行数 | 角色 |
|---|---|---|
| `repos/sdrpp/core/src/signal_path/source.h` | 56 | SourceManager + SourceHandler 回调表（注册约定核心） |
| `repos/sdrpp/core/src/signal_path/source.cpp` | 106 | 注册/注销/选择/start/stop/tune 编排 |
| `repos/sdrpp/core/src/dsp/stream.h` | 142 | 双缓冲 + 条件变量流控（数据契约） |
| `repos/sdrpp/core/src/dsp/source.h` | 22 | DSP 侧 block 基类（与 signal_path 层不同） |
| `repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp` | 608 | 主对照源（全文） |
| `repos/sdrpp/source_modules/airspy_source/src/main.cpp` | 628 | 对照源：多档增益模式 |
| `repos/sdrpp/source_modules/airspyhf_source/src/main.cpp` | 427 | 对照源：AGC 档位 + 步进衰减 |
| `repos/sdrpp/source_modules/file_source/src/main.cpp` | 227 | 对照源：离线文件、无设备 |
| MBDSDR `cpp/src/dsp/source.h` | 83 | ISource C++ vtable 基类 |
| MBDSDR `cpp/src/dsp/rtl_sdr_source.{h,cpp}` | 109 / 417 | RtlSdrSource + RtlLibOps 适配器 |
| MBDSDR `cpp/src/dsp/rtl_sdr_ops.h` | 66 | librtlsdr ops 函数指针缝 |
| MBDSDR `cpp/src/dsp/file_source.h` | 92 | FileSource（SigMF/WAV/raw） |
| MBDSDR `cpp/src/dsp/device_capabilities.{h,cpp}` | 87 / 118 | 设备能力上报（诚实空态） |
| MBDSDR `cpp/src/dsp/tuner_gain_table.h` | ~58 | 离散增益档 snap 纯逻辑 |
| MBDSDR `cpp/src/dsp/spectrum_engine.{h,cpp}` 片段 | — | 引擎如何持有并驱动 ISource（拉取循环） |

---

## 1. SDR++ 侧：file:line + 注释性短片段

### 1.1 统一 Source 接口 = C 回调表（非 C++ vtable），数据契约是 `dsp::stream`

`core/src/signal_path/source.h:13-22` `[GPLv3]`：
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
要点：**回调表里没有「setGain / setSampleRate / 枚举设备」**——那些全下沉到各源自己的 `menuHandler`（GUI 回调）。管理器只掌握生命周期（menu/select/deselect/start/stop/tune）+ 一个出流 `stream` + 不透明 `ctx`。这是一个「**最小生命周期表 + 一个 IQ 输出流**」的统一抽象，增益/采样率/设备能力**不在统一层**。

注册/注销编排 `core/src/signal_path/source.cpp:10-60` `[GPLv3]`：
- 重名拒绝：`registerSource` `:11-14` 已存在名则 `flog::error` 并 return（不覆盖）。
- 选择即接管流：`selectSource` `:42-60` 先对旧源 `deselectHandler`，再 `selectHandler`，然后 `sigpath::iqFrontEnd.setInput(selectedHandler->stream)`；server 模式改走 `server::setInput(...)` `:53-55`（**同一流在 GUI 前端与远程 server 二选一**，是远程控制的接入点）。
- 同一时刻只有一个 `selectedHandler`（`source.h:49-50`）。

### 1.2 数据契约与流控：`dsp::stream<T>` 双缓冲 + 条件变量背压

`core/src/dsp/stream.h:43-68` `[GPLv3]`：
```cpp
virtual inline bool swap(int size) {
    std::unique_lock<std::mutex> lck(swapMtx);
    swapCV.wait(lck, [this] { return (canSwap || writerStop); });
    if (writerStop) { return false; }   // 收到停止：放弃本次写入
    dataSize = size;
    T* temp = writeBuf; writeBuf = readBuf; readBuf = temp;
    canSwap = false;                     // 写方等消费方 flush 后才能再 swap
    ... rdyCV.notify_all();
    return true;
}
```
机制：**写方（源）填 `writeBuf` → `swap()` 交换读写指针 → 阻塞等待 `canSwap`**；读方（iqFrontEnd）`read()`（`:70-76`）取走数据后 `flush()`（`:78-92`）把 `canSwap=true` 并 `swapCV.notify`。`stopWriter()`（`:94-100`）置 `writerStop` 并唤醒，使阻塞在 `swap` 里的写回调立刻返回 false——这就是 **stop() 能干净打断异步回调线程**的关键。缓冲区 `STREAM_BUFFER_SIZE = 1000000`（`:9`）。

### 1.3 rtl_sdr_source 全文要点 `[GPLv3]`（source_modules/rtl_sdr_source/src/main.cpp）

**模块导出约定（每个源都一样的 C ABI）：**
- 元信息 `SDRPP_MOD_INFO{...}` `:17-23`（名称/作者/版本/Max instances=1）。
- 导出 `_INIT_()` `:587`、`_CREATE_INSTANCE_(name)` `:596`、`_DELETE_INSTANCE_` `:600`、`_END_()` `:604`——动态库通过这 4 个 C 符号被实例化/销毁。

**构造即注册：** `:66-73` 把 6 个静态函数槽位填进 `handler`（`handler.ctx=this`），`:95` `sigpath::sourceManager.registerSource("RTL-SDR", &handler)`；析构 `:99-101` 先 `stop` 再 `unregisterSource`。

**采样率协商（RTL 是硬编码表）：** `:27-39` `sampleRates[]` 是一个**编译期常量数组**（250K…3.2M 共 11 档），不是从驱动读的。选好后经 `core::setInputSampleRate(this->sampleRate)`（`:277`/`:369`/`:379`）把「输入采样率」上推给 core。

**增益模型（手动 + tunerAgc + rtlAgc，单档离散增益表）：**
- 开设备时从驱动读离散增益表 `:192-196`：`rtlsdr_get_tuner_gains(openDev, gains)`，拷进 `gainList` 并排序。
- `start()` `:313-322`：`rtlsdr_set_agc_mode(rtlAgc)`；若 `tunerAgc` 则 `set_tuner_gain_mode(0)`（自动），否则 `set_tuner_gain_mode(1)`（手动）+ `set_tuner_gain(gainList[gainId])`。
- UI 滑条按 `gainId` 索引 `gainList`，`:448`/`:461` 运行中直接 `rtlsdr_set_tuner_gain(...)`。

**流控 = librtlsdr async 回调线程 → stream.swap：**
- `start()` `:324-328`：`asyncCount = round(sr/(200*512))*512`，起 `workerThread`。
- `worker()` `:526-529`：`rtlsdr_reset_buffer` 后 `rtlsdr_read_async(..., asyncHandler, ...)`。
- `asyncHandler` `:531-539` `[GPLv3]`：把无符号字节转 `(x-127.4)/128.0`，再 `if (!stream.swap(sampCount)) { return; }`——**swap 返回 false（收到停止）即直接 return，回调线程自然退出**。

**stop() 干净关停 `:332-342` `[GPLv3]`：** `stream.stopWriter()` → `rtlsdr_cancel_async()` → `workerThread.join()` → `stream.clearWriteStop()` → `rtlsdr_close()`。顺序（先停写再 cancel 再 join）保证异步回调不在关闭后碰 stream。

**错误恢复 / 调谐重试 `:344-359` `[GPLv3]`：**
```cpp
uint32_t newFreq = freq;
for (i = 0; i < 10; i++) {
    rtlsdr_set_center_freq(openDev, freq);
    if (rtlsdr_get_center_freq(openDev) == newFreq) { break; }
}
if (i > 1) flog::warn("RTL-SDR took {0} attempts to tune...", i);
```
即「写后回读、最多 10 次、不收敛仅告警不报错」——PLL 写丢失防御。

**设备能力上报（无统一结构，各自 refresh()）：** `refresh()` `:117-159` 用 `rtlsdr_get_device_count/name/usb_strings` 枚举设备，拼成 `vendor product [serial]##idx` 列表字符串给下拉框；增益表来自 `rtlsdr_get_tuner_gains`。**没有跨源统一的 Capabilities struct**。

### 1.4 对照源差异（airspy / airspyhf / file）

**airspy `[GPLv3]`（airspy_source/src/main.cpp）：**
- 采样率**从驱动查**：`:151-154` 先 `airspy_get_samplerates(dev, rates, 0)` 取个数，再取满，填 `sampleRateList`——与 RTL 的硬编码表形成对比。
- 增益**三模式**：`gainMode` 0=Sensitive(`set_sensitivity_gain`, `:286`)、1=Linear(`set_linearity_gain`, `:291`)、2=Free（LNA/Mixer/VGA 三段独立 + 各自 AGC，`:293-309`）。比 RTL 的单档增益复杂得多，且**全部在源内自行实现**，统一层不感知。
- 流控：`callback` `:565-570` `memcpy` 到 `writeBuf` 后 `if (!stream.swap(...)) return -1;`——同样的 swap 背压。

**airspyhf `[GPLv3]`（airspyhf_source/src/main.cpp）：**
- AGC 是**档位枚举** Off/Low/High（`AGG_MODES_STR` `:28`），`start` `:260-263` `set_hf_agc` + `set_hf_agc_threshold(agcMode-1)`。
- 衰减是**步进 6dB** 的滑条 `:351`（`atten/6.0f` → `set_hf_att`）。又是一种全新增益/衰减词汇表，证明**统一层刻意不抽象增益**。

**file `[GPLv3]`（file_source/src/main.cpp）：**
- 构造 `:33` **server 模式直接 return 不注册**（`if (core::args["server"].b()) { return; }`）——文件源在无头模式下不存在。
- 无设备：`tune()` `:111-114` 只打日志不改硬件；增益/采样率从 wav 头读（`:133` `reader->getSampleRate()`）。
- 拉块尺寸自适应 `:156-157`：`blockSize = min(sr/200, STREAM_BUFFER_SIZE)`；worker `:160-164` 循环 `readSamples → volk_16i_s32f_convert_32f → stream.swap`，swap false 即 break。
- `menuSelected` `:70-80` 把 `iqFrontEnd.setBuffering(false)` 并锁住 waterfall 中心频率——文件源会改全局前端缓冲行为，这是「源有副作用钩子」的例子。

---

## 2. 机制总结（跨 4 源归纳）

1. **统一接口的边界划在哪**：统一层（SourceManager）只管「生命周期 6 回调 + 1 个 IQ 出流 + ctx」。**增益模型、采样率表、设备枚举、能力字段全部故意留在各源内部**。统一的是「怎么被 start/stop/tune/接流」，不是「怎么设增益」。
2. **注册模型**：动态库导出 4 个 C 符号；构造时 `registerSource("名", &handler)`；同一时刻仅一个 selected；注销时把输入切到 `nullSource`。
3. **增益模型（无跨源统一）**：RTL=单离散增益表+tunerAgc+rtlAgc；airspy=3 模式×多段+LNA/Mixer AGC；airspyhf=AGC 档位+步进衰减。**每种增益词汇表都是源私有**。
4. **采样率协商**：两种范式并存——RTL 硬编码常量表；airspy/airspyhf 运行时从驱动 `get_samplerates` 查；file 从文件头读。选定后一律 `core::setInputSampleRate` 上推。
5. **流控**：唯一真统一的东西是 `dsp::stream<T>` 双缓冲 + 条件变量。源（异步回调线程）写 `writeBuf`→`swap` 阻塞等消费；消费端 `read`→`flush` 放行。stop 用 `stopWriter` 唤醒阻塞回调。
6. **错误恢复**：开设备失败 → 日志 + 置空选择（RTL `selectedDevName=""`）；调谐写丢失 → 写后回读最多 10 次（RTL）；swap 失败 → 回调返回 -1/false 自然退出。**整体偏「告警+继续」，无重连/无 watchdog 关设备**。
7. **设备能力上报**：无统一结构；各源 `refresh()` 自枚举设备列表 + 自读增益/采样率表。

---

## 3. MBDSDR 现状对照（读真实代码，file:line）

MBDSDR 走的是**与 SDR++ 不同但更「统一」的路线**：一个纯 C++ vtable 基类 `ISource` + 引擎**拉取式**读循环，而非 C 回调表 + push 异步双缓冲。

### 3.1 ISource 基类（cpp/src/dsp/source.h）`[MIT]`
- `start/stop/readIQ` `:21-27`；`setCenterFreq/setSampleRate/setGain` `:29-31`。
- **可选 RF 前端特性给空默认实现** `:38-48`：`setDirectSampling/setOffsetTuning/setRtlAgc/setTunerAgc/setBiasTee/setPpm` 全是 `{ (void)x; }` 空体——测试/文件源继承空操作，引擎可盲目转发。**这比 SDR++ 更进了一步**：SDR++ 把增益/前端全留源内，MBDSDR 反而把常见前端旋钮**上收到统一接口（带空默认）**。
- **统一能力上报** `:65` `virtual DeviceCapabilities capabilities() const { return noDeviceCapabilities(); }`——SDR++ 没有的统一结构。
- **统一离散增益表** `:70` `setGainStage(stage,gainDb)`、`:78` `availableGainsDb()`——把 airspy 的「多段增益」和 RTL 的「离散档」抽象成统一可选接口。

### 3.2 引擎持源与拉取流控（spectrum_engine）`[MIT]`
- 持有单一 `std::unique_ptr<ISource> source_`（`spectrum_engine.h:336`）——**同一时刻一个源**，等价于 SDR++ 的 selectedHandler，但没有「命名注册表、运行时切多个已注册源」。
- 拉取循环 `spectrum_engine.cpp:782-811`：每帧 `wantN = round(sr*0.025)`（约 25ms），`source_->readIQ(iq)`；**got==0 时对真实设备计零读帧**，达 `kMaxZeroReadBeforeDrop` 则 `dropSourceLocked()` + 发 `reconnectRequested`（`:790-805`）；空转时 `sleep_for(20ms)`（`:807`）。
- **流控模型差异**：MBDSDR 用**引擎线程阻塞式 `read_sync` + 20ms 节拍**，没有 SDR++ 的「源异步回调线程 + stream 双缓冲 + 消费 flush 放行」。即 MBDSDR 是 pull（消费端定速），SDR++ 是 push（生产端推、双缓冲吸收抖动）。

### 3.3 RtlSdrSource（rtl_sdr_source.{h,cpp}）`[MIT]`
- 经 `RtlLibOps` 函数指针缝（`rtl_sdr_ops.h:24-46`）调用 librtlsdr；生产绑定在 `HAVE_RTLSDR`（`rtl_sdr_source.cpp:132-202` 的 `adv*` 适配器 + `g_realOps`），否则 `rtlLibOps()` 返回 nullptr、`start()` 优雅失败 `:254-258`。
- **调谐重试（干净室派生 SDR++ 的 10 次，MBDSDR 收紧为 5）**：`pushCenterFreq` `:217-251`，每次写后 `getCenterFreq` 回读；`kRtlMaxTuneAttempts=5`（`rtl_sdr_ops.h:62`）；不收敛 `qWarning` 且**请求保留、标 `tuneConverged_=false`**（`:246-250`），不假装成功。
- **增益 snap**：`setGain` `:28-40` 用 `gainTable_.snap(gainDb)` 先吸附到最近合法档再推送；`start()` `:299-332` 从 `getTunerGains` 读表、重 snap、并 `getTunerGain` **回读驱动实际接受值**写入 `gainDb_`（诚实上报）。
- **读失败看门狗**：`readIQ` `:365-380` 连续 `kReadFailThreshold=20` 次失败（`rtl_sdr_source.h:104`）后**主动 close 设备、`dev_=nullptr`、`isConnected()=false`**——比 SDR++「仅告警」多了「判死关设备 + 引擎据此回落测试信号/重连」。

### 3.4 FileSource（file_source.h）`[MIT]`
- 三格式 SigMF / 16-bit mono WAV / raw cf32（`:32-38`）；`isConnected()` **恒 false**（`:55`），所以 EOF/暂停的零读**不会**被引擎误判为设备掉线（对应 3.2 的零读只对 real 生效）。
- 暂停/seek/progress 是真实文件偏移（`:58-64`），不是假循环。与 SDR++ file 的「swap false 即 break」不同，MBDSDR file 走同一套 `readIQ` 拉取接口。

### 3.5 设备能力上报（device_capabilities.{h,cpp}）`[MIT]`
- 统一 `DeviceCapabilities` struct（`device_capabilities.h:52-60`）：connected / deviceName / tunableMin/MaxHz / sampleRateMin/MaxHz / **provenance（数据出处）**。
- **诚实空态**：连不上全 0/`connected=false`，UI 显示「RTL-SDR 未连接」（`device_capabilities.cpp:67-73`）。
- 采样率候选档 `kCandidateRatesHz` `:36-47` 与 RTL2832U 接受规则（>225k、≤3.2M、避开 300k–900k 死区）对应；`buildSampleRateOptions` `:105-115` 按设备实测范围过滤——**比 SDR++ rtl 的硬编码表更「按设备派生」**。

---

## 4. 差距判定表：MBDSDR 源如何向「统一 Source 抽象」收敛

判定列：**已实现且真实 / 缺深度 / 未实现**；「通用价值」指与硬件无关、可云内确定性验证的部分；**不硬抄 GPL**，只取机制、自有命名与结构。

| 维度 | SDR++ 做法 | MBDSDR 现状 | 判定 | 收敛建议（通用、不硬抄） | 云内可验证方案 |
|---|---|---|---|---|---|
| 统一生命周期接口 | C 回调表 6 槽 + stream + ctx | C++ vtable `ISource::start/stop/readIQ` | **已实现且真实** | 保持 vtable（比 C 表更安全）；无需引入回调表 | 注入 fake ISource，测 start/stop 状态机 |
| 同一时刻单源 | selectedHandler + nullSource | `unique_ptr<ISource> source_` + `sourceChanged` 信号 | **已实现且真实** | 等价 | 切源后 sourceChanged 信号确定性触发 |
| **命名源注册表**（运行时枚举已注册多源并热切换） | `getSourceNames()` + map<string,handler> | 源是代码内硬接线，无注册表 | **缺深度** | 抽一个轻量 `ISourceFactory` 注册表（名→工厂），供无头控制层列出可用源 | 单测注册/枚举/未知名诚实报错 |
| 增益模型统一 | 无统一（各源自管） | `setGain` + 可选 `setGainStage`/`availableGainsDb` 统一空默认 | **已实现且更优** | 保持；airspy 类多段源可直接落 `setGainStage` | TunerGainTable snap 纯逻辑单测（空表 passthrough/吸附/越界钳位/回读） |
| 离散增益表 | `rtlsdr_get_tuner_gains` 源内读 | `TunerGainTable` 注入 + snap + 回读 actual | **已实现且真实** | 保持 | 注入 fake 表，测 snapped 值被推送、actual 被回读覆盖 |
| 采样率协商 | RTL 硬编码表 / airspy 驱动查 | `DeviceCapabilities` 范围 + `buildSampleRateOptions` 派生 | **已实现且更优** | 保持「按设备范围派生」范式 | 喂不同 caps，测候选档过滤/死区剔除 |
| 流控/背压 | push 异步回调 + `dsp::stream` 双缓冲 + flush 放行 | pull：引擎线程 read_sync + 20ms 节拍 | **架构不同，非缺陷** | 保持 pull（更简单、易测）；若未来需多源并发推流再引入有界队列 | fake source 定速/变速 readIQ，测引擎零读回落 |
| 调谐写丢失防御 | 写后回读最多 10 次 | `pushCenterFreq` 回读、上限 5、不收敛诚实告警 | **已实现且真实** | 保持「回读验证」机制 | fake ops 让回读≠目标，测耗尽预算后 `lastTuneConverged()==false` 且请求保留 |
| 错误恢复/看门狗 | 开失败置空、仅告警 | 读失败连续 20 次→关设备→isConnected=false→引擎回落/重连 | **已实现且更优** | 保持「判死关设备 + 事件上报」 | fake readSync 连续返错，测设备被关、isConnected 翻转 |
| 设备能力上报 | 无统一结构，各自 refresh | 统一 `DeviceCapabilities` + provenance + 诚实空态 | **已实现且更优** | 保持统一 struct | 喂握手字段，测 tuner 名/范围映射、未知型号落 0 |
| server 模式输入分流 | `server::setInput` vs `iqFrontEnd.setInput` 二选一 | （本批次未深读 server，见 BatchB） | **未实现（待 BatchB）** | 无头控制层落地时复用同一 IQ 出流做只读 tap | （Wave2 无头层 ctest 覆盖） |
| 设备热插拔枚举 | refresh() 枚举 vendor/product/serial | `device_lister`/`device_presence_notifier`（本批次未展开） | **缺深度（待核）** | 抽 `enumerateSources()` 统一枚举接口 | 枚举空态/枚举结果确定性 |

### 收敛结论（一句话）
MBDSDR 的 ISource 在「统一接口 / 增益抽象 / 能力上报 / 诚实空态 / 调谐回读 / 判死重连」上**已经比 SDR++ 更统一、更可测**；真正向 SDR++ 机制**还缺**的是：**① 命名源注册表 + 运行时热切换多源**、**② server/无头模式的 IQ 输入分流**（BatchB/Wave2 承接）、**③ 设备热插拔枚举的统一接口**。流控保持 pull 即可，不必硬抄双缓冲 push。

---

## 5. 未读透 / 待核清单（如实）

1. **`core/src/dsp/source.h`（DSP block 基类）与 signal_path/source.h 的关系**：前者是 DSP block 图节点（`registerOutput(&out)`），本批未读 `block.h`/`signal_path.cpp` 如何把 source 的 `stream` 接进 iqFrontEnd 的消费端——仅从 `setInput(stream)` 与 `stream.read()/flush()` 推断。属于 BatchA（signalpath 生命周期/线程模型）范围，本批只取了「流是数据契约」这一层。
2. **server 模式 IQ 分流**（`server::setInput`）：仅在 `source.cpp:53-55` 见到调用点，未读 `server.cpp`——归 BatchB，本批不展开。
3. **`device_lister.*` / `device_presence_notifier.*` / `rtl_tcp_source.*`**：MBDSDR 热插拔与 rtl_tcp 握手细节未逐行读（本批重点是本地 rtl_sdr_source + file_source）。
4. **airspyhf 源码里的调试日志**（`:135-137` 的 `==== CALLING airspyhf_open_fd ====`）疑似上游调试残留，未深究其影响。
5. **SDR++ `config.acquire()/release()` 的并发语义**：各源大量 `config.acquire(); ...; config.release(true)`，本批只当「读写配置」用，未读 ConfigManager 锁实现。
6. ctest 104 基线本批**未重跑**（纯笔记、零代码改动，无破基线风险；落地阶段再跑）。
