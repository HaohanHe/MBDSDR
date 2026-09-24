# GNU Radio 核心 + gr-osmosdr 设计学习笔记

> 源码版本：gnuradio master (depth=1 clone 2026-09) / gr-osmosdr master
> 学习目标：读懂流图块模型、TPB 调度、采样率级联、设备抽象，对照 MBDSDR 找差距。

---

## 1. 流图块模型

### 1.1 block 基类接口

`gr::block` 是所有叶子处理块的抽象基类，继承自 `gr::basic_block`。

**核心虚函数签名：**

- `forecast(int noutput_items, gr_vector_int& ninput_items_required)` — `block.h:159`
  - 给定要产出 `noutput_items` 个输出样点，估算每个输入流需要多少样点。
  - 默认实现是 1:1 同步：`ninput_items_required[i] = noutput_items + history() - 1`（`block.cc:93-98`）。

- `general_work(int noutput_items, gr_vector_int& ninput_items, gr_vector_const_void_star& input_items, gr_vector_void_star& output_items)` — `block.h:184-187`
  - 实际做信号处理的函数。读输入、写输出。
  - 返回值：实际产出的样点数；特殊返回值 `WORK_CALLED_PRODUCE = -2`（各输出口产量不同时手动调 `produce()`）、`WORK_DONE = -1`（流结束）（`block.h:68`）。
  - 块必须在 `general_work` 内部调 `consume()` / `consume_each()` 告诉调度器消费了多少输入（`block.h:252-260`，实现在 `block.cc:174-179`）。

- `start()` / `stop()` — `block.h:197,202`：启动/关闭硬件驱动的钩子，在调度器开始/结束时被调用。

**同步块子类（sync_block / sync_decimator / sync_interpolator）** 把 `general_work` 简化成 `work(noutput_items, input_items, output_items)`，因为输入输出 1:1（或固定整数比），不需要手动管 consume。RTL-SDR 源就是 `gr::sync_block` 子类（`rtl_source_c.cc:81`）。

### 1.2 history 与延迟

- `history`：一个输出样点需要回看多少个输入样点（FIR 滤波器的 tap 数）。`block.h:98-99`。
- `declare_sample_delay()`：告诉调度器块的内部延迟，用于 tag 位置对齐。`block.h:120-126`。
- 调度器在创建 buffer_reader 时会把 `history-1` 作为初始偏移，保证第一帧就有足够的历史样点（`flat_flowgraph.cc:272`：`buffer_add_reader(src_buffer, grblock->history() - 1, ...)`）。

### 1.3 输入输出缓冲机制

GNU Radio 用**环形缓冲（ring buffer）+ 多读者指针**模型：

- 每个输出口对应一个 `buffer_sptr`（写者端，上游 block 持有）。
- 每个输入口对应一个 `buffer_reader_sptr`（读者端，下游 block 持有），指向上游的 buffer。
- buffer 是 `buffer_double_mapped`（同一块物理内存映射两次），实现零拷贝环形读写。

**Buffer 大小怎么定（`block::allocate_buffer`，`block.cc:462-520`）：**

1. 默认固定 buffer：`s_fixed_buffer_size = 32KB`（`block.cc:28-31`），按 itemsize 换算成样点数：`nitems = s_fixed_buffer_size * 2 / item_size`（`block.cc:472`）。乘 2 是因为 TPB 调度器只填一半，做双缓冲以提升并行度。
2. 下限保护：`nitems >= 2 * output_multiple()`（`block.cc:475-476`）。
3. 下游需求：`nitems = max(nitems, downstream_max_nitems)`（`block.cc:497`），其中 downstream_max_nitems 由 `flat_flowgraph::allocate_block_detail` 计算，考虑下游抽取率 × output_multiple + history（`flat_flowgraph.cc:108-112`）。
4. LCM 对齐：`downstream_lcm_nitems` 取下游所有固定速率块的输入样点需求的最小公倍数，保证调度边界对齐（`flat_flowgraph.cc:119-128`）。

### 1.4 Tag 传播

Tag 是带时间戳/元数据的样点标记（key/value 对，PMT 类型）。

**传播策略枚举**（`block.h:73-85`）：
- `TPP_ALL_TO_ALL = 1`：所有输入口的 tag 复制到所有输出口（默认，`block.cc:50`）。
- `TPP_ONE_TO_ONE = 2`：第 i 输入口的 tag 只到第 i 输出口。
- `TPP_DONT = 0` / `TPP_CUSTOM = 3`：调度器不自动传播，块自己处理。
- `TPP_TSB = 4`：tagged stream block 专用。

**传播实现**（`block_executor.cc:111-241`，`propagate_tags()`）：
- 每次 `general_work()` 返回后，调度器收集从 `start_nitems_read` 到当前 `nitems_read` 之间的所有输入 tag（`block_executor.cc:141`）。
- 按 relative_rate 做 offset 缩放：`new_tag.offset = llround(tag.offset * rrate)`（`block_executor.cc:162`）。
- 高精度模式用有理数乘法：`offset * mp_rrate + 1/2`（`block_executor.cc:177`），避免浮点误差累积。
- 然后写到所有输出 buffer 里。

---

## 2. 调度器：TPB (Thread-Per-Block)

### 2.1 工作循环

每个 block 跑在自己的线程里，线程体是 `tpb_thread_body`（`tpb_thread_body.cc:27-153`）。

主循环（`tpb_thread_body.cc:68-152`）：
1. 处理消息队列（`tpb_thread_body.cc:74-93`）。
2. 调 `d_exec.run_one_iteration()` 跑一次数据处理（`tpb_thread_body.cc:97`）。
3. 根据返回状态做下一步：
   - `READY`：有进展，通知上下游邻居（`tpb_thread_body.cc:116-118`）。
   - `READY_NO_OUTPUT`：只消费了没产出，只通知上游（`tpb_thread_body.cc:120-122`）。
   - `DONE`：结束，通知所有人后退出（`tpb_thread_body.cc:124-127`）。
   - `BLKD_IN`：等输入数据，条件变量等待（默认 250ms 超时，`tpb_thread_body.cc:131-138`）。
   - `BLKD_OUT`：等下游 buffer 空出空间，无条件变量等待（`tpb_thread_body.cc:141-147`）。

### 2.2 run_one_iteration 状态机

`block_executor::run_one_iteration()`（`block_executor.cc:261-722`）是核心调度逻辑，分三种情况：

**Source 块（无输入，`block_executor.cc:282-327`）：**
- 只看输出 buffer 有多少空间可写。
- 调 `min_available_space()` 找所有输出口的最小可写空间（`block_executor.cc:56-109`）。
- 限制在 `max_noutput_items` 以内（`block_executor.cc:295`）。

**Sink 块（无输出，`block_executor.cc:329-405`）：**
- 看每个输入 buffer 有多少可读数据。
- 按 `relative_rate` 估算可以"沉"多少输入：`noutput_items = max_items_avail * relative_rate`（`block_executor.cc:389`）。

**普通块（`block_executor.cc:407-714`）：**
1. 先收集每个输入口当前有多少样点可用（`block_executor.cc:418-427`）。
2. 再看输出口有多少空间可写（`block_executor.cc:431-470`）。
3. 调 `forecast(noutput_items, ninput_items_required)` 问块需要多少输入（`block_executor.cc:523`）。
4. 如果输入不够，**把 noutput_items 减半重试**（`block_executor.cc:550-553`），直到够了或者退到 output_multiple。
5. 够了就拿读指针、写指针，调 `general_work()`（`block_executor.cc:624-625`）。
6. work 完了做 tag 传播、推进读写指针、处理速率自更新。

### 2.3 背压（Backpressure）怎么处理

- **下游慢（BLKD_OUT）**：上游 block 写不进 buffer，等下游消费。
  - `min_available_space()` 返回 0 时进入 BLKD_OUT（`block_executor.cc:85-99`）。
  - 支持 `output_blocked_callback`（比如 file sink 的截断写），如果回调成功就重试（`block_executor.cc:307-320`）。
- **上游慢（BLKD_IN）**：下游 block 没数据可处理，等上游生产。
  - forecast 要的输入 > 实际可用时进入 BLKD_IN（`block_executor.cc:548-596`）。
  - 同样支持 `input_blocked_callback`。
  - 有超时（默认 250ms），防止死锁。

### 2.4 通知机制

block_detail 里有个 `d_tpb`（TPB 状态），带两个条件变量：
- `input_cond`：输入有新数据了，通知下游。
- `output_cond`：输出 buffer 有空了，通知上游。

`notify_neighbors()` / `notify_upstream()` 在 READY 时被调（`tpb_thread_body.cc:117,121`），唤醒等待的邻居线程。这是典型的**条件变量 + 环形缓冲**生产者-消费者模型。

---

## 3. 采样率级联 / 速率匹配

### 3.1 rational_resampler（有理数重采样）

**原理**：先整数插值 L 倍，再整数抽取 M 倍，中间跑一个抗混叠低通滤波器。整体速率比 = L/M。

**构造参数**（`rational_resampler_impl.cc:104-166`）：
- `interpolation`（L）、`decimation`（M）。
- 自动约分：`gcd(L, M)`，然后 `L /= gcd; M /= gcd`（`rational_resampler_impl.cc:127,147-148`）。
- 自动设计 Kaiser 窗低通滤波器（`design_resampler_filter()`，`rational_resampler_impl.cc:43-74`）。
- 设置 `set_relative_rate(L, M)`（`rational_resampler_impl.cc:156`）——告诉调度器输出/输入的速率比。

**多相滤波结构**：
- 把整个滤波器抽头按插值因子 L 分成 L 个子滤波器（`install_taps()`，`rational_resampler_impl.cc:187-208`）。
- 工作时（`general_work()`，`rational_resampler_impl.cc:230-261`）：
  - 计数器 `ctr` 从 0 开始，每次用第 `ctr % L` 个子滤波器算一个输出样点。
  - `ctr += M`，如果 `ctr >= L` 就减去 L、输入指针前进一步。
  - 这样就实现了 L 插 M 抽的有理数速率转换，而且每个输入样点只进一次 FIR。

**forecast**（`rational_resampler_impl.cc:217-227`）：
- 要产出 N 个输出，需要约 `N * M / L` 个输入样点（加 history 余量）。

### 3.2 pfb_arb_resampler（任意速率多相滤波器组重采样）

**原理**：N 路并行子滤波器（filter_size 路），用一个相位累加器（phase accumulator）在子滤波器之间连续插值，可以实现任意有理数/接近任意实数的速率比。

**构造**（`pfb_arb_resampler_ccc_impl.cc:29-44`）：
- `rate`：输出/输入速率比（float，可以是非整数）。
- `filter_size`：子滤波器路数（默认 32 左右）。
- `set_history(taps_per_filter())`：每个子滤波器的抽头数就是 history。
- `set_relative_rate(rate)`（`pfb_arb_resampler_ccc_impl.cc:39`）。
- 如果 rate >= 1，`output_multiple = max(rate, filter_size)`（`pfb_arb_resampler_ccc_impl.cc:40-43`）——强制每次输出是大块，提高吞吐。

**forecast**（`pfb_arb_resampler_ccc_impl.cc:46-57`）：
- 要产出 N 个输出，需要 `N / rate` 个输入样点。

**general_work**（`pfb_arb_resampler_ccc_impl.cc:118-139`）：
- `nitems = floor(noutput_items / relative_rate())` —— 算需要读多少输入。
- 调 `d_resamp.filter(out, in, nitems, nitems_read)` 做多相滤波 + 插值。
- `consume_each(nitems_read)` —— 实际消费了多少输入（可能和预估略有出入）。

**运行时调速率**：`set_rate(float rate)`（`pfb_arb_resampler_ccc_impl.cc:75-81`）——可以动态改重采样比，比如做时钟恢复时微调采样率。

### 3.3 多级重采样怎么级联

GNU Radio 里没有显式的"级联规划"——你就是把多个 resampler block 串在流图里：
```
source → low_rate_decim → channel_filter → pfb_arb_resamp → demod
```

每级自己管自己的 relative_rate，调度器通过 buffer 大小和 forecast 自动适配。设计多级抽取的原则（工程经验）：
1. 第一级大整数抽取（用 CIC 或半带滤波器，省算力）。
2. 中间级有理重采样把速率拉到目标附近。
3. 最后一级用 PFB 任意重采样做精细速率匹配（比如载波同步后的采样率跟踪）。

### 3.4 输出采样率怎么传播

- **块内声明**：每个 block 通过 `set_relative_rate(num, den)` 告诉调度器自己的速率比（`block.h:305`）。
- **调度器用它做什么**：
  - 估算 buffer 大小（`flat_flowgraph.cc:108`：`decimation = 1.0 / relative_rate()`，buffer 至少要 2×decimation×output_multiple + history）。
  - tag offset 缩放（`block_executor.cc:162,177`）。
  - sink 块估算 noutput_items（`block_executor.cc:389`：`max_items_avail * relative_rate`）。
- **端到端采样率**：用户自己在流图外面维护——上游源设了 sample_rate，下游每个 resampler 的 ratio 乘起来就是最终输出采样率。GNU Radio 本身不做自动端到端传播，因为很多场景下采样率是用户显式控制的。

---

## 4. gr-osmosdr 设备抽象

### 4.1 整体架构

`osmosdr::source` 是一个 `gr::hier_block2`（层次块），不是叶子 block。它对外暴露统一接口，对内根据参数选择具体硬件驱动块，然后用 hier_block2 的内部连线把硬件输出桥到对外端口。

**类继承**：`osmosdr::source`（抽象接口，`source.h:38`）→ `source_impl`（具体实现，`source_impl.cc:115`）。

**接口方法**（`source.h` 纯虚函数）：
- 采样率：`get_sample_rates()` / `set_sample_rate(double)` / `get_sample_rate()`（`source.h:75-89`）。
- 频率：`get_freq_range()` / `set_center_freq(double, chan)` / `get_center_freq()` / `set_freq_corr(ppm)`（`source.h:96-128`）。
- 增益：`get_gain_names()` / `get_gain_range()` / `set_gain_mode(bool)` / `set_gain(double, chan)` / `set_gain(double, name, chan)`（`source.h:135-203`）。
- 天线：`get_antennas()` / `set_antenna(string, chan)` / `get_antenna(chan)`（`source.h:230-246`）。
- IQ 校正：`set_dc_offset_mode()` / `set_iq_balance_mode()`（`source.h:266,290`）。
- 带宽：`set_bandwidth(double)` / `get_bandwidth_range()`（`source.h:307,321`）。
- 时钟/时间：`set_clock_source()` / `set_time_now()` 等（`source.h:355-418`）。

### 4.2 设备发现与加载

`source_impl` 构造函数（`source_impl.cc:115-433`）做了这些事：

1. 解析 args 字符串（key=value 格式，逗号分隔）。
2. 如果用户没指定设备类型，遍历所有编译进来的后端调 `get_devices()` 枚举硬件（`source_impl.cc:202-268`）。
3. 根据 args 里的 `rtl=...` / `hackrf=...` / `uhd=...` 等 key，选择对应的具体 source 块（`source_impl.cc:296-397`）。
   - RTL-SDR：`make_rtl_source_c(arg)`（`source_impl.cc:298`）。
   - HackRF：`make_hackrf_source_c(arg)`（`source_impl.cc:333`）。
   - BladeRF：`make_bladerf_source_c(arg)`（`source_impl.cc:340`）。
   - USRP/UHD：`make_uhd_source_c(arg)`（`source_impl.cc:312`）。
4. 拿到具体块的指针后，存在 `_devs` 数组里（`source_impl.cc:400`），然后 `connect(block, i, self(), channel++)` 把硬件输出桥到 hier_block2 的对外输出口（`source_impl.cc:416`）。
5. 如果编译了 iqbalance 库，还会在中间串一个 IQ 校正块（`source_impl.cc:403-414`）。

**多设备支持**：`_devs` 是数组，可以同时跑多个设备，每个设备贡献若干通道。`set_sample_rate` 会广播到所有设备（`source_impl.cc:478-479`）。

### 4.3 RTL-SDR 具体实现

`rtl_source_c` 继承 `gr::sync_block`（`rtl_source_c.cc:81`）—— 因为 RTL-SDR 是 1:1 同步采样（输入 0，输出 1，速率恒定）。

**关键参数**：
- `BUF_LEN = 16 * 32 * 512 = 262144` 字节（`rtl_source_c.cc:47`）—— 每块 buffer 大小，必须是 512 的倍数。
- `BUF_NUM = 15`（`rtl_source_c.cc:48`）—— 异步读的 buffer 个数。
- `BYTES_PER_SAMPLE = 2`（`rtl_source_c.cc:51`）—— RTL-SDR 输出 8-bit 无符号 IQ，每个样点 2 字节。

**异步读线程**：
- `start()` 时启一个线程跑 `rtlsdr_read_async()`（`rtl_source_c.cc:323`），这是 librtlsdr 的异步 API，USB 后台收数据，回调 `_rtlsdr_callback`。
- 回调把数据 memcpy 到环形队列 `_buf` 里（`rtl_source_c.cc:300-311`），满了就丢最老的（打个 "O" 表示 overrun）。
- `work()` 函数（`rtl_source_c.cc:333-374`）从队列里取数据，用查找表 `_lut` 把 8-bit 无符号值转成 float（`(i-127.4)/128.0`，`rtl_source_c.cc:179`），输出 gr_complex。

**采样率设置**：
- `set_sample_rate(double rate)` 直接调 `rtlsdr_set_sample_rate(_dev, (uint32_t)rate)`（`rtl_source_c.cc:441`）。
- 支持的采样率列表是硬编码的（`rtl_source_c.cc:421-429`）：250k / 1M / 1.024M / 1.8M / 1.92M / 2M / 2.048M / 2.4M / 2.56M Sps。

**增益**：`set_gain(double gain, size_t chan)` 自动分配到各 gain stage（`rtl_source_c.cc:593-610`），支持手动指定某一级。

---

## 5. 可迁移到 MBDSDR 的点

对照我们自己的 `mbdsdr_ai/` 代码，看看缺什么、能学什么。

### 5.1 设备抽象层（对照 sdr_backend.py）

**我们现状**（`sdr_backend.py`）：
- 有 `SDRDevice` dataclass（`sdr_backend.py:39`），字段是 device_type / sample_rate_range / max_gain。
- 有 `SDRBackend` 基类，`set_sample_rate` / `set_gain` 带异常回滚（`sdr_backend.py:133-176`）。
- 但目前看起来是单设备、单 backend 抽象，没有多后端自动枚举/选择。

**可迁移的点**：
1. **统一接口 + 后端注册表模式**：osmosdr 用 args 字符串解析 + 编译时宏开关（`#ifdef ENABLE_RTL` / `#ifdef ENABLE_HACKRF`）来决定编译进哪些后端。我们可以学这个思路——定义一个 `SDRSource` 抽象基类（sample_rate / center_freq / gain / antenna / read()），然后每个硬件后端一个子类，工厂函数根据 args 字符串选实现。
2. **设备枚举能力**：osmosdr 的 `get_devices()` 静态方法（`rtl_source_c.cc:376-410`）能列出所有连接的设备（带序列号、型号）。我们现在 `SDRDevice` 是静态配置的，缺自动发现——做频谱扫描产品时用户体验会差。
3. **采样率/增益范围元数据**：osmosdr 的 `get_sample_rates()` 返回 `meta_range_t`（支持离散值列表 + 连续区间混合），`get_gain_range()` 返回 `gain_range_t`。我们现在 `sample_rate_range` 只是个 `Tuple[float, float]`（`sdr_backend.py:45`），但 RTL-SDR 的采样率是**离散的固定值**（不是连续可调），这个信息没传上来，用户设个 1.5M Sps 就会出问题。
4. **IQ 校正流水线**：osmosdr 在 source 输出后面自动串了 `iqbalance::fix_cc` 块（`source_impl.cc:404-407`）。我们的处理链里目前没看到 DC offset / IQ imbalance 校正模块——实际设备（尤其是便宜的 RTL-SDR）IQ 不平衡很严重，直接做解调效果会差。
5. **多通道同步**：osmosdr 的 channel 是按设备通道累加的（`source_impl.cc:402-418`），未来如果做多通道 MIMO 或者多设备同步接收，这个架构要提前留好。

### 5.2 重采样（对照 dsp.py）

**我们现状**（`dsp.py`）：
- `decimate(x, factor)` 就是简单的整数抽取 `x[::factor]`（`dsp.py:177`）——**没有抗混叠滤波**！
- 音频解调里用了 `scipy.signal.resample_poly` 做有理重采样（`dsp.py:308,377`），这个是对的。
- 没有 PFB 任意重采样，没有动态速率调整。

**可迁移的点**：
1. **整数抽取必须加抗混叠低通**：现在的 `decimate()` 直接 `x[::factor]`，会混叠。至少应该学 GNU Radio 的 rational_resampler 思路——先低通再抽取，或者直接用 `scipy.signal.decimate`（内置抗混叠）。这是最紧急要补的。
2. **多相滤波结构**：GNU Radio 的 rational_resampler 把滤波器抽头按插值因子分成 N 个子滤波器，每个输入样点只跑一个子滤波，算力是 O(1) 每输出样点而不是 O(taps)。我们现在用 `resample_poly` 其实已经是这个结构了，但要理解为什么这么设计。
3. **PFB 任意重采样做时钟同步**：如果以后做数字解调（比如 ADS-B、FSK），需要符号同步，PFB arb resampler 是标准工具。我们目前没有这个模块，可以预留在 dsp.py 里。
4. **速率比的显式传递**：GNU Radio 每个 block 都有 `relative_rate`，调度器靠它算 buffer 大小和 tag 位置。我们现在是流式 numpy 数组处理，没有显式的"速率级联"概念——做实时系统时要注意每一级的采样率要显式记下来，不然 tag / 时间戳对不上。

### 5.3 流图 / 调度（对照整体架构）

**我们现状**：目前是脚本式处理——拿一块 IQ，跑一串函数，出结果。没有流图概念。

**可迁移的点**：
1. **背压与流控**：GNU Radio 的 TPB 调度器天然处理了"上游太快下游跟不上"的问题（环形 buffer + 条件变量）。我们现在如果做实时流式接收，需要自己管：USB 读线程 → 环形队列 → 处理线程 → 输出队列。RTL-SDR 的实现就是这个模式（`rtl_source_c.cc:323` 异步读线程 + `_buf` 环形队列 + `work()` 消费），可以直接参考。
2. **大块处理 vs 小块处理**：GNU Radio 每次 work 调用处理几百到几千个样点，批处理效率高。我们现在是一次性处理整个 buffer，如果做实时流，要切成块、控制每块大小在合理范围（不要太大导致延迟，不要太小导致开销）。
3. **Tag / 时间戳传播**：GNU Radio 的 tag 系统是带 offset 偏移的，经过重采样会自动按速率比缩放。我们现在如果要在数据流里打时间戳或者频率校正标记，需要自己维护 offset 到时间的映射——经过重采样后这个映射会变，可以学 GNU Radio 的 `relative_rate` 缩放方法。

### 5.4 频谱分析（对照 signal_spectrum.py）

**我们现状**（`signal_spectrum.py`）：Welch 平均 PSD + Peak Hold + 峰列表，纯 numpy 实现，已经做得不错了。

**可迁移的点**：
1. **信号检测后的带宽估计 → 触发重采样**：GNU Radio 流图里 resampler 是静态连好的。但如果我们做 AI 驱动的频谱分析——扫到一个信号后，自动把中心频率调过去、把采样率降到刚好覆盖这个信号带宽——那就是动态重构流图。osmosdr 的 `set_center_freq` / `set_sample_rate` 都是运行时可调用的，我们的 SDRBackend 已经支持这个了（`sdr_backend.py:133, set_sample_rate`），但 dsp 处理链的参数目前是写死的，缺动态重配置能力。
2. **流式 PSD**：现在 `analyze_iq_spectrum` 是一次性处理整段。如果做实时频谱，需要改成滑动窗口流式输出——这个模式和 GNU Radio 的 block 模型很像，每次处理 N 个样点输出一帧 PSD。

---

## 关键文件索引

| 主题 | 文件 | 关键行 |
|---|---|---|
| block 基类接口 | gnuradio-runtime/include/gnuradio/block.h | 159, 184, 252, 305 |
| forecast 默认实现 | gnuradio-runtime/lib/block.cc | 93 |
| buffer 分配 | gnuradio-runtime/lib/block.cc | 462-520 |
| TPB 线程体 | gnuradio-runtime/lib/tpb_thread_body.cc | 68-152 |
| 调度状态机 | gnuradio-runtime/lib/block_executor.cc | 261-722 |
| tag 传播 | gnuradio-runtime/lib/block_executor.cc | 111-241 |
| 流图 flatten + buffer 规划 | gnuradio-runtime/lib/flat_flowgraph.cc | 69-158, 160-276 |
| 有理重采样 | gr-filter/lib/rational_resampler_impl.cc | 104-166, 217-261 |
| PFB 任意重采样 | gr-filter/lib/pfb_arb_resampler_ccc_impl.cc | 29-44, 118-139 |
| osmosdr 统一接口 | gr-osmosdr/include/osmosdr/source.h | 38-419 |
| osmosdr 后端选择 | gr-osmosdr/lib/source_impl.cc | 271-422 |
| RTL-SDR 具体实现 | gr-osmosdr/lib/rtl/rtl_source_c.cc | 80-185, 333-374, 438-453 |
