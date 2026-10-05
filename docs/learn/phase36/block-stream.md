<!-- SPDX-License-Identifier: MIT | Phase36 G1：深读 GNU Radio 块式流处理。
精读 repos/gnuradio/gnuradio-runtime/（block work 语义 / flowgraph / TPB 调度 / buffer）。
对照 cpp/src/dsp/ 流链。只学机制不抄代码，file:line 为证。 -->

# GNU Radio 块式流处理精读（block work / flowgraph / TPB / buffer）

> 范围：`repos/gnuradio/gnuradio-runtime/`。判定哪些机制我方已等价拥有、哪些值得借、
> 哪些是反例。我方流链 = `spectrum_engine.cpp` 单线程 pull 泵 + `VfoChannel` 向量级同步链。

---

## 1. 一句话结论

**GNU Radio 的流处理是「块 = 独立线程 + 共享环形 buffer + forecast 反推输入需求 +
noutput_items 二分回退协商」的数据flow 引擎；我方是「单线程 pull 泵 + 向量整块同步调用 +
块内尾状态」的极简链。两者解决同一问题的代价完全不同：GR 用线程+零拷贝环换「任意拓扑、
任意速率比、动态重配」；我方用「一个 pump 线程一次跑完一帧」换「零线程/零锁/零拷贝拷贝」。
我方当前规模下，GR 的调度抽象 90% 是 YAGNI；真正值得借的只有两条小机制（见 §6）。**

---

## 2. 机制总结

### 2.1 block work 契约（双层：general_work 通用 / work 简化）
- 基类 `block` 的纯虚是 `general_work(noutput_items, ninput_items, in, out)`，返回
  `WORK_CALLED_PRODUCE(-2)` / `WORK_DONE(-1)` 或实际产量 —— `include/gnuradio/block.h:68,184-187`。
  块必须显式 `consume/consume_each` 声明每个输入吃掉了多少，`produce_each(n)` 推进写指针
  —— `block.h:46-53`；实现转发到 `d_detail`：`lib/block.cc:174-184`。
- **forecast(noutput_items, ninput_items_required)** 是「给定要产 N 个输出，需要每个输入多少」
  的反推钩子 —— `block.h:159`；基类默认 1:1：`ninput = noutput + history - 1` —— `block.cc:93-98`。
- 派生层把通用契约特化成更简单的 `work()`：`sync_block`(1:1) 内部调 `work()` 再
  `consume_each(r)`，forecast=`nout+hist-1`（`lib/sync_block.cc:44-53`）；
  `sync_decimator` 消费 `r*decimation`（`sync_decimator.cc:50-53`）；
  `sync_interpolator` 消费 `r/interp`（`sync_interpolator.cc:45-53`）；
  `tagged_stream_block` 先读 length tag 得本帧确切输入数，不够 `return 0` 等下回合，跑完按帧
  `consume` 并回写新长度 tag（`tagged_stream_block.cc:170-193`）。
- `history`（滤波器历史抽头数）由调度器在 reader 上预填 0（`block.h:90-99`）；
  `set_output_multiple/set_alignment` 强制 noutput_items 对齐以利 SIMD（`block.cc:106-131`）。

### 2.2 noutput_items 协商循环（block_executor::run_one_iteration，核心）
一次迭代 = 一次 work 调用，状态机返回 `READY / READY_NO_OUTPUT / BLKD_IN / BLKD_OUT / DONE`
—— `lib/block_executor.h:51-56`。regular 路径（`block_executor.cc:407-714`）：
1. **读输入可用量**：每个 `buffer_reader->items_available()`，记 `max_items_avail` —— :418-427。
2. **算下游写空间**：`min_available_space()` 取所有输出 buffer 的 `space_available()` 最小值，
   `round_down(output_multiple)` 且不超过 `bufsize/2`（best_n）—— :56-109（:75,81,84）。
3. **定比块反推 + 对齐**：`fixed_rate()` 时用 `fixed_rate_ninput_to_noutput(max_items_avail)`
   把手里输入换算成可产量、尽量吃光（:473-487）；再按 output_multiple/alignment 回卷（:493-520）。
4. **forecast 校验 + 二分回退**：`m->forecast(noutput_items, ninput_items_required)`（:523）；
   若某输入 `required > available`（:533），**把 noutput_items 减半再 round_up 重试** ——
   :548-554（`noutput_items /= 2; goto try_again`）。这是「按可行性收缩工作量」的关键机制。
5. **仍不够**：上游已 `done` 则结束（:572）；所需 > `max_possible_items_available()`（缓冲装不下，
   如抽头太多）直接报错（:576-588）；否则返回 `BLKD_IN`（:596）。
6. **就绪→收尾**：取 `read_pointer()/write_pointer()`（零拷贝指针）调 `general_work()`（:601-625）；
   `produce_each(n)` 推进写指针（:663-665），`post_work_cleanup` 推进读指针，按 relative_rate
   做 tag 位移传播（:646-656）。
- 源块路径 noutput_items 只受输出空间/max 约束（:282-327）；汇块用 `max_items_avail*relative_rate`
  猜吞量（:329-405）。

### 2.3 TPB 调度（thread-per-block）
- **每个块一个线程**：used blocks 拓扑排序后逐个 `create_thread`，线程体 = `tpb_container`
  → `tpb_thread_body`（`lib/scheduler_tpb.cc:60-89`）；`barrier(blocks.size()+1)` 让所有块就绪后
  同步开跑（:70-71,90）。线程亲和/优先级可按块设（`tpb_thread_body.cc:54-62`）。
- **每线程主循环**（`tpb_thread_body.cc:68-152`）：先排空消息队列（:74-93），再 `run_one_iteration()`
  （:97），按返回态睡/醒：`READY`→`notify_neighbors`、`READY_NO_OUTPUT`→`notify_upstream`、
  `DONE`→通知消息邻居后退出（:117-127）；`BLKD_IN` 在 `input_cond` 上 wait（带超时轮询，:129-139）；
  `BLKD_OUT` 在 `output_cond` 上 wait（:141-147）。
- **唤醒 = 邻居置位标志**（`lib/tpb_detail.cc`）：`notify_downstream` 给每个输出 buffer 的每个
  reader 置「下游输入就绪」（:40-50）；`notify_upstream` 给每个输入 reader 的上游写方置「输出有
  空间」（:28-38）。即「块跑到阻塞就睡，谁让它不阻塞谁 signal」的事件驱动。
- scheduler 可插拔，TPB 只是注册表一个工厂（`lib/top_block_impl.cc:37`）。

### 2.4 buffer：双映射环形零拷贝
- **双映射 = 同一份物理页映射两次、首尾相接**：`pointer_to_second_copy() = d_base + d_size`
  （`lib/vmcircbuf.h:46-47`）。写指针到环尾时继续线性写就落进第二段、自动 alias 回环首——
  **热路径无取模**。指针直接进块：`write_pointer()=&d_base[d_write_index*sizeof_item]`
  （`lib/buffer.cc:119`），读指针同理（`buffer_reader.cc:118-122`），块直接读写共享内存、
  **块间无 memcpy**；索引仍按环回卷（仅整数加减，`buffer_double_mapped.h:65-75`）。
- **可用写空间 = bufsize - 最慢读者已存数据 - 1**：`space_available()=bufsize-most_data-1`
  （`lib/buffer_double_mapped.cc:137-169`，关键 :167）。`-1` 是「满/空不歧义」预留槽；
  `most_data=max over readers of items_available`（:145-150），即**扇出按最慢消费者节流**；
  读者侧 `items_available()=index_sub(write_idx,read_idx)`（`buffer_reader.cc:101-116`）。
- **单映射是退路**：双映射 mmap 失败时用普通单段内存，读指针近尾需连续大块时由
  `input_blocked_callback` 把数据 `memmove` 回环首腾地方（`buffer_single_mapped.cc:303-350`）。

### 2.5 flowgraph 物化（flat_flowgraph）
- `start()`→`setup_connections()`→`make_scheduler`（`lib/top_block_impl.cc:112,121`）。
- **每个输出端口的 buffer 大小按下游需求反推**（`allocate_block_detail`，
  `lib/flat_flowgraph.cc:69-158`）：遍历下游块，容量下限 `2*(decimation*output_multiple+history)`
  （:105-112，至少 2 个消费周期+历史才不饿死）；尺寸取下游 `relative_rate_d` 与 fixed-rate 步进的
  **LCM**（:119-129），使定比块索引对齐。
- **扇出 = 一个写 buffer + 多个 reader**：下游不新建 buffer，而是
  `buffer_add_reader(上游输出buffer, history-1, ...)`（:203-274，尤其 :270-274），`history-1`
  为启动零预填。buffer 类型可按端口协商（HOST/DEVICE 直通，:181-263）；消息端口（PMT）与流端口
  分离，另走 `message_port_sub`（:58-66）。

---

## 3. 关键算法 / 机制索引（file:line 速查）

| 机制 | 位置 |
|---|---|
| work 契约 + 魔术返回值（GENERAL_PRODUCE/DONE） | `include/gnuradio/block.h:68,184-187` |
| forecast 基类默认 1:1 / 派生层包 work | `lib/block.cc:93-98`；`sync_block.cc:44-53`；`sync_decimator.cc:50-53` |
| TSB 按长度 tag 取数 | `lib/tagged_stream_block.cc:170-193` |
| 迭代状态机五态 | `lib/block_executor.h:51-56` |
| min_available_space（含 bufsize/2 best_n） | `lib/block_executor.cc:56-109`（:81,84） |
| noutput_items 二分回退 / forecast 校验 | `lib/block_executor.cc:548-554`；:523,533 |
| general_work 调用点 / produce_each | `lib/block_executor.cc:601-625`；:663-665 |
| 一块一线程 + 启动屏障 | `lib/scheduler_tpb.cc:60-90` |
| 主循环 condvar 等待 | `lib/tpb_thread_body.cc:68-152` |
| notify_downstream / upstream 置位 | `lib/tpb_detail.cc:28-56` |
| 双映射环 second_copy | `lib/vmcircbuf.h:46-47` |
| 零拷贝读写指针 | `lib/buffer.cc:119`；`buffer_reader.cc:118-122` |
| space_available = 最慢读者 - 1 | `lib/buffer_double_mapped.cc:137-169`（:167） |
| 单映射 memmove 退路 | `lib/buffer_single_mapped.cc:303-350` |
| buffer 尺寸反推（2x 量子 + LCM） | `lib/flat_flowgraph.cc:105-129` |
| 扇出 = 一 buffer 多 reader | `lib/flat_flowgraph.cc:203-274`（:270-274） |
| start 序列 / scheduler 工厂 | `lib/top_block_impl.cc:37,112,121` |

---

## 4. 可借鉴点（机制，非代码）

1. **forecast 反推 + 二分回退**（§2.2）：先估「产 N 输出需多少输入」，不够就把 N 砍半重试，
   比「固定块大小硬等一整块」更省延迟、避免死等——值得记的「按需收缩工作量」心智模型。
2. **环缓冲 sizing**（§2.5）：下限 = 下游消费量子 2 倍 + 历史，取 LCM 对齐定比块。缓冲不是越大越好。
3. **扇出一 buffer 多 reader + 按最慢读者节流**（§2.4/2.5）：宽带 IQ 只存一份，各 VFO 持读指针，
   写空间由最慢消费者定。我方 vfo_manager 正是此模型（见 §5）。
4. **history/尾状态由块自管**：滤波器把 T-1 历史留内部、启动预填零——与我方
   `FirLowpass::delayLine_` / `Channelizer::tail_` 同思路，已落地。

---

## 5. 我方差距判定（对照 cpp/src/dsp/ 流链）

我方流链事实（真读）：`spectrum_engine.cpp` 的 `run()` 是**单线程 pull 泵**——
`source_->readIQ(iq)` 拉一帧（≈25ms，:1055）→ 就地 `noiseBlanker_/frontend_`（:1087-1088）
→ 可选前端抽取 `frontendDecim_.process`（:1114）→ FFT 谱（:1130）→ `vfoManager_.process(iq,
srEff, centerNow)` **同步**扇出到各 VFO（:1150），每 VFO = `Channelizer + unique_ptr<IDemod> +
AudioResampler`（`vfo_manager.h:77-79`）。块间传 `std::vector` 整块、尾状态在块内
（`channelizer.h:126 tail_`、`demod.h` 的 FirLowpass/NuttallLpf delayLine_）。`IQBuffer`
（`iq_buffer.h`）只是「最近 n 点」覆盖式快照环，**不是块间零拷贝环**。

### 5.1 一块一线程 + condvar 唤醒 —— YAGNI，明确不补
- 我方只有 1 个真实宽带源 + N 个**同一线程内**串行跑的 VFO，没有跨块并发，也就没有「生产者
  阻塞、消费者唤醒」的跨线程同步需求。引入 TPB = 每块一线程 + mutex/condvar + 跨线程环，纯粹把
  一次函数调用拆成 N 次上下文切换。**当前规模（单源、桌面端、8GB cgroup OOM 约束）下是负优化**；
  GR 要它是为了任意动态拓扑/多速率/设备直通/在线重配，而我方拓扑启动即定型、模式切换走 rebuild。不补。

### 5.2 块间零拷贝双映射环 —— YAGNI，理由充分
- 我方块间传 `std::vector<complex<float>>`，每帧一次拷贝，但帧仅 ~25ms、单 VFO 链同线程，拷贝
  在 L2 内，远小于「跨线程共享环 + 预留槽 + 双映射 mmap」的复杂度。vmcircbuf 价值在跨线程少锁
  共享；同线程换来的是 mmap shm/tmpfile 平台脆弱性（GR 要 4 种 factory 兜底，`vmcircbuf.cc:86-90`）。不补。

### 5.3 forecast 反推 + noutput_items 二分回退 —— 小借鉴候选（优先级低）
- 我方 `readIQ` 帧大小由源决定（~25ms 对齐 D，`spectrum_engine.cpp:1050-1053`），下游整帧吃掉，
  没有「输入不够先少产点」的协商——整帧同步 pull，上游给多少下游算多少，本就不缺数据。
- 唯一受益点：若未来做**异步源**（网络/SpyServer 偶发短帧），现在是「短帧照跑、下一块再补」
  （:1077），滤波器 tail 已跨帧连续、等价 GR history，够用。判定：**不立项**；记为「引入异步/变长源
  再回看 §2.2」。

### 5.4 缓冲 sizing 公式（2×消费量子 + LCM）—— 部分已内建，列观察项
- 我方 Channelizer/decimator 块大小由 `readIQ` 帧与抽取比自然导出，无显式「至少 2 个消费周期」
  下限；当前帧远大于抽取量子、不暴露问题。判定：**仅当出现「块大小≈抽取量子」的极端低延迟配置、
  导致滤波器历史截断/饿死时，才按 `2*(decim*mult+history)` 加下限**。现在加 = 过度设计。

### 5.5 history/尾状态 + 启动预填零 —— 我方已等价拥有，不补
- `demod.h` FirLowpass/NuttallLpf、`channelizer.h:126 tail_`、`agc` 都是「块内留 T-1 状态、
  reset() 清零」，与 GR history 同构。不补。

---

## 6. 反例核查（这些不要抄）

1. **goto 状态机**：`block_executor.cc` 用 `blkd_in_try_again / blkd_out_try_again /
   try_again / were_done` 四处 goto 编织控制流（:292,319,404,431,465,553,568）。能跑，但
   可读性/可测性差。→ 我方继续用普通循环 + 显式状态枚举，不引入 goto 编织。
2. **一块一线程的线程爆炸**：大扇出图（一源 → 几十 VFO）下 TPB 会起几十个线程，上下文切换
   与 condvar 开销反噬吞吐——这是 GR 公认的已知短板，也正是我方坚持单 pump 的理由。
3. **环形 `-1` 预留槽**：`space_available = bufsize - most_data - 1`（:167）永远浪费 1 个槽
   换满/空判别。我方「最近 n 点」覆盖环（`iq_buffer.h`）用显式 `count_` 判别满/空，不浪费
   槽，更简单。
4. **双映射 mmap 的平台脆弱性**：GR 要 createfilemapping / sysv_shm / mmap_shm / mmap_tmpfile
   四种 factory 逐个试（`vmcircbuf.cc:86-90`），就为了在不同 OS/权限下凑出「两次映射同一份
   物理页」。→ 这种为零拷贝引入的平台适配面，我方不碰。
5. **`d_detail` 隐式转发的双重状态**：块调 `consume` 既写 `d_consumed` 又立即推进读指针
   （`block_detail.cc:94-112`），产量与消费在两个方法里分别记账，新人容易漏调。→ 我方块
   接口保持单一返回值（`process()->vector`），状态收敛在对象内。

---

## 7. 收口

- **学什么**：forecast 反推 + noutput_items 二分回退（§2.2 step5，记为异步源候选）；
  缓冲 sizing = 2×消费量子 + 历史 + LCM（§2.5）；扇出一 buffer 多 reader + 最慢读者节流。
- **不学什么**：TPB 一块一线程、双映射 vmcircbuf 零拷贝环、goto 状态机——在我方单源同步
  pull 架构下全是负优化或平台负担。
- **我方现状**：VFO 扇出模型、块内 history/尾状态、启动清零，均已与 GR 等价落地；
  唯一真增量是 §5.3/§5.4 两条「条件触发」的观察项，均不立项。整体判定：**以 YAGNI 为主，
  本轮不引入调度/buffer 抽象**；机制笔记留档，待异步源或极端低延迟需求出现时再回看。
