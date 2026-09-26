# GNU Radio / GQRX 源码精读笔记（移植到 MBDSDR 的依据）

本文件记录移植块流式架构、标签系统、书签管理器、远程控制之前对上游源码的精读。
所有引用均为 `repos/` 下相对路径 + `file:line`。

---

## 1. GNU Radio 块流式架构

### 1.1 流标签格式 `tag_t`
`gnuradio-runtime/include/gnuradio/tags.h:28-55`

```cpp
struct tag_t {
    uint64_t offset = 0;          // 标签落在第几个样本上
    pmt::pmt_t key   = PMT_NIL;   // 符号：freq / rx_rate / gain / time ...
    pmt::pmt_t value = PMT_NIL;   // 任意 PMT 值
    pmt::pmt_t srcid = PMT_F;     // 产生标签的块 ID（追踪来源）
};
```
- 按 `offset` 排序（`tags.h:42-47` 的 `operator<` / `offset_compare`）。
- 相等比较包含 key/value/srcid/offset（`tags.h:50-54`）。
- 对应 MBDSDR：`StreamTag(offset, key, value, srcid)`，用普通 dict 承载 value。

### 1.2 环形缓冲 `buffer`
`gnuradio-runtime/lib/buffer.cc`

- 构造：`buffer.cc:57-85`，关键字段 `d_write_index`（写指针下标）、`d_abs_write_offset`（写指针绝对样本计数）、`d_readers[]`（多 reader）、`d_done`。
- 写指针：`buffer.cc:119` `write_pointer()` 返回 `&d_base[d_write_index*sizeof_item]`。
- 推进写指针：`buffer.cc:126-143` `update_write_pointer(nitems)`：
  ```cpp
  d_write_index = index_add(d_write_index, nitems);   // 模 bufsize
  d_abs_write_offset += nitems;                        // 绝对计数单调增
  ```
- 标签存储：`buffer.cc:162-166` `add_item_tag` 把 `(offset, tag)` 插进 `std::multimap<uint64_t, tag_t>`，按 offset 自然有序。
- 标签回收：`buffer.cc:168-205` `prune_tags(max_time)` 从头删掉 `offset + max_reader_delay + bufsize < max_time` 的旧标签（map 有序，遇到不满足即 break）。

### 1.3 多 reader `buffer_reader`
`gnuradio-runtime/include/gnuradio/buffer_reader.h`

- 每个 reader 独立维护自己的读位置（`buffer_reader.h:226-231`）：
  ```cpp
  unsigned int d_read_index;     // 环形内下标 [0, bufsize)
  uint64_t     d_abs_read_offset;// 自启动以来读过的样本总数（单调增）
  ```
- `items_available()`（`buffer_reader.h:85`）= writer 绝对写偏移 − reader 绝对读偏移。
- `read_pointer()`（`buffer_reader.h:103`）返回当前可读起点。
- `update_read_pointer(nitems)`（`buffer_reader.h:108`）推进 reader。
- `get_tags_in_range(start,end)`（`buffer_reader.h:145`）/ `get_first_tag_in_range`（`buffer_reader.h:166-191`）在 buffer 的 multimap 里按 offset 区间查询标签。
- 一个 buffer 可挂多个 reader（fan-out），每个 reader 互不影响读进度。

### 1.4 Block 基类
`gnuradio-runtime/lib/block.cc`

- 构造默认参数（`block.cc:34-62`）：
  - `d_output_multiple=1`（`block.cc:38`）——输出必须是该数的整数倍。
  - `d_relative_rate=1.0`（`block.cc:42`）——输入输出样本比。
  - `d_history=1`（`block.cc:44`）——需要回看的历史样本数。
  - `d_tag_propagation_policy=TPP_ALL_TO_ALL`（`block.cc:50`）。
- `forecast(noutput_items, ninput_items_required)`（`block.cc:93-98`）默认 1:1：
  ```cpp
  ninput_items_required[i] = noutput_items + history() - 1;
  ```
  即：要产出 N 个样本，需要 N+history−1 个输入（因为 history−1 个样本被“回看”消费掉）。
- `set_output_multiple`（`block.cc:106-113`）要求 ≥1。
- 标签 API：`add_item_tag(which_output, tag)`（`block.cc:216-218`）、`get_tags_in_range(...)`（`block.cc:221-235`）。
- 注意：真正的 `general_work` 在 C++ 子类里实现，基类只定义接口；调度器（block_executor）反复调用 `forecast`→检查输入可用→`general_work`→`produce/consume`。

### 1.5 图执行与拓扑排序
`gnuradio-runtime/lib/flowgraph.cc`

- `topological_sort(blocks)`（`flowgraph.cc:384-401`）：
  1. `sort_sources_first`（`flowgraph.cc:403-421`）先把 source（无上游的块，`source_p` 在 `:423-426`）排前面。
  2. 所有块置 WHITE（`flowgraph.cc:391-392`）。
  3. 对每个 WHITE 块做 DFS：`topological_dfs_visit`（`flowgraph.cc:428-453`）。
  4. 最后 `reverse(result)`（`flowgraph.cc:399`）——得到 source→sink 的执行顺序。
- DFS 三色判环（`flowgraph.cc:434-449`）：WHITE 未访问、GREY 在栈上（遇到 GREY = 有环，抛 "flow graph has loops!"）、BLACK 已完成。
- `flat_flowgraph.cc:39-50` `setup_connections`：先 `calc_used_blocks`，再 `allocate_block_detail`（建 buffer），再 `connect_block_inputs`（把下游输入接到上游 buffer 的 reader）。
- `scheduler.cc` 只是空壳基类（`scheduler.cc:19-25`），真正调度在 `scheduler_tpb.cc`（单线程线程块 TPB）。

**移植取舍**：MBDSDR 不做 TPB 的多线程复杂调度器，但保留：
- Block 的 `work(inputs, outputs)→nproduced` + `forecast()`；
- StreamBuffer 环形缓冲 + 多 reader；
- StreamTag 随样本流传播；
- FlowGraph 用 Kahn/DFS 拓扑排序 + 单线程执行（可选多线程 per-chain）。

---

## 2. GQRX 接收链拓扑

`src/receivers/nbrx.cpp`

构造函数 `nbrx::nbrx`（`nbrx.cpp:36-93`）把块串成一条固定链：

```
self(),0 ─► iq_resamp ─► nb ─► filter ─┬─► meter
                                        └─► sql ─► agc ─► demod ─► audio_rr0/rr1 ─► self() 左/右
```
关键 `connect` 语句（`nbrx.cpp:73-92`）：
- `:73` self→iq_resamp
- `:74` iq_resamp→nb（噪声抑制）
- `:75` nb→filter（信道滤波）
- `:76` filter→meter（电平表，fan-out）
- `:77` filter→sql（静噪）
- `:78` sql→agc
- `:79` agc→demod
- `:83-91` demod→audio_rr→self() 立体声左右声道

即 **input → resamp → filter → meter/sql → agc → demod → audio** 的信号链（与任务描述一致）。
demod 可热切换（`nbrx.cpp:192-299` `set_demod`）：disconnect 旧 demod、connect 新 demod（NONE/SSB/AM/AMSYNC/FM）。

---

## 3. GQRX 书签

`src/qtgui/bookmarks.cpp`

- 单例 `Bookmarks::Get()`（`bookmarks.cpp:37-51`），配置文件 `~/.config/gqrx/bookmarks.csv`（`setConfigDir` `:53-57`）。
- `BookmarkInfo` 字段（见 load 解析 `:116-131`）：`frequency(int64 Hz), name, modulation, bandwidth(int), tags[]`。
- 文件格式（GQRX 原生，分号分隔）：
  - 第一段（空行之前）：`TagName; color`（标签定义，`:94-104`）。
  - 第二段（空行之后）：`Frequency; Name; Modulation; Bandwidth; Tags`（逗号分隔多 tag，`:115-131`）。
  - 表头注释行以 `#` 开头（`:91-92`, `:112-113`）。
- `add`（`:59-64`）追加后 `stable_sort` 按频率排序并 `save()`。
- `load`（`:72-146`）/`save`（`:149-208`）。
- 频率区间查询 `getBookmarksInRange(low,high)`（`:210-232`）：用 `lower_bound`/`upper_bound` 在已排序数组上二分，再过滤 `IsActive()`。
- 标签 `findOrAddTag`（`:234-250`）、`removeTag`（`:252-287`，保留 "Untagged"）、`setTagChecked`（`:289-297`）。

**移植取舍**：MBDSDR 持久化到 JSON（`~/.mbdsdr/bookmarks.json`），但**兼容导入**任务指定的 GQRX CSV：
`Frequency,Name,Modulation,Bandwidth,Color`（逗号分隔，5 列）。导出也写同格式 CSV。不预存任何地区电台。

---

## 4. GQRX 远程控制

`src/applications/gqrx/remote_control.cpp`

- 默认端口 `7356`，默认只允许 `127.0.0.1`（`remote_control.cpp:31-32`）。
- `startRead()`（`:201-280`）：按行读，`split(" ")` 拆 `[cmd, arg...]`，分发：
  - `f` 读频率 / `F freq` 写频率（`:223-226`）
  - `m` 读模式 / `M mode [passband]` 写模式（`:227-230`）
  - `l ?` 列可调量 / `l name` 读 / `L name val` 写（`:231-234`）
  - `q`/`Q` 关闭连接（`:263-270`）
  - 未知命令 → `RPRT 1\n`（`:271-276`）
- 应答协议（hamlib rigctld 风格）：
  - 成功 → `RPRT 0\n`（`cmd_set_freq :619`，`cmd_set_mode :661`）
  - 失败 → `RPRT 1\n`（`:622`, `:647`）
  - 查询 → 直接返回值（`cmd_get_freq` 返回 `"%1\n"`，`:605-608`）。
- `cmd_set_freq`（`:611-623`）：`toDouble` 失败即 `RPRT 1`，不抛异常。
- 后台通过 Qt signal/slot 解耦，命令处理不阻塞 GUI。

**移植取舍**：MBDSDR 用 Python `socketserver.ThreadingTCPServer` 实现同样的 `cmd arg\n → RPRT 0/1\n` 协议；
命令按任务要求简化为 `f`（频率）、`m`（模式）、`g`（增益）、`q`（关闭）；无后端时返回 `RPRT 1` 但绝不崩溃。额外加 WebSocket 通道供 web 前端。

---

## 5. MBDSDR 增强（上游没有）

1. **FlowGraph AI 自动调参**：执行器每 N 个样本回调 `on_stats(stats)`，根据输入信号统计（RMS/峰值/占空比）自动给增益块/滤波块建议新参数（不强行写死，返回建议 + 可一键 apply）。
2. **书签 AI 信号识别**：`suggest_mode(freq_hz, bw_hz, power_profile)` 根据频率/带宽/功率包络启发式建议模式（FM/AM/USB/CW…）与带宽，不预存电台库。
3. **WebSocket 远程控制**：除 TCP 外，同一命令分发器挂一个 WS handler，便于 web 前端接入。
