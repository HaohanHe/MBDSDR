# SDR++ 设备抽象/VFO/多线程流式 + GNU Radio 流图调度 深学笔记

> 本笔记为移植 `mbdsdr_ai/device_manager.py` / `spyserver_client.py` /
> `flowgraph_runtime.py` / `anr.py` 提供上游 file:line 依据。
> 所有路径相对仓库根 `repos/sdrpp/` 与 `repos/gnuradio/`。

---

## 一、SDR++ 设备抽象与多线程流式

### 1.1 Source 基类 / SourceManager（设备注册-选择-启停）

`repos/sdrpp/core/src/signal_path/source.h:9-56`
- `SourceManager` 是设备源的注册表：`std::map<std::string, SourceHandler*> sources`
  （`source.h:48`）。
- `SourceHandler` 结构（`source.h:13-22`）把每个具体源模块的回调函数指针 + 上下文
  打包：
  - `stream`：`dsp::stream<dsp::complex_t>*`，设备读到的 IQ 流出口；
  - `selectHandler/deselectHandler`：菜单选中/取消；
  - `startHandler/stopHandler`：启停硬件读取线程；
  - `tuneHandler(double freq, void* ctx)`：远程改频率。
- 这是 SDR++ 的"插件即源"模型：每个 source_module 在 `_CREATE_INSTANCE_` 里
  `registerSource()`，core 不感知具体硬件。

`repos/sdrpp/core/src/signal_path/source.cpp:42-60` `selectSource()`
- 先 `deselectHandler` 当前源，再 `selectHandler` 新源；
- 关键一行 `sigpath::iqFrontEnd.setInput(selectedHandler->stream)`（source.cpp:57）：
  把选中源的 IQ 流挂到信号路径前端。**这就是"设备热切换"的接线点**。

`source.cpp:69-91` `start()/stop()/tune()`：纯转发到 `selectedHandler->*Handler`，
core 不直接碰硬件。

### 1.2 信号路径：input → decim → DC block → split → VFO → demod → audio

`repos/sdrpp/core/src/signal_path/iq_frontend.cpp:17-70` `IQFrontEnd::init()`
- `inBuf.init(in)`（iq_frontend.cpp:29）：输入缓冲，挂在 source 的 stream 上；
- `preproc` 链（iq_frontend.cpp:36-39）：`decim → dcBlock → conjugate`，每个块
  用 `preproc.addBlock(&blk, enabled)` 控制是否旁路；
- `split.init(preproc.out)`（iq_frontend.cpp:41）：IQ 分路器，把同一份基带
  fan-out 给 FFT 路径和所有 VFO；
- `addVFO()`（iq_frontend.cpp:140-160）：每个 VFO 自带一个 `dsp::stream<>`
  输入，`new RxVFO(vfoIn, effectiveSr, sampleRate, bandwidth, offset)`，
  然后 `bindIQStream(vfoIn)` 挂到 split 上。
- 数据流：`source.stream → inBuf → preproc → split → {VFO_i, FFT}`。

`iq_frontend.cpp:204-242` `start()/stop()`：按依赖顺序 start/stop 各块
（inBuf → preproc → split → vfos → reshape → fftSink）。

### 1.3 dsp::stream —— 双缓冲 swap/read/flush（核心同步原语）

`repos/sdrpp/core/src/dsp/stream.h:24-141`
- 两个缓冲 `writeBuf` / `readBuf`（stream.h:125-126），默认 1 MSample
  （`STREAM_BUFFER_SIZE 1000000`，stream.h:9）。
- 协议：
  1. 生产者把数据填进 `writeBuf`；
  2. 调 `swap(size)`（stream.h:43-68）：**阻塞等 `canSwap`**（即上一轮 reader
     已 flush），然后交换 writeBuf/readBuf，置 `dataReady=true`，`rdyCV.notify_all()`；
  3. 消费者 `read()`（stream.h:70-76）：**阻塞等 `dataReady`**，返回 dataSize；
  4. 消费者处理完 readBuf，调 `flush()`（stream.h:78-92）：清 `dataReady`，
     置 `canSwap=true`，`swapCV.notify_all()`。
- 这就是 SDR++ 的背压：生产者 swap 时若消费者还没 flush，就阻塞在 swapCV 上。
- 停止语义：`stopWriter()`（stream.h:94-100）置 `writerStop=true` 并唤醒
  swapCV，swap 返回 false 让生产者退出；`stopReader()`（stream.h:106-112）
  同理唤醒 readCV。

### 1.4 Processor 基类 —— 每块一个线程的 run() 循环

`repos/sdrpp/core/src/dsp/processor.h:7-38`
- `OVERRIDE_PROC_RUN` 宏（processor.h:7-19）展开成每个块的主循环：
  ```
  int count = _in->read();          // 阻塞等输入
  if (count < 0) return -1;         // reader stop
  process(count, _in->readBuf, out.writeBuf);   // 子类实现
  _in->flush();                     // 通知上游我吃完了
  if (!out.swap(count)) return -1;  // 阻塞等下游消费完
  ```
- 这就是 SDR 版的 TPB：**每个 Processor 子类在自己的线程里跑这个 read/process/flush/swap
  循环**，块间用 `dsp::stream<>` 双缓冲解耦。

### 1.5 rtl_tcp_source 模块（网络源范式）

`repos/sdrpp/source_modules/rtl_tcp_source/src/main.cpp:24-94`
- 模块构造时 `handler.stream = &stream`（main.cpp:92），`registerSource("RTL-TCP", &handler)`
  （main.cpp:93）。
- `start()`（main.cpp:127-158）：`rtltcp::connect(&stream, ip, port)` 建连，
  然后逐项 `setFrequency/setSampleRate/setPPM/setDirectSampling/setAGCMode/...`
  同步参数。
- `stop()`（main.cpp:160-166）：`client->close()`。
- `tune()`（main.cpp:168-175）：running 时转发 `setFrequency`。

`repos/sdrpp/source_modules/rtl_tcp_source/src/rtl_tcp_client.cpp:75-95` worker()
- 独立线程：`sock->recv(buffer, bufferSize*2, true)` 读原始 uint8 IQ；
- 换算 `stream->writeBuf[i].re = (buf[2i]-128)/128.0; .im = (buf[2i+1]-128)/128.0`
  （rtl_tcp_client.cpp:86-87）—— 与我们 `rtltcp_client.py:229-231` 一致；
- `stream->swap(scount)`（rtl_tcp_client.cpp:91）：阻塞等下游消费。
- 命令包 `sendCommand(uint8_t cmd, uint32_t param)`（rtl_tcp_client.cpp:70-73）：
  `struct Command{uint8_t cmd; uint32_t param}__attribute__((packed))`，param 大端
  （htonl）。opcode 表：1=freq, 2=sr, 3=gain_mode, 4=gain, 5=ppm, 8=AGC,
  9=direct_sampling, 10=offset_tuning, 13=gain_index, 14=bias_tee。

---

## 二、GNU Radio 流图与块调度

### 2.1 FlowGraph 拓扑排序 + 三色判环

`repos/gnuradio/gnuradio-runtime/lib/flowgraph.cc:384-401` `topological_sort()`
- 先 `sort_sources_first`（flowgraph.cc:403-421）把 source（无入边的块）排前面；
- DFS 三色标记：WHITE=未访，GREY=在栈，BLACK=完成；
- `topological_dfs_visit`（flowgraph.cc:428-453）：遇到 GREY 邻点即
  `throw std::runtime_error("flow graph has loops!")`（flowgraph.cc:441）。

### 2.2 TPB 调度器：每块一个线程

`repos/gnuradio/gnuradio-runtime/lib/scheduler_tpb.cc:49-91`
- `scheduler_tpb` 构造：`calc_used_blocks()` → `topological_sort()` →
  对每个 block `d_threads.create_thread(...)`（scheduler_tpb.cc:75-89）；
- `start_sync->wait()`（scheduler_tpb.cc:90）：所有块线程就绪屏障；
- `stop()`（scheduler_tpb.cc:95）：`d_threads.interrupt_all()`；
- `wait()`（scheduler_tpb.cc:97）：`join_all()`。

`repos/gnuradio/gnuradio-runtime/lib/tpb_thread_body.cc:63-137` 主循环
- 每块一个 `tpb_thread_body`，在自己线程里：
  1. `d_exec.run_one_iteration()`（tpb_thread_body.cc:103）执行一次 work；
  2. 根据返回 state 分支：
     - `READY`（tpb_thread_body.cc:115）：`notify_neighbors` 唤醒上下游；
     - `BLKD_IN`（tpb_thread_body.cc:122-130）：在 `input_cond` 上等输入；
     - `BLKD_OUT`（tpb_thread_body.cc:132-137）：在 `output_cond` 上等输出空间；
     - `DONE`（tpb_thread_body.cc:118-121）：结束线程。

### 2.3 block_executor::run_one_iteration —— work() 调用约定

`repos/gnuradio/gnuradio-runtime/lib/block_executor.cc:261-722`
- **Source 分支**（block_executor.cc:282-327）：无输入，只查输出缓冲空间
  （`min_available_space`，block_executor.cc:56-109），空间不足返回 `BLKD_OUT`。
- **Sink 分支**（block_executor.cc:329-405）：无输出，查输入可读样本数，
  不足返回 `BLKD_IN`。
- **普通块**（block_executor.cc:407-597）：
  1. 查每输入 `items_available()`（block_executor.cc:418-427）；
  2. 查输出缓冲 `space_available()`（block_executor.cc:431-470）；
  3. `m->forecast(noutput_items, d_ninput_items_required)`（block_executor.cc:523）
     —— 问块"要产 n 个输出，每输入要多少样本"；
  4. 若某输入 `required > available`（block_executor.cc:532-546），缩小 noutput_items
     重试，仍不够返回 `BLKD_IN`；
  5. 都够了：`d_input_items[i] = read_pointer()`（block_executor.cc:603），
     `d_output_items[i] = write_pointer()`（block_executor.cc:611）；
  6. 调 `m->general_work(noutput_items, d_ninput_items, d_input_items, d_output_items)`
     （block_executor.cc:624-625）；
  7. 返回值处理（block_executor.cc:658-665）：
     - `WORK_DONE (-1)` → done；
     - `WORK_CALLED_PRODUCE (-2)` → 块自己已调 produce；
     - 否则 `d->produce_each(n)` 推进写指针。
- `general_work` 内部块自己调 `consume(i, n)` / `consume_each(n)` 推进读指针。

### 2.4 buffer 环形缓冲

`repos/gnuradio/gnuradio-runtime/lib/buffer.cc:57-143`
- `d_write_index`（buffer.cc:71）、`d_abs_write_offset`（buffer.cc:72）；
- `update_write_pointer(nitems)`（buffer.cc:126-143）：环形下标回绕 + 绝对偏移推进；
- `space_available()` = bufsize - (abs_write - slowest_reader.abs_read)；
- 多 reader：`d_readers` vector，每个 reader 独立 `d_read_index` /
  `d_abs_read_offset`；
- 标签 `d_item_tags` 是 multimap<offset, tag_t>（buffer.cc:162-166），
  `prune_tags`（buffer.cc:168-205）回收过旧标签。

### 2.5 block.h work API 契约

`repos/gnuradio/gnuradio-runtime/include/gnuradio/block.h:67-68`
- 魔法返回值：`WORK_CALLED_PRODUCE = -2`、`WORK_DONE = -1`。
- `forecast(int noutput_items, gr_vector_int& ninput_items_required)`
  （block.h:159）：纯虚，子类覆写。
- `general_work(int noutput_items, gr_vector_int& ninput_items,
  gr_vector_const_void_star& input_items, gr_vector_void_star& output_items)`
  （block.h:184）：纯虚。块内部调 `consume(i, n)` / `produce(i, n)`。

---

## 三、移植映射表

| 上游机制 | 上游位置 | 我们的移植文件 |
|---|---|---|
| SourceManager.registerSource/select/start/stop | source.h:9-56, source.cpp:42-91 | `mbdsdr_ai/device_manager.py` DeviceManager |
| SourceHandler.stream 双缓冲 | stream.h:24-141 | 复用 `block_stream.py:StreamBuffer`（环形，带锁） |
| Processor.read/process/flush/swap 循环 | processor.h:7-19 | `flowgraph_runtime.py` 每块线程函数 |
| rtl_tcp worker 线程 + uint8 IQ 换算 | rtl_tcp_client.cpp:75-95 | `spyserver_client.py`（SpyServer 协议变体） |
| FlowGraph 三色 DFS 判环 | flowgraph.cc:428-453 | `flowgraph_runtime.py` FlowGraph.validate |
| TPB 每块一线程 | scheduler_tpb.cc:75-89, tpb_thread_body.cc:63-137 | `flowgraph_runtime.py` Scheduler |
| forecast/general_work/consume/produce | block_executor.cc:523,624,664, block.h:159,184 | `flowgraph_runtime.py` Block.work 约定 |
| 背压 BLKD_IN/BLKD_OUT | tpb_thread_body.cc:122-137 | `flowgraph_runtime.py` Condition 等待 |

## 四、我们的增强（相对上游）

1. **DeviceManager 统一所有驱动**：SDR++ 每个源是独立 .so 模块（rtl_tcp/hackrf/
   bladerf/plutosdr...），靠 SourceManager 注册表胶水；我们用一个 Python 类
   枚举 pyrtlsdr / SoapySDR / 各 params 模块，`list_devices()` 一次返回所有。
2. **FlowGraph 纯 Python 块**：GNU Radio 块要 C++ 或 SWIG；我们直接注册 Python
   callable / Block 子类，零编译。
3. **ANR AI 自动噪声底估计**：GNU Radio 谱减需要手动喂静默段；我们用滑动
   百分位跟踪噪声底（谱线下包络），无需手动标静默。
4. **热插拔轮询**：SDR++ 靠 udev 事件（C++）；我们用后台线程周期 diff
   `list_devices()`，回调通知上层。
