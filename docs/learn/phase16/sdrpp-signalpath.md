# Wave1 / Batch A — SDR++ 信号路径与生命周期精读笔记

> 范围：`repos/sdrpp/core/src/signal_path/{signal_path,source,sink,vfo_manager,iq_frontend}.{h,cpp}`、`core/src/core.cpp`、配套 `dsp/{stream,block,chain}.h`、`dsp/routing/splitter.h`、`dsp/sink/handler_sink.h`、`dsp/buffer/frame_buffer.h`、`dsp/channel/rx_vfo.h`、`core/src/module.{h,cpp}`、`gui/main_window.cpp` 启动段、`server.cpp` 启动段。
>
> **版权声明**：SDR++ 为 GPLv3 项目。下列引用仅为**机制学习**的注释性短片段（每处 ≤ 3 行、来自原文 file:line），**不复制任何实现代码进 MBDSDR**；MBDSDR 落地走干净室（自有命名/结构，MIT）。引用目的是把"哪一行做了什么"锚定下来，便于复盘。

---

## 1. 真读 file:line + 注释性短片段

> 路径前缀统一记为 `repos/sdrpp/core/src/`，下文用相对路径。

### 1.1 全局单例 —— `signal_path.{h,cpp}`

- `signal_path.h:8-13`：命名空间 `sigpath` 直接导出 4 个全局对象（非指针、非工厂），整个进程一份：
  ```cpp
  namespace sigpath {
      SDRPP_EXPORT IQFrontEnd iqFrontEnd;
      SDRPP_EXPORT VFOManager vfoManager;
      SDRPP_EXPORT SourceManager sourceManager;
      SDRPP_EXPORT SinkManager sinkManager;
  };
  ```
- `signal_path.cpp:3-8`：仅做一次定义。**信号路径是进程级单例**，模块通过 `sigpath::iqFrontEnd.xxx()` 直接拿到。

### 1.2 SourceManager —— `signal_path/source.{h,cpp}`

- `source.h:13-22`：每个源模块注册一个 C 函数指针表（不是虚基类，是 C ABI 风格回调）：
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
- `source.cpp:42-60` `selectSource()`：选源即**改 IQ 前端的输入指针**，server 模式下改 server 的输入：
  ```cpp
  if (core::args["server"].b()) {
      server::setInput(selectedHandler->stream);
  } else {
      sigpath::iqFrontEnd.setInput(selectedHandler->stream);
  }
  ```
- `source.cpp:83-91` `tune()`：NORMAL 模式把用户频率加上 `tuneOffset` 转给硬件 `tuneHandler`，再 `onRetune.emit(freq+tuneOffset)` 通知下游；PANADAPTER 模式则按 `ifFreq` 调硬件（硬件本振不动，只移 IF）。
- `source.cpp:19-34` `unregisterSource()`：若卸载的是当前选中源，先 `deselectHandler`，再把 iqFrontEnd 输入切到内置 `nullSource`，避免悬空指针。

### 1.3 SinkManager —— `signal_path/sink.{h,cpp}`

- `sink.h:17-23`：`Sink` 抽象只有 3 个纯虚：`start()/stop()/menuHandler()`。
- `sink.h:25-66`：一个 `Stream` = `splitter → volumeAjust → sinkOut` 的固定小链。`providerId` 是 combo 索引，不是指针。
- `sink.cpp:21-29` `Stream::init()`：
  ```cpp
  splitter.init(_in);
  splitter.bindStream(&volumeInput);
  volumeAjust.init(&volumeInput, 1.0f, false);
  sinkOut = &volumeAjust.out;
  ```
- `sink.cpp:31-50` `start()/stop()`：按 `splitter → volumeAjust → sink` 顺序启停（注意 stop 是同序反向，因为都是独立线程，谁先停都行，这里是对称写法）。
- `sink.cpp:218-240` `setStreamSink()`：运行中换 sink provider 时——先 `sink->stop()`、`delete`、`create` 新的、若原在跑则 `start()`。**换 sink 不拆上游 splitter**。
- `sink.cpp:138-162` `registerStream()`：新流默认绑 `NullSink`，再从 config 恢复。
- `sink.cpp:109-136` `unregisterSinkProvider()`：把所有用该 provider 的流先切回 "None"，再删 provider——**引用方自动降级到空 sink，不崩**。

### 1.4 VFOManager —— `signal_path/vfo_manager.{h,cpp}`

- `vfo_manager.h:10-40`：一个 `VFO` 同时持有 **DSP 侧** `dsp::channel::RxVFO* dspVFO` 和 **GUI 侧** `ImGui::WaterfallVFO* wtfVFO`——业务层把"频谱框"和"实际信道化器"绑成一个对象。
- `vfo_manager.cpp:5-18` VFO 构造：`dspVFO = sigpath::iqFrontEnd.addVFO(...)`，即 **VFOManager 不自己做信道化，只是 iqFrontEnd 里多个 RxVFO 的 GUI/业务包装**。
- `vfo_manager.cpp:20-28` 析构顺序：`dspVFO->stop()` → 从 waterfall 删 → `sigpath::iqFrontEnd.removeVFO(name)` → 删 GUI 对象。
- `vfo_manager.cpp:30-42` `setOffset/setCenterOffset`：先改 waterfall 框，再 `dspVFO->setOffset(...)`——**GUI 是真源，DSP 跟着 GUI 走**。
- `vfo_manager.cpp:95-103` `createVFO()`：重名/空名返回 NULL，构造完 `onVfoCreated.emit(vfo)`（main_window 据此在 waterfall 画框）。
- `vfo_manager.cpp:203-209` `updateFromWaterfall()`：每帧从 waterfall 把 `centerOffsetChanged` 同步回 DSP（鼠标拖拽路径）。

### 1.5 IQFrontEnd —— `signal_path/iq_frontend.{h,cpp}`

- `iq_frontend.h:66-84` 成员布局自顶向下就是**数据流拓扑**：
  ```
  inBuf → preproc{decim, dcBlock, conjugate} → split →┬→ fftIn → reshape → fftSink
                                                      └→ vfoStreams[*] (每个 RxVFO 一个输入流)
  ```
- `iq_frontend.cpp:29-47` `init()`：
  ```cpp
  inBuf.init(in);  inBuf.bypass = !buffering;
  decim.init(NULL, _decimRatio);
  dcBlock.init(NULL, genDCBlockRate(effectiveSr));
  conjugate.init(NULL);
  preproc.init(&inBuf.out);
  preproc.addBlock(&decim, _decimRatio > 1);
  preproc.addBlock(&dcBlock, dcBlocking);
  preproc.addBlock(&conjugate, false);
  split.init(preproc.out);
  ```
  `preproc` 是一个可动态旁路/接入的 `dsp::chain`（见 1.7）。
- `iq_frontend.cpp:140-160` `addVFO()`：
  ```cpp
  dsp::stream<dsp::complex_t>* vfoIn = new dsp::stream<dsp::complex_t>;
  dsp::channel::RxVFO* vfo = new dsp::channel::RxVFO(vfoIn, effectiveSr, sampleRate, bandwidth, offset);
  vfoStreams[name] = vfoIn;  vfos[name] = vfo;
  bindIQStream(vfoIn);        // = split.bindStream(vfoIn)
  vfo->start();
  ```
- `iq_frontend.cpp:76-99` `setSampleRate()` 重配时序（关键）：
  ```cpp
  dcBlock.tempStop();
  for (auto& [name, vfo] : vfos) vfo->tempStop();
  _sampleRate = sampleRate;  effectiveSr = _sampleRate / _decimRatio;
  dcBlock.setRate(...);
  for (auto& [name, vfo] : vfos) vfo->setInSamplerate(effectiveSr);
  updateFFTPath();
  dcBlock.tempStart();
  for (auto& [name, vfo] : vfos) vfo->tempStart();
  ```
  **先 tempStop 所有下游 → 改参数 → tempStart**，靠 block 的 `tempStopDepth` 计数避免嵌套重配互相打架。
- `iq_frontend.cpp:204-242` `start()/stop()`：按 `inBuf → preproc → split → 所有 vfos → reshape → fftSink` 顺序。
- `iq_frontend.cpp:248-267` FFT handler：`volk window multiply → fftwf_execute → acquire GPU/UI buffer → volk power_spectrum → release`。FFT 在 **fftSink 自己的线程**里跑，通过 `acquireFFTBuffer/releaseFFTBuffer` 回调把 dB 数据交给 GUI 线程的 waterfall。
- `iq_frontend.cpp:269-309` `updateFFTPath()`：重设计 FFT 尺寸/窗时同样 `reshape.tempStop(); fftSink.tempStop(); ... tempStart();`。

### 1.6 DSP 线程模型基石 —— `dsp/stream.h` / `dsp/block.h`

- `dsp/stream.h:9`：每个流默认 **1 MSample 双缓冲**（`STREAM_BUFFER_SIZE 1000000`）。
- `dsp/stream.h:43-68` `swap()`（生产者侧）：
  ```cpp
  std::unique_lock<std::mutex> lck(swapMtx);
  swapCV.wait(lck, [this] { return (canSwap || writerStop); });
  if (writerStop) return false;
  dataSize = size;
  T* temp = writeBuf; writeBuf = readBuf; readBuf = temp;
  canSwap = false;
  ... rdyCV.notify_all();
  ```
  即：**生产者必须等消费者通过 `flush()` 把 `canSwap` 抬起来，才能写下一块**——天然背压。
- `dsp/stream.h:70-76` `read()`（消费者侧）：等 `dataReady || readerStop`，返回块长。
- `dsp/stream.h:78-92` `flush()`：消费者读完后清 `dataReady`、抬 `canSwap`、notify 生产者。
- `dsp/block.h:67-94`：
  ```cpp
  void workerLoop() { while (run() >= 0) {} }
  virtual void doStart() { workerThread = std::thread(&block::workerLoop, this); }
  virtual void doStop() {
      for (auto& in : inputs)  in->stopReader();
      for (auto& out : outputs) out->stopWriter();
      if (workerThread.joinable()) workerThread.join();
      ... clearReadStop/clearWriteStop ...
  }
  ```
  **每个 block = 一个 OS 线程**，`run()` 返回 <0 即退出循环。
- `dsp/block.h:46-62` `tempStop()/tempStart()` 是**引用计数**的：`tempStopDepth++`，只有首次真正 doStop，嵌套重配安全。

### 1.7 Splitter / Chain / HandlerSink / RxVFO / FrameBuffer

- `dsp/routing/splitter.h:46-61` Splitter 是 fan-out 节点，自己一个线程：
  ```cpp
  int count = _in->read();
  for (const auto& stream : streams) {
      memcpy(stream->writeBuf, _in->readBuf, count * sizeof(T));
      if (!stream->swap(count)) { _in->flush(); return -1; }
  }
  _in->flush();
  ```
  **拷贝分发**（不是零拷贝），每个下游流独立背压；任一下游 `swap()` 失败就整体退出。
- `dsp/routing/splitter.h:13-44` `bindStream/unbindStream`：运行中增删分支用 `tempStop/tempStart` 包住。
- `dsp/chain.h:62-90` `enableBlock()`：把 block 接进已串好的链——改前后邻居的 `setInput`，若链在跑就 `block->start()`。`disableBlock()` 对称：先 `block->stop()`，再把邻居跳过它。**这就是 iqFrontEnd 能运行时开关 decim/DCBlock/conjugate 的机制**。
- `dsp/sink/handler_sink.h:19-27` `Handler::run()`：读一块 → 调 C 回调 → flush。IQFrontEnd 的 FFT 分支就是挂在这个 sink 上。
- `dsp/buffer/frame_buffer.h:51-93` `SampleFrameBuffer` **有两个线程**：写线程把上游块拷进 32 深环形缓冲，读线程再从环里取出来交给下游流——这是用来吸收 USB/网络抖动的 jitter buffer（`bypass=true` 时直通）。
- `dsp/channel/rx_vfo.h:102-114` `RxVFO::run()`：read → `process()`（xlator NCO + rational resampler + 可选 FIR）→ flush 输入 → `out.swap(outCount)`。每个 VFO 自己一个线程。

### 1.8 core.cpp 生命周期 —— `core/src/core.cpp`

- `core.cpp:35-39`：`core` 命名空间下 4 个全局对象：`configManager / moduleManager / modComManager / args`。
- `core.cpp:61-72` `sdrpp_main()`：解析命令行 → server 模式在此分叉。
- `core.cpp:361`：`if (serverMode) return server::main();` —— **server 模式完全走另一条初始化路径**，不进 GUI。
- `core.cpp:376-403` GUI 模式：`backend::init(resDir)` → `SmGui::init` → 字体/图标/bandplan → `gui::mainWindow.init()` → `backend::renderLoop()`。
- `core.cpp:408-418` 关闭顺序：
  ```cpp
  for (auto& [name, mod] : core::moduleManager.modules) mod.end();
  backend::end();
  sigpath::iqFrontEnd.stop();
  core::configManager.disableAutoSave();
  core::configManager.save();
  ```
  **先让所有模块 end()（它们会自行注销 source/sink/vfo），再停 iqFrontEnd，最后存配置**。

### 1.9 模块加载 —— `module.{h,cpp}` + `gui/main_window.cpp`

- `module.cpp:34-45`：`dlopen(path, RTLD_LAZY|RTLD_LOCAL)` 后 `dlsym` 5 个固定符号：`_INFO_/_INIT_/_CREATE_INSTANCE_/_DELETE_INSTANCE_/_END_`。缺任何一个都拒载。
- `module.cpp:81-83`：所有符号拿到后立刻 `mod.init()`（模块级注册：向 sourceManager/sinkManager 注册 provider）。
- `module.cpp:86-106` `createInstance()`：工厂函数造出一个 `Instance`，检查 `maxInstances`。
- `module.h:43-50` `Instance` 虚接口：`postInit()/enable()/disable()/isEnabled()`。
- `gui/main_window.cpp:91-92`：**先把 iqFrontEnd 接到一个 `dummyStream` 上并 start**，再开始扫模块目录。
- `gui/main_window.cpp:101-115`：遍历 modulesDir，对每个 `.so` 调 `loadModule()`（只调 `_INIT_`，不造实例）。
- `gui/main_window.cpp:136-143`：再按 `config["moduleInstances"]` 逐个 `createInstance(name, mod)`，按 `enabled` 决定是否 `disableInstance`。
- `gui/main_window.cpp:227`：全部实例造完后 `doPostInitAll()`——模块在 `postInit()` 里才能安全地 `vfoManager.createVFO(...)`（此时所有兄弟模块都已注册）。
- `server.cpp:101,121`：server 模式只加载文件名含 `"source"` 的模块，无头不拉 radio/decoder。
- `server.cpp:128-149`：server 模式同样 createInstance → doPostInitAll → `sourceManager.selectSource(...)`。

---

## 2. 机制总结

### 2.1 数据流拓扑（GUI 模式）

```
[Source 模块线程]  --dsp::stream<complex_t>-->
   SourceManager.selectedHandler->stream
        |
        v
[SampleFrameBuffer (inBuf)]  ── 32-deep jitter ring, 2 threads, bypass 可关
        |
        v
[preproc chain]  decim(optional) -> dcBlock(optional) -> conjugate(optional)
        |                              (chain 运行时可旁路/接入)
        v
[Splitter]  ── 1 个线程，memcpy fan-out
        ├──> [Reshaper] -> [HandlerSink=FFT]  ──acquire/release buffer──> GUI waterfall
        ├──> [RxVFO "Radio" 自己的线程]  xlator→resamp→FIR ──stream──> radio 模块
        ├──> [RxVFO "VFO B" ...]
        └──> [任意 bindIQStream 的 tap，如 recorder/exporter]
                                          |
                                          v
                              [radio 模块解调线程] → stereo_t stream
                                          |
                                          v
                              [SinkManager::Stream splitter] → Volume → [AudioSink/NullSink/...]
```

### 2.2 线程模型（核心结论）

| 块 | 线程归属 | 阻塞方式 |
|---|---|---|
| Source 模块（rtl/file/…） | 模块自己起的采集线程 | `stream.swap()` 阻塞等下游 flush |
| SampleFrameBuffer 写线程 | block::doStart 起 | `_in->read()` 等上游 |
| SampleFrameBuffer 读线程 | 额外一个 `readWorkerThread` | `cnd.wait` 等环形缓冲非空 |
| preproc 里每个启用的 Processor | 各一个 `workerThread` | 上游 `read()` 阻塞 |
| Splitter | 自己一个线程 | 上游 `read()`；对每个下游 `swap()` 背压 |
| Reshaper / FFT HandlerSink | 各一个线程 | 上游 `read()`；handler 里跑 FFTW |
| 每个 RxVFO | 自己一个线程 | 上游 read → 处理 → `out.swap()` |
| Sink Stream 的 splitter/volume/sink | 各一个线程 | 上游 read |
| GUI renderLoop | 主线程 | 等 vsync，不碰 DSP 数据 |
| 控制面（tune/setBW/…） | 调用方线程（GUI 或 server） | 拿 block 的 `ctrlMtx`（recursive_mutex）做 tempStop/改参/tempStart |

**关键不变量**：
1. **数据面**是一长串独立 OS 线程，相邻线程之间用一个 `dsp::stream<T>`（双缓冲 + 两个 CV）解耦；生产者 `swap()` 被消费者 `flush()` 解除阻塞 = 显式背压。
2. **控制面**改任何参数（采样率/带宽/offset/开关某 block），都用 `block::tempStop/tempStart`（引用计数）包住，**不销毁/不新建线程**，只临时停一下 run() 循环再续上。
3. **fan-out 用 memcpy**（Splitter），不是零拷贝——换简单换可预测。
4. **VFO 是 iqFrontEnd 的子节点**，不是平级管理器；VFOManager 只是 GUI 包装 + 注册表。

### 2.3 流的启停与重配时序

- **启动**：`iqFrontEnd.start()` 自上游向下游依次 start：`inBuf → preproc.start()（链式 start 所有启用 block）→ split.start() → 每个 vfo.start() → reshape.start() → fftSink.start()`（`iq_frontend.cpp:204-222`）。上游先 ready，下游再开始 read。
- **停止**：同序反向（`iq_frontend.cpp:224-242`），每个 block::doStop 会 `stopReader/stopWriter` 并 join。
- **改采样率**：`tempStop` 所有受影响下游 → 改 `_sampleRate/effectiveSr` → 通知 dcBlock + 每个 vfo 重设 inSr → 重建 FFT plan → `tempStart`（`iq_frontend.cpp:76-99`）。
- **改 decimation 比**：额外先 `decim.tempStop()`，改比，再用 `preproc.setBlockEnabled(...)` 把 decim 块从链里接/摘（摘的时候 `split.setInput(decim 前一个块的 out)`）（`iq_frontend.cpp:105-122`）。
- **增删 VFO**：`addVFO` 即 new 一个 RxVFO + `split.bindStream(vfoIn)` + `vfo->start()`；`removeVFO` 反向。Splitter 自己 tempStop/tempStart 保护绑定表（`splitter.h:13-44`）。
- **换 sink provider**：`sink->stop()/delete/create()/start()`，上游 splitter 不动（`sink.cpp:218-240`）。

### 2.4 core 的生命周期编排（GUI 模式）

```
sdrpp_main
 ├─ args.parse
 ├─ config.load(defConfig) + 修复缺/多 key
 ├─ [server 模式分叉：server::main() 返回]
 ├─ backend::init(resDir)         // OS 窗口/GL
 ├─ SmGui::init / 字体 / 图标 / bandplan
 ├─ gui::mainWindow.init()
 │   ├─ iqFrontEnd.init(&dummyStream, ...)  // 先空跑起来
 │   ├─ iqFrontEnd.start()
 │   ├─ 扫 modulesDir，dlopen 每个 .so → mod._INIT_()（模块注册自己的 source/sink provider）
 │   ├─ 按 config["moduleInstances"] createInstance()（构造模块 Instance，此时它可以向 sourceManager 注册源）
 │   ├─ 各种 UI 菜单 init
 │   ├─ 从 config 恢复频率/VFO 偏移/音量/sink 选择
 │   └─ doPostInitAll()           // 所有兄弟就位后，模块才开始真正建 VFO/拉流
 ├─ backend::renderLoop()         // 主线程进渲染循环，DSP 线程在后台跑
 └─ 退出时：
     for mod in modules: mod.end()    // 模块注销 source/sink/vfo
     backend::end()
     iqFrontEnd.stop()
     config.save()
```

**server 模式**（`server.cpp:90-149`）：只 dlopen 文件名含 "source" 的模块 → createInstance → doPostInitAll → selectSource。没有 GUI、没有 waterfall、没有 FFT handler（或换成网络推流）。

---

## 3. MBDSDR 现状对照（真读 file:line）

> 路径前缀 `cpp/src/dsp/`。

### 3.1 整体线程模型

- `spectrum_engine.h:42`：`class SpectrumEngine : public QThread` —— **整个接收链就一个 run() 线程**。
- `spectrum_engine.cpp:763-811` run() 主循环：
  ```cpp
  while (running_.load()) {
      QMutexLocker lk(&sourceMutex_);          // 整个块持锁
      ...
      std::size_t got = source_->readIQ(iq);  // 阻塞读 ~25ms
      ...
      noiseBlanker_.process(iq);
      frontend_.process(iq);
      recorder_.writeIQ(iq);
      vfoManager_.process(iq, srEff, centerNow);   // 同步 fan-out 给所有 VFO
      ...
      audioSink_->write(out);                   // 同步写音频
      lk.unlock();
      if (!real) sleep_for(33ms);
  }
  ```
- `spectrum_engine.cpp:766`：**UI 线程的所有命令也抢同一把 `sourceMutex_`**（见 3.4 列举的几十处 `QMutexLocker lk(&sourceMutex_)`）。即 MBDSDR 用"一把大锁 + 单线程顺序流"代替了 SDR++ 的"N 个线程 + 流间背压"。

| 维度 | SDR++ | MBDSDR |
|---|---|---|
| 线程数 | 每个 DSP block 一个 OS 线程（10+） | 1 个 engine run() 线程 + UI 线程 |
| 块间解耦 | `dsp::stream` 双缓冲 + CV 背压 | `std::vector<std::complex<float>>` 按值传，无背压 |
| 重配 | `tempStop/tempStart` 引用计数，不停整个流水线 | 直接在 run() 块顶改（`needDemodReset_` 标志，`spectrum_engine.cpp:768`） |
| jitter buffer | SampleFrameBuffer 32 深环形（`frame_buffer.h:127`） | 无；靠 source 自己 readIQ 阻塞节奏 |
| fan-out | Splitter 线程 memcpy 到 N 个流 | `vfoManager_.process()` 同步 for 循环（`vfo_manager.cpp:231`） |

### 3.2 IQ 前端

- MBDSDR `iq_frontend.h:65-85`：`IQFrontend` 只是 3 个**有状态纯函数**的串联（DCBlocker → Highpass → IQBalance），`process(io)` 原地改 vector。
- `iq_frontend.cpp:41-50` DC blocker 是一阶 IIR（`y = x - x_prev + R*y_prev`，R=0.998）。
- `iq_frontend.cpp:97-151` IQ balance 用 2×2 协方差白化，且**特意做了功率守恒**（`k = sqrt((l1+l2)/2)`，注释 131-149 行解释为什么不能让空道噪声被抬到满幅）。
- **对照**：SDR++ 的 `IQFrontEnd` 是一个**带线程拓扑的容器**（inBuf/decim/dcBlock/split/FFT/VFO 全在里面）；MBDSDR 的同名类只是**校正算法本身**，拓扑在 SpectrumEngine::run() 里手写。

### 3.3 VFO 管理

- MBDSDR `vfo_manager.h:64-130` `VfoChannel`：每路 VFO 自带 `Channelizer + IDemod + AudioResampler`（`vfo_manager.h:73-75`）。
- `vfo_manager.cpp:228-263` `process()`：单线程 for 循环，每路先 `setVfoOffsetHz(freqHz - centerNow)` 连续调 NCO，再 `channelizer.process(iq)` → `demod->process()` → resampler 出 48k。
- `vfo_manager.h:15-17` 注释明说："all public methods are called from the engine run() thread under the engine's sourceMutex_. No internal locking is needed."
- **对照**：SDR++ 每个 RxVFO 是独立线程（`rx_vfo.h:102`），VFO 之间天然并行；MBDSDR 所有 VFO 在 engine 线程里串行跑，VFO 多了 CPU 是加法关系。MBDSDR 的优势是没有流间拷贝/背压开销、无锁；劣势是扩到 N 路重解调时没法吃多核。

### 3.4 Sink / 音频

- MBDSDR `iaudio_sink.h` + `memory_audio_sink.{h,cpp}`：`IAudioSink` 接口只有 `write/writeStereo/setVolume/setMuted/isAvailable/outputDevices`。
- `memory_audio_sink.cpp:29-40` `write()`：把 post-volume/post-mute 样本 append 到 `buffer_`，供测试断言。
- `spectrum_engine.cpp:1051` 之后：WFM 立体声写 `writeStereo(L,R)`，单声道写 `write(out)`。
- **对照**：SDR++ 的 SinkManager 是**可插拔 provider 注册表**（`sink.cpp:88-107` registerSinkProvider），运行中可换 Audio/Network/Null，每路立体声流自带 Volume + Splitter（还能 `bindStream` 给第三方模块 tap）。MBDSDR 只有一个 `IAudioSink* audioSink_`（`spectrum_engine.h:345`），不能运行中多路 tap、没有 provider 注册表。

### 3.5 模块/生命周期

- MBDSDR **没有 dlopen 插件体系**：SpectrumEngine 是编译期把所有 decoder（CW/ADSB/APT/RDS/stereo）作为成员对象组合进来的（`spectrum_engine.h:31-33, 367-372`）。
- 启停：`SpectrumEngine::run()` 由 `QThread::start()` 起，`shutdown()` 置 `running_=false`（`spectrum_engine.h:50, 375`）。
- **对照**：SDR++ 的 `dlopen + 5 符号 + postInit 两阶段` 是为了让第三方 .so 在运行时挂进来；MBDSDR 目前是单二进制，不需要。

### 3.6 Source 抽象

- MBDSDR `source.h:16-79` `ISource`：`start/stop/readIQ/setCenterFreq/setSampleRate/setGain` + 一组可选 front-end 旋钮（directSampling/offsetTuning/rtlAgc/biasTee/ppm，`source.h:38-48`，空默认实现）+ `capabilities()` + `availableGainsDb()`。
- 对照 SDR++ `SourceHandler` C 回调表（`source.h:13-22`）：MBDSDR 用 C++ 虚基类，语义等价但没有 `menuHandler/selectHandler/deselectHandler` 这种 GUI 回调——因为无头/控制层还没建。

---

## 4. 差距判定表

> 评级：**A=已实现且真实**；**B=有骨架但缺深度**；**C=未实现**。
> "云内可验证"指不依赖真机、offscreen/合成信号 + ctest 能确定性验。

| # | 能力 | SDR++ 出处 | MBDSDR 现状 | 评级 | 通用价值 | 云内可验证方案 |
|---|---|---|---|---|---|---|
| 1 | 每块一线程 + stream 双缓冲背压 | `dsp/block.h:67-94`, `dsp/stream.h:43-76` | 单 run() 线程 + vector 传 | **C**（架构选择，非缺陷） | 高：扩 N 路解调/录制 tap 时才需要多核 | 不急。若未来要加"第 2 路独立解调同时录音"，再干净室抽 `dsp::stream<T>` 双缓冲做单测（喂合成 IQ，验 swap/flush 背压与 stopReader 退出） |
| 2 | jitter buffer（32 深环形吸收 USB 抖动） | `dsp/buffer/frame_buffer.h:51-93` | 无 | **B**：rtl_tcp 走 socket 有 TCP 自带缓冲；本地 RTL 未串 SampleFrameBuffer | 中：本地 USB 源在高负载/GC 抖时可能 underrun | ctest：喂一个会"突刺"的假 source（readIQ 偶发延迟），下游 FFT/VFO 不应丢块；目前 MBDSDR 无此注入点 |
| 3 | 运行中 tempStop/tempStart 重配（引用计数） | `dsp/block.h:46-62`, `iq_frontend.cpp:76-99` | run() 块顶 `needDemodReset_` 标志同步重建（`spectrum_engine.cpp:768`） | **B**：功能在，但粒度是"整个 demod 重建"，不是"单块热改参数" | 高：无头控制层跑起来后，改 BW/增益不能停流 | 已有 rebuildDemod；补 ctest：在 run() 循环间改 BW，断言下一块 channelizer 系数已更新且无死锁/无 stale 样本 |
| 4 | Splitter fan-out（memcpy 到 N 路，独立背压） | `dsp/routing/splitter.h:46-61` | `vfoManager_.process()` 同步 for 循环（`vfo_manager.cpp:231`） | **A**（功能等价，单线程版） | 中 | 已被现有多 VFO ctest 覆盖（vfoAdd/vfoSetOffset） |
| 5 | FFT 分支独立线程 + acquire/release buffer 回调给 GUI | `iq_frontend.cpp:248-267` | `powerSpectrum_.process()` 在 run() 里同步算，emit spectrumReady（`spectrum_engine.cpp:864-870`） | **A**（功能等价） | 低：单线程算 FFT 已是瓶颈之外 | 已有频谱 ctest |
| 6 | SourceManager 注册表 + selectSource 切输入 | `source.cpp:42-60` | `std::unique_ptr<ISource> source_` + tryConnectRtl/disconnectSource/openOfflineFile 走同一 swap 路径 | **A** | 高 | 已有 connectRtlTcp/openOfflineFile 失败回退 test signal 的 ctest |
| 7 | SinkProvider 注册表 + 运行中换 sink + 多路 bindStream tap | `sink.cpp:88-107, 218-240` | 单一 `IAudioSink*`，setTestAudioSink 可换（`spectrum_engine.h:71`） | **B**：能换 sink，但没有 provider 注册表、没有第三方 tap 点 | 高：无头层要加"录制 tap/网络 sink/远程 audio stream"时需要 | 干净室抽 `AudioSinkProvider` 注册表 + `bindStream(name)`；ctest：注册两个 provider，运行中切换，断言 MemoryAudioSink 仍收到完整块 |
| 8 | VFO = iqFrontEnd 子节点，业务层 VFOManager 只做包装 | `vfo_manager.cpp:5-18` | VfoChannel 自带 channelizer/demod/resampler（`vfo_manager.h:73-75`），与 engine 平级 | **A**（设计不同但功能等价） | 中 | 已被多 VFO ctest 覆盖 |
| 9 | dlopen 插件 + 5 符号 + postInit 两阶段 | `module.cpp:34-83`, `main_window.cpp:227` | 编译期组合，无插件 | **C**（当前不需要） | 低：MBDSDR 是单二进制桌面端 | 不做。等真要第三方 decoder 再干净室抄 `_INFO_/_INIT_/_CREATE_INSTANCE_/_DELETE_INSTANCE_/_END_` 五符号契约（本身是通用 dlopen 模式，无 GPL 风险） |
| 10 | server 模式只加载 source 模块、selectSource 后退出初始化 | `server.cpp:101-149` | 无头控制层尚未建（Phase16 Wave2 目标） | **C** | **最高**（Wave2 P1） | 设计：命令 → SpectrumEngine slot（不经 QWidget），读写分离、写 gate；ctest 验：tune/setMode/setBW/setGain/startRecording 后状态可回读，未知命令诚实报错 |
| 11 | panadapter IF 调谐（硬件本振不动，只移 IF） | `source.cpp:24-27, 87-88` | `vfoSetOffset()` 已实现带 band-edge 回退（`spectrum_engine.h:149-157`） | **A** | 中 | 已有 vfoSetOffset ctest（断言 tuner 是否真重调） |
| 12 | 配置热恢复 stream/sink 选择/音量 | `sink.cpp:305-325` | recDir/recTemplate 等有缓存，但 sink provider 无注册表 | **B** | 中 | 随 #7 一起做 |
| 13 | 重采样率时级联 tempStop 所有 VFO 再 tempStart | `iq_frontend.cpp:76-99` | `vfoManager_.sourceRateChanged()` 全量 rebuild（`spectrum_engine.cpp:880-883`） | **A** | 中 | 已有 setSampleRate ctest |
| 14 | 优雅关闭：模块 end() → iqFrontEnd.stop() → config.save | `core.cpp:408-418` | `shutdown()` 置 running_=false，run() 退出；config 持久化在 UI 层 | **B**：DSP 线程能停，但"先停数据面再存配置"的编排没显式化 | 中 | ctest：shutdown 后 join engine 线程，断言无残留块、recorder 文件已 close（现有生命周期测试覆盖部分） |

---

## 5. 对 Wave2（无头控制层）的直接启示

1. **不需要现在就上多线程 block 拓扑**。MBDSDR 单 run() + `sourceMutex_` 对桌面端够用；无头控制层只要把"命令 → engine slot"这条路搭好，命令本身就是异步排队到 run() 块顶执行的。
2. **真正缺的是 #10（无头命令面）和 #7（sink/provider 注册表 + tap 点）**。Wave2 优先做 #10；#7 等需要"远程 audio stream / 网络录制 tap"时再做。
3. **tempStop 思路可借鉴但不必照搬**：MBDSDR 的 `needDemodReset_` 标志已经实现了"块顶原子重配"；无头命令只要把 `setBandwidth/setMode/setGain` 走 slot，自然串行化到 run() 块顶，不会出现 SDR++ 那种跨线程改 block 参数的竞争。
4. **Source 抽象已对齐**（`source.h:16-79`），无头命令直接调 `source_->setCenterFreq/setGain` 即可，不需要新接口。

---

## 6. 未读透清单（如实）

- `dsp/routing/stream_link.h`、`dsp/multirate/power_decimator.*`、`dsp/correction/dc_blocker.*`、`dsp/math/conjugate.*`：只在 iq_frontend.cpp 里看到用法，没逐行读实现。本笔记用到的是它们的**接口语义**（init/setRate/setRatio/setInput），足够支撑线程模型结论。
- `dsp/audio/volume.h`、`dsp/sink/null_sink.h`：只看了 sink.h 的成员声明，没读 .cpp。从 SinkManager 用法推断是标准 volume/null block。
- `gui/main_window.cpp` 240 行之后（waterfall/menus 细节）、`gui/widgets/waterfall.*`：本轮不需要，Wave B/C 再读。
- `server.cpp` 150 行之后（协议编解码、会话管理）：**Wave B 主战场**，本轮只读了 90-149 行的初始化段。
- `module_com.{h,cpp}`（模块间事件总线）：本轮未读，Wave B/C 需要。
- MBDSDR 侧：`channelizer.{h,cpp}`、`demod.*`、`audio_resampler.*`、`recorder.*`、`qt_audio_sink.cpp` 的具体实现没逐行读；本笔记只对照到它们在 run() 里的调用点。
- ctest 104 基线：本轮**未实际跑**（只确认了 spec 里写的 104/104）。Wave2 落地时再跑。
