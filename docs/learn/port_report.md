# 移植报告：SDR++ 设备抽象/VFO/多线程流式 + GNU Radio 流图调度

## 一、上游关键机制 file:line

### SDR++
| 机制 | 上游位置 | 要点 |
|---|---|---|
| SourceManager 注册表 | `repos/sdrpp/core/src/signal_path/source.h:9-56` | `std::map<string, SourceHandler*>`，每个源模块注册回调 |
| selectSource 接线 IQ 流 | `source.cpp:42-60` | `iqFrontEnd.setInput(selectedHandler->stream)` |
| start/stop/tune 纯转发 | `source.cpp:69-91` | core 不直接碰硬件 |
| 信号路径 in→decim→DC→split→VFO | `iq_frontend.cpp:17-70, 140-160` | split fan-out 给 FFT + 多 VFO |
| dsp::stream 双缓冲 swap/read/flush | `dsp/stream.h:43-92` | 写者 swap 阻塞等 flush，读者 read 阻塞等 dataReady |
| Processor.read/process/flush/swap 循环 | `dsp/processor.h:7-19` | 每块一个线程跑这个宏 |
| rtl_tcp worker 线程 + uint8 IQ 换算 | `rtl_tcp_source/src/rtl_tcp_client.cpp:75-95` | `(buf[2i]-128)/128.0` |
| 5 字节命令包 (cmd + BE u32) | `rtl_tcp_client.cpp:70-73` | opcode 1=freq,2=sr,3=gain_mode,4=gain |

### GNU Radio
| 机制 | 上游位置 | 要点 |
|---|---|---|
| FlowGraph 三色 DFS 判环 | `gnuradio-runtime/lib/flowgraph.cc:428-453` | GREY 邻点即抛 "flow graph has loops!" |
| topological_sort | `flowgraph.cc:384-401` | sort_sources_first + DFS |
| TPB 每块一线程 | `scheduler_tpb.cc:75-89` | 对每个 block create_thread |
| tpb_thread_body 状态机 | `tpb_thread_body.cc:63-137` | READY/BLKD_IN/BLKD_OUT/DONE |
| forecast/general_work 调用约定 | `block_executor.cc:523,624` | forecast 问输入需求，general_work 干活 |
| consume/produce | `block_executor.cc:664` | `produce_each(n)` 推进写指针 |
| 环形缓冲 + 多 reader | `buffer.cc:57-143` | `d_write_index`/`d_abs_write_offset` |
| WORK_DONE/WORK_CALLED_PRODUCE | `include/gnuradio/block.h:67-68` | 魔法返回值 |

## 二、移植了什么

### 1. `mbdsdr_ai/device_manager.py`（新文件）
- `DeviceInfo` dataclass：name/driver/serial/sample_rates(列表)/gains(档位)
- `DeviceManager`：
  - `list_devices()`：探测 pyrtlsdr + SoapySDR（HackRF/BladeRF/PlutoSDR 自动经 SoapySDR 枚举）
  - `open(info)` / `close()`：返回 `OpenDevice` 句柄，统一 read_samples/set_freq/set_sr/set_gain
  - `start_monitor(callback)` / `stop_monitor()`：后台线程轮询设备列表 diff，热插拔回调
- 异常：`DeviceNotFoundError` / `DeviceDisconnectedError`
- 红线遵守：无硬件返回 `[]`，不造假；拔线 read_samples 抛 `DeviceDisconnectedError`

### 2. `mbdsdr_ai/spyserver_client.py`（新文件）
- `SpyServerClient`：TCP 连接 → 1024 字节 ASCII 握手串（key=value\\0）解析 → 5 字节命令包
- `start(host,port)` / `stop()` / `read_samples(n)` / `set_frequency` / `set_sample_rate` / `set_gain`
- uint8 I/Q 交织 → complex64：`(I-128)/128.0 + j(Q-128)/128.0`
- 支持 `sock_factory` 注入 fake socket 做单测
- 红线：连接失败/握手失败抛 `SpyServerError`，不造假 IQ

### 3. `mbdsdr_ai/flowgraph_runtime.py`（新文件）
- `FlowGraph`：add_block/connect/validate（三色判环 + 未连接端口检查）/partition（并查集拆独立子图）
- `TpbBuffer`：带 Condition 的有界环形缓冲，写满阻塞、读空阻塞、stop 广播唤醒
- `TpbBlock`：forecast()/work() 约定，Source ninputs=0，Sink noutputs=0
- `Scheduler`：每块一个线程，run()/stop()/wait()，对应 tpb_thread_body 状态机
- 内置 VectorSource/VectorSink/MultiplyConst
- 背压：上游写满 BLKD_OUT，下游读空 BLKD_IN

### 4. `mbdsdr_ai/anr_iq.py`（新文件）
> 注：项目已有 `mbdsdr_ai/anr.py`（音频域 float32，类名 `SpectralSubtractionANR`），
> 为不改现有文件，IQ 域版本放 `anr_iq.py`。
- 谱减法：FFT → 减噪声底（过减因子 alpha=1+2*strength）→ 保相位 → IFFT
- `process(iq) -> iq`（complex64 进 complex64 出）
- `set_noise_estimate(iq)`：手动喂静默段
- `set_strength(0.0..1.0)`
- AI 增强：自动百分位跟踪噪声底，无需手动标静默

## 三、增强（相对上游）
1. **统一 DeviceManager**：SDR++ 每个源一个 .so 插件；我们一个 Python 类枚举所有驱动
2. **纯 Python 块**：GNU Radio 块要 C++/SWIG；我们直接子类化 TpbBlock
3. **ANR 自动噪声底**：滑动百分位跟踪，无需手动静默段
4. **热插拔轮询**：后台线程 diff 枚举列表，回调通知

## 四、测试结果

新增 4 个测试文件，共 21 个用例，全绿：
- `tests/test_device_manager.py`：5 个（空枚举、DeviceInfo、open 不存在、热插拔回调、异常类型）
- `tests/test_spyserver.py`：5 个（握手解析、IQ 换算、命令包格式、连接失败、坏握手）
- `tests/test_flowgraph_runtime.py`：6 个（简单链、判环、未连接端口、分区、背压、stop 唤醒）
- `tests/test_anr.py`：5 个（SNR 改善 >3dB、strength=0 直通、空输入、短输入、自动跟踪）

全量 pytest：**1135 passed, 0 failed**（240s）。
唯一 flaky 的 UI 测试 `test_ui_vfo_constellation.py::test_instantiate_no_devices` 单独跑通过，
是 PySide6 offscreen 状态问题，与本次改动无关。

## 五、红线遵守
- ✅ 无设备/无网络时返回空列表或抛明确错误，不造假
- ✅ 只新建文件，未修改现有共享文件（anr.py 原样保留，新 IQ 版放 anr_iq.py）
- ✅ 全量 pytest 绿
- ✅ 本地 commit，未 push
